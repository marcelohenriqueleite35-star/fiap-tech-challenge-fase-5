"""Thompson Sampling Beta-Bernoulli e políticas de comparação.

O estado do bandit (sucessos/falhas por contexto e braço) fica num "ArmStore".
O contexto é o segmento do cliente combinado com o mês da campanha (ex.: "senior|may"):
- PostgresArmStore: lê e atualiza direto na tabela bandit_state (usado pela API).
- InMemoryArmStore: mesmo contrato em memória (usado na simulação, que roda
  dezenas de milhares de passos e depois publica o estado final no Postgres).
"""

from collections import defaultdict
from typing import Protocol

import numpy as np
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.config import ARM_NAMES, CONTEXTS
from src.database import BanditState, utcnow

Counts = dict[str, tuple[int, int]]  # braço -> (sucessos, falhas)


def posterior_mean(successes: int, failures: int) -> float:
    """Conversão esperada do braço com prior Beta(1, 1): (S+1) / (S+F+2)."""
    return (successes + 1) / (successes + failures + 2)


class ArmStore(Protocol):
    def get_counts(self, context: str) -> Counts: ...
    def increment(self, context: str, arm: str, reward: int) -> None: ...


class InMemoryArmStore:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])

    def get_counts(self, context: str) -> Counts:
        return {arm: tuple(self._counts[(context, arm)]) for arm in ARM_NAMES}

    def increment(self, context: str, arm: str, reward: int) -> None:
        self._counts[(context, arm)][0 if reward else 1] += 1

    def snapshot(self) -> dict[tuple[str, str], tuple[int, int]]:
        return {key: tuple(value) for key, value in self._counts.items()}


class PostgresArmStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    # A coluna `segment` da tabela guarda a chave do contexto ("segmento|mês").
    def get_counts(self, context: str) -> Counts:
        with self._session_factory() as session:
            rows = session.execute(select(BanditState).where(BanditState.segment == context)).scalars()
            found = {row.arm: (row.successes, row.failures) for row in rows}
        return {arm: found.get(arm, (0, 0)) for arm in ARM_NAMES}

    def increment(self, context: str, arm: str, reward: int, session: Session | None = None) -> None:
        # Incremento atômico no banco: seguro com várias réplicas da API em paralelo.
        stmt = (
            update(BanditState)
            .where(BanditState.segment == context, BanditState.arm == arm)
            .values(
                successes=BanditState.successes + int(reward == 1),
                failures=BanditState.failures + int(reward == 0),
                updated_at=utcnow(),
            )
        )
        if session is not None:
            session.execute(stmt)
            return
        with self._session_factory.begin() as own_session:
            own_session.execute(stmt)

    def replace_all(self, counts: dict[tuple[str, str], tuple[int, int]]) -> None:
        """Publica o estado treinado na simulação: reescreve a grade inteira (contexto x braço),
        com zero nos contextos sem histórico (ex.: meses sem campanha), para o feedback ter linha."""
        with self._session_factory.begin() as session:
            session.execute(delete(BanditState))
            session.add_all(
                BanditState(
                    segment=context,
                    arm=arm,
                    successes=counts.get((context, arm), (0, 0))[0],
                    failures=counts.get((context, arm), (0, 0))[1],
                    updated_at=utcnow(),
                )
                for context in CONTEXTS
                for arm in ARM_NAMES
            )


class ThompsonSamplingBandit:
    """Thompson Sampling puro de contagem: amostra de Beta(S+1, F+1) por braço."""

    name = "thompson_sampling"

    def __init__(self, store: ArmStore, rng: np.random.Generator | None = None) -> None:
        self.store = store
        self.rng = rng or np.random.default_rng()

    def sample_scores(self, context: str) -> dict[str, float]:
        counts = self.store.get_counts(context)
        return {arm: float(self.rng.beta(s + 1, f + 1)) for arm, (s, f) in counts.items()}

    def select_arm(self, context: str) -> tuple[str, dict[str, float]]:
        scores = self.sample_scores(context)
        return max(scores, key=scores.get), scores

    def update(self, context: str, arm: str, reward: int) -> None:
        self.store.increment(context, arm, reward)

    def posterior_means(self, context: str) -> dict[str, float]:
        return {arm: posterior_mean(s, f) for arm, (s, f) in self.store.get_counts(context).items()}


class EpsilonGreedyPolicy:
    """Com probabilidade epsilon escolhe um braço aleatório; senão, o de maior conversão média.
    Usado como referência: a exploração é cega (não depende da incerteza de cada braço)."""

    name = "epsilon_greedy"

    def __init__(self, store: ArmStore, epsilon: float, rng: np.random.Generator) -> None:
        self.store = store
        self.epsilon = epsilon
        self.rng = rng

    def select_arm(self, context: str) -> tuple[str, dict[str, float]]:
        means = {arm: posterior_mean(s, f) for arm, (s, f) in self.store.get_counts(context).items()}
        if self.rng.random() < self.epsilon:
            return str(self.rng.choice(ARM_NAMES)), means
        return max(means, key=means.get), means

    def update(self, context: str, arm: str, reward: int) -> None:
        self.store.increment(context, arm, reward)


def _best_arm(arms: list[str], rewards: list[int]) -> str:
    successes, totals = defaultdict(int), defaultdict(int)
    for arm, reward in zip(arms, rewards, strict=True):
        successes[arm] += reward
        totals[arm] += 1
    return max(totals, key=lambda a: posterior_mean(successes[a], totals[a] - successes[a]))


class FixedBaselinePolicy:
    """Regra determinística: sempre o braço com maior conversão histórica, para todos os clientes."""

    name = "baseline_fixo"

    def __init__(self, arm: str) -> None:
        self.arm = arm

    @classmethod
    def from_history(cls, arms: list[str], rewards: list[int]) -> "FixedBaselinePolicy":
        return cls(_best_arm(arms, rewards))

    def select_arm(self, context: str) -> tuple[str, dict[str, float]]:
        return self.arm, {}

    def update(self, context: str, arm: str, reward: int) -> None:
        pass  # regra estática: não aprende


class SegmentedBaselinePolicy:
    """Regra determinística por contexto: o melhor braço histórico de cada (segmento, mês),
    definido uma vez e congelado. Mostra quanto do ganho vem só do contexto."""

    name = "baseline_segmentado"

    def __init__(self, rules: dict[str, str], default_arm: str) -> None:
        self.rules = rules
        self.default_arm = default_arm

    @classmethod
    def from_history(cls, contexts: list[str], arms: list[str], rewards: list[int]) -> "SegmentedBaselinePolicy":
        grouped: dict[str, tuple[list[str], list[int]]] = defaultdict(lambda: ([], []))
        for context, arm, reward in zip(contexts, arms, rewards, strict=True):
            grouped[context][0].append(arm)
            grouped[context][1].append(reward)
        rules = {context: _best_arm(a, r) for context, (a, r) in grouped.items()}
        return cls(rules, _best_arm(arms, rewards))

    def select_arm(self, context: str) -> tuple[str, dict[str, float]]:
        return self.rules.get(context, self.default_arm), {}

    def update(self, context: str, arm: str, reward: int) -> None:
        pass


class UniformRandomPolicy:
    """Divisão uniforme entre os braços, equivalente a um teste A/B/n contínuo."""

    name = "teste_ab_uniforme"

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng

    def select_arm(self, context: str) -> tuple[str, dict[str, float]]:
        return str(self.rng.choice(ARM_NAMES)), {}

    def update(self, context: str, arm: str, reward: int) -> None:
        pass
