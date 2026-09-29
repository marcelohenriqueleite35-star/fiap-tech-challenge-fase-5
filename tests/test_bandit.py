import numpy as np
import pandas as pd

from src.bandit import (
    EpsilonGreedyPolicy,
    FixedBaselinePolicy,
    InMemoryArmStore,
    SegmentedBaselinePolicy,
    ThompsonSamplingBandit,
)
from src.config import ARM_NAMES, CONTEXTS, assign_segment, context_key, to_arm
from src.simulation import SimulationConfig, environment_rates, run_simulation


def test_assign_segment_boundaries():
    assert assign_segment(29) == "jovem"
    assert assign_segment(30) == "adulto"
    assert assign_segment(59) == "adulto"
    assert assign_segment(60) == "senior"


def test_to_arm_maps_channel_and_week_window():
    assert to_arm("cellular", "mon") == "celular_inicio_semana"
    assert to_arm("telephone", "fri") == "telefone_fim_semana"


def test_contexts_cover_every_segment_and_month():
    assert context_key("senior", "may") == "senior|may"
    assert len(CONTEXTS) == 3 * 12
    assert "jovem|jan" in CONTEXTS


def test_update_counts_successes_and_failures():
    store = InMemoryArmStore()
    bandit = ThompsonSamplingBandit(store, rng=np.random.default_rng(0))
    bandit.update("adulto|may", ARM_NAMES[0], 1)
    bandit.update("adulto|may", ARM_NAMES[0], 0)
    bandit.update("adulto|may", ARM_NAMES[0], 0)
    assert store.get_counts("adulto|may")[ARM_NAMES[0]] == (1, 2)
    assert store.get_counts("adulto|jun")[ARM_NAMES[0]] == (0, 0)


def test_thompson_sampling_converges_to_best_arm():
    rates = dict(zip(ARM_NAMES, [0.05, 0.30, 0.10, 0.02], strict=True))
    rng = np.random.default_rng(42)
    bandit = ThompsonSamplingBandit(InMemoryArmStore(), rng=np.random.default_rng(7))
    picks = []
    for _ in range(3000):
        arm, _ = bandit.select_arm("jovem|may")
        bandit.update("jovem|may", arm, int(rng.random() < rates[arm]))
        picks.append(arm)
    last = picks[-1000:]
    assert last.count(ARM_NAMES[1]) / len(last) > 0.9


def test_epsilon_greedy_explores_about_epsilon():
    store = InMemoryArmStore()
    for _ in range(100):
        store.increment("adulto|may", ARM_NAMES[2], 1)
    policy = EpsilonGreedyPolicy(store, epsilon=0.2, rng=np.random.default_rng(1))
    picks = [policy.select_arm("adulto|may")[0] for _ in range(5000)]
    off_best = sum(p != ARM_NAMES[2] for p in picks) / len(picks)
    assert 0.12 < off_best < 0.18  # 20% aleatório, e 1/4 disso cai no próprio melhor braço


def test_baseline_picks_best_historical_arm():
    arms = ["a", "a", "b", "b", "b"]
    rewards = [1, 0, 1, 1, 0]
    assert FixedBaselinePolicy.from_history(arms, rewards).arm == "b"


def test_segmented_baseline_uses_best_arm_per_context():
    contexts = ["x", "x", "x", "y", "y", "y"]
    arms = ["a", "b", "b", "a", "a", "b"]
    rewards = [1, 0, 0, 0, 0, 1]
    policy = SegmentedBaselinePolicy.from_history(contexts, arms, rewards)
    assert policy.select_arm("x")[0] == "a"
    assert policy.select_arm("y")[0] == "b"
    assert policy.select_arm("z")[0] == policy.default_arm


def _toy_history(n: int = 4000, seed: int = 0) -> pd.DataFrame:
    # Em maio o melhor braço é o 0; em agosto, o 3. Uma regra fixa não acerta os dois.
    rng = np.random.default_rng(seed)
    best = {"may": ARM_NAMES[0], "aug": ARM_NAMES[3]}
    rows = []
    for client_id in range(n):
        month = str(rng.choice(["may", "aug"]))
        arm = str(rng.choice(ARM_NAMES))
        rows.append(
            {
                "client_id": client_id,
                "customer_segment": "adulto",
                "month": month,
                "arm": arm,
                "converted": int(rng.random() < (0.30 if arm == best[month] else 0.10)),
            }
        )
    return pd.DataFrame(rows)


def test_environment_rates_cover_every_arm_of_every_context():
    data = _toy_history(200)
    data["context"] = [context_key(s, m) for s, m in zip(data["customer_segment"], data["month"], strict=True)]
    rates = environment_rates(data)
    assert set(rates) == {(c, a) for c in data["context"].unique() for a in ARM_NAMES}


def test_contextual_thompson_sampling_beats_fixed_baseline():
    results = run_simulation(_toy_history(), SimulationConfig(seed=1))["results"]
    assert results["thompson_sampling"]["conversion_rate"] > results["baseline_fixo"]["conversion_rate"]
    assert results["thompson_sampling"]["conversion_rate"] > results["teste_ab_uniforme"]["conversion_rate"]
