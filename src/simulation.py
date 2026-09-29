"""Simulação das políticas de oferta sobre o histórico de campanhas.

Usada pelo pipeline (src/train_and_experiment.py) e pelo notebook de avaliação.
Não depende de banco nem de Feature Store: recebe um DataFrame com as colunas
client_id, customer_segment, month, arm e converted.

Metodologia:
1. Uma fração "histórica" (padrão 30%) define as regras dos baselines e inicializa as
   contagens do Thompson Sampling e do Epsilon-Greedy (warm start), para que todas as
   políticas comecem com a mesma informação.
2. Os demais clientes chegam um a um, em ordem de calendário (mês a mês, como numa
   campanha real). Cada política escolhe um braço para o contexto do cliente e a
   conversão é sorteada de Bernoulli(p[contexto, braço]), com p estimado dos dados reais.
3. Todas as políticas usam os mesmos números aleatórios por cliente (common random
   numbers), então a diferença entre elas vem só das escolhas.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from src.bandit import (
    EpsilonGreedyPolicy,
    FixedBaselinePolicy,
    InMemoryArmStore,
    SegmentedBaselinePolicy,
    ThompsonSamplingBandit,
    UniformRandomPolicy,
)
from src.config import ARM_NAMES, MONTHS, context_key

# Peso (em contatos) da taxa do mês ao estimar a taxa de um contexto pequeno.
SHRINKAGE = 20


@dataclass
class SimulationConfig:
    seed: int = 42
    historical_fraction: float = 0.3
    cold_start: bool = False
    epsilon: float = 0.1
    calendar_order: bool = True


def add_context(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    data["context"] = [context_key(s, m) for s, m in zip(data["customer_segment"], data["month"], strict=True)]
    return data


def environment_rates(data: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Taxa de conversão "real" de cada (contexto, braço).

    Contextos com poucos contatos são puxados para a taxa do mesmo braço no mês inteiro
    (média ponderada com SHRINKAGE contatos), para o simulador não tratar ruído como sinal.
    """
    by_month = data.groupby(["month", "arm"])["converted"].agg(["sum", "count"])
    by_context = data.groupby(["context", "arm"])["converted"].agg(["sum", "count"])
    rates = {}
    for context, month in data[["context", "month"]].drop_duplicates().itertuples(index=False):
        for arm in ARM_NAMES:
            m_sum, m_count = by_month.loc[(month, arm)] if (month, arm) in by_month.index else (0, 0)
            month_rate = (m_sum + 1) / (m_count + 2)
            c_sum, c_count = by_context.loc[(context, arm)] if (context, arm) in by_context.index else (0, 0)
            rates[(context, arm)] = (c_sum + SHRINKAGE * month_rate) / (c_count + SHRINKAGE)
    return rates


def split_history(data: pd.DataFrame, config: SimulationConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    historical, stream = train_test_split(
        data, train_size=config.historical_fraction, random_state=config.seed, stratify=data["converted"]
    )
    if config.calendar_order:
        stream = stream.assign(_order=stream["month"].map(MONTHS.index)).sort_values("_order", kind="stable")
        stream = stream.drop(columns="_order")
    return historical, stream


def build_policies(historical: pd.DataFrame, config: SimulationConfig) -> tuple[list, InMemoryArmStore]:
    arms, rewards = historical["arm"].tolist(), historical["converted"].tolist()

    ts_store, eg_store = InMemoryArmStore(), InMemoryArmStore()
    if not config.cold_start:
        for context, arm, converted in historical[["context", "arm", "converted"]].itertuples(index=False):
            ts_store.increment(context, arm, int(converted))
            eg_store.increment(context, arm, int(converted))

    policies = [
        ThompsonSamplingBandit(ts_store, rng=np.random.default_rng(config.seed + 1)),
        EpsilonGreedyPolicy(eg_store, config.epsilon, rng=np.random.default_rng(config.seed + 3)),
        SegmentedBaselinePolicy.from_history(historical["context"].tolist(), arms, rewards),
        FixedBaselinePolicy.from_history(arms, rewards),
        UniformRandomPolicy(np.random.default_rng(config.seed + 2)),
    ]
    return policies, ts_store


def simulate(policy, contexts: np.ndarray, uniforms: np.ndarray, rates: dict) -> dict:
    oracle = {c: max(rates[(c, a)] for a in ARM_NAMES) for c in set(contexts)}
    rewards = np.zeros(len(contexts), dtype=int)
    chosen = []
    regret = 0.0
    for t, (context, u) in enumerate(zip(contexts, uniforms, strict=True)):
        arm, _ = policy.select_arm(context)
        p = rates[(context, arm)]
        reward = int(u < p)
        policy.update(context, arm, reward)
        rewards[t] = reward
        regret += oracle[context] - p
        chosen.append(arm)
    best = np.array([rates[(c, a)] == oracle[c] for c, a in zip(contexts, chosen, strict=True)])
    return {
        "rewards": rewards,
        "chosen": chosen,
        "cumulative_rate": np.cumsum(rewards) / np.arange(1, len(rewards) + 1),
        "conversion_rate": float(rewards.mean()),
        "conversions": int(rewards.sum()),
        "regret": regret,
        # Fração das escolhas fora do melhor braço do contexto (exploração ou erro da regra).
        "suboptimal_rate": float(1 - best.mean()),
        "arm_share": pd.Series(chosen).value_counts(normalize=True).reindex(ARM_NAMES, fill_value=0).to_dict(),
    }


def run_simulation(data: pd.DataFrame, config: SimulationConfig) -> dict:
    """Roda todas as políticas sobre o mesmo fluxo de clientes e devolve resultados e estado do TS."""
    # Ordem estável: o mesmo seed gera o mesmo resultado no pipeline e no notebook.
    data = add_context(data).sort_values("client_id", kind="stable").reset_index(drop=True)
    historical, stream = split_history(data, config)
    rates = environment_rates(data)
    policies, ts_store = build_policies(historical, config)

    uniforms = np.random.default_rng(config.seed).random(len(stream))
    contexts = stream["context"].to_numpy()
    results = {p.name: simulate(p, contexts, uniforms, rates) for p in policies}
    return {
        "seed": config.seed,
        "results": results,
        "policies": {p.name: p for p in policies},
        "ts_store": ts_store,
        "historical": historical,
        "stream": stream,
        "rates": rates,
    }


def summarize_seeds(runs: list[dict]) -> pd.DataFrame:
    """Uma linha por (seed, política) com as métricas principais."""
    return pd.DataFrame(
        [
            {
                "seed": run["seed"],
                "politica": name,
                "conversao": res["conversion_rate"],
                "conversoes": res["conversions"],
                "regret": res["regret"],
                "subotimas": res["suboptimal_rate"],
            }
            for run in runs
            for name, res in run["results"].items()
        ]
    )
