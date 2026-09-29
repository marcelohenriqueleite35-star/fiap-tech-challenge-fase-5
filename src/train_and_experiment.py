"""Pipeline de avaliação: Thompson Sampling vs. Epsilon-Greedy vs. baselines vs. teste A/B.

1. Lê o histórico de contatos (campaign_history) e as features dos clientes pela
   Offline Store do Feast (get_historical_features, com join point-in-time).
2. Roda a simulação de src/simulation.py (metodologia descrita lá) para vários seeds:
   contexto = segmento do cliente x mês da campanha, clientes chegando em ordem de
   calendário. As métricas são a média dos seeds; o gráfico, o Golden Set e o estado
   publicado vêm do primeiro seed.
3. Métricas e artefatos vão para o MLflow; o estado aprendido pelo Thompson Sampling
   é publicado na tabela bandit_state, de onde a API passa a servir.
4. Executa o Golden Set (5 clientes fictícios) e imprime o resultado no console.

Uso: python -m src.train_and_experiment [--seed 42] [--n-seeds 5] [--historical-fraction 0.3]
                                        [--cold-start] [--random-order] [--no-publish]
"""

import argparse
import json
import logging
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from feast import FeatureStore  # noqa: E402

from src.bandit import PostgresArmStore, ThompsonSamplingBandit  # noqa: E402
from src.config import (  # noqa: E402
    ARM_NAMES,
    ARMS,
    CONTEXTS,
    FEAST_REPO_PATH,
    MLFLOW_EXPERIMENT_NAME,
    MLFLOW_TRACKING_URI,
    assign_segment,
    context_key,
)
from src.database import get_engine, get_session_factory, init_db  # noqa: E402
from src.golden_set import GOLDEN_CLIENTS, GOLDEN_MONTHS  # noqa: E402
from src.simulation import SimulationConfig, run_simulation, summarize_seeds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train_and_experiment")

LOG_EVERY = 500
# Paleta categórica (ordem fixa, validada para daltonismo); cada política mantém sempre a mesma cor.
POLICY_COLORS = {
    "thompson_sampling": "#2a78d6",
    "baseline_fixo": "#eb6834",
    "teste_ab_uniforme": "#1baf7a",
    "epsilon_greedy": "#eda100",
    "baseline_segmentado": "#e87ba4",
}


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------
def load_experiment_data() -> pd.DataFrame:
    history = pd.read_sql("SELECT client_id, arm, month, converted FROM campaign_history", get_engine())
    entity_df = history[["client_id"]].copy()
    entity_df["event_timestamp"] = datetime.now(UTC)

    store = FeatureStore(repo_path=FEAST_REPO_PATH)
    features = store.get_historical_features(
        entity_df=entity_df,
        features=["client_features:customer_segment", "client_features:age"],
    ).to_df()

    data = history.merge(features[["client_id", "customer_segment", "age"]], on="client_id", how="inner")
    missing = len(history) - len(data)
    if missing:
        log.warning("%d clientes do histórico sem features na Offline Store foram descartados", missing)
    return data


def plot_curves(results: dict, path: Path, title: str = "Taxa de conversão acumulada por política") -> None:
    skip = 500  # os primeiros passos são só ruído de amostra pequena
    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor="#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    y_max = max(r["cumulative_rate"][skip:].max() for r in results.values()) * 100 * 1.08
    # Eixo a partir de perto da pior curva: as políticas diferem em décimos de ponto percentual.
    y_min = min(r["cumulative_rate"][skip:].min() for r in results.values()) * 100 * 0.9
    ax.set_ylim(y_min, y_max)

    # Rótulos diretos no fim das curvas, afastados para não colidirem quando as curvas empatam.
    min_gap = (y_max - y_min) * 0.045
    ends = sorted(((res["cumulative_rate"][-1] * 100, name) for name, res in results.items()), reverse=True)
    label_y, previous = {}, None
    for value, name in ends:
        y = value if previous is None else min(value, previous - min_gap)
        label_y[name], previous = y, y

    for name, res in results.items():
        curve = res["cumulative_rate"][skip:] * 100
        x = np.arange(skip + 1, skip + len(curve) + 1)
        ax.plot(x, curve, color=POLICY_COLORS[name], linewidth=2, label=name)
        ax.annotate(
            f"{name} {curve[-1]:.2f}%",
            xy=(x[-1], curve[-1]),
            xytext=(x[-1] * 1.01, label_y[name]),
            textcoords="data",
            va="center",
            fontsize=9,
            color="#0b0b0b",
        )
    ax.set_xlabel("Clientes abordados", color="#52514e")
    ax.set_ylabel("Taxa de conversão acumulada (%)", color="#52514e")
    ax.set_title(title, loc="left", color="#0b0b0b")
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors="#52514e")
    ax.legend(loc="upper right", frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Golden Set
# ---------------------------------------------------------------------------
def run_golden_set(bandit: ThompsonSamplingBandit, rates: dict) -> pd.DataFrame:
    rows = []
    for client in GOLDEN_CLIENTS:
        segment = assign_segment(client["age"])
        month = GOLDEN_MONTHS[client["client_id"]]
        context = context_key(segment, month)
        sampled_arm, _ = bandit.select_arm(context)
        means = bandit.posterior_means(context)
        best_arm = max(means, key=means.get)
        best_in_data = max(ARM_NAMES, key=lambda a: rates[(context, a)])
        rows.append(
            {
                "client_id": client["client_id"],
                "idade": client["age"],
                "profissao": client["job"],
                "poutcome": client["poutcome"],
                "contexto": context,
                "oferta_recomendada": sampled_arm,
                "melhor_braco_modelo": best_arm,
                "conversao_esperada_%": round(100 * means[best_arm], 2),
                "melhor_braco_nos_dados": best_in_data,
                # A decisão faz sentido se o braço que o modelo considera melhor é o melhor nos dados.
                "faz_sentido": "sim" if best_arm == best_in_data else "não",
            }
        )
    return pd.DataFrame(rows)


def print_golden_set(golden: pd.DataFrame) -> None:
    line = "=" * 150
    print(f"\n{line}\nGOLDEN SET - recomendações do Thompson Sampling treinado\n{line}")
    print(golden.to_string(index=False))
    print(line)
    for _, row in golden.iterrows():
        print(f"  Cliente {row['client_id']} ({row['contexto']}): {ARMS[row['oferta_recomendada']]}")
    print(line + "\n")


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42, help="primeiro seed")
    parser.add_argument("--n-seeds", type=int, default=5, help="quantidade de seeds (seed, seed+1, ...)")
    parser.add_argument("--historical-fraction", type=float, default=0.3)
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help="Thompson Sampling começa do prior Beta(1,1), sem usar a fração histórica",
    )
    parser.add_argument("--no-publish", action="store_true", help="não grava o estado do bandit no Postgres")
    parser.add_argument("--epsilon", type=float, default=0.1, help="exploração do Epsilon-Greedy")
    parser.add_argument("--random-order", action="store_true", help="clientes em ordem aleatória, em vez de mês a mês")
    args = parser.parse_args()
    config = SimulationConfig(
        seed=args.seed,
        historical_fraction=args.historical_fraction,
        cold_start=args.cold_start,
        epsilon=args.epsilon,
        calendar_order=not args.random_order,
    )

    data = load_experiment_data()
    runs = []
    for seed in range(args.seed, args.seed + args.n_seeds):
        runs.append(run_simulation(data, replace(config, seed=seed)))
        log.info("Seed %d simulado", seed)
    sim = runs[0]
    results, ts_store = sim["results"], sim["ts_store"]
    baseline_arm = sim["policies"]["baseline_fixo"].arm
    log.info(
        "Dados: %d históricos, %d no fluxo simulado. Baseline fixo = %s",
        len(sim["historical"]),
        len(sim["stream"]),
        baseline_arm,
    )

    per_seed = summarize_seeds(runs)
    means = per_seed.groupby("politica")[["conversao", "conversoes", "regret", "subotimas"]].mean()
    stds = per_seed.groupby("politica")["conversao"].std().fillna(0)
    ts_by_seed = per_seed[per_seed["politica"] == "thompson_sampling"].set_index("seed")["conversao"]
    ts_rate = means.loc["thompson_sampling", "conversao"]
    summary = {}
    for name in results:
        if name == "thompson_sampling":
            continue
        other = per_seed[per_seed["politica"] == name].set_index("seed")["conversao"]
        summary[f"lift_vs_{name}_pct"] = 100 * (ts_rate / means.loc[name, "conversao"] - 1)
        summary[f"ts_wins_vs_{name}"] = int((ts_by_seed > other).sum())

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    with mlflow.start_run(run_name=f"ts_vs_baseline_seed{args.seed}") as run:
        mlflow.log_params(
            {
                "seeds": f"{args.seed}..{args.seed + args.n_seeds - 1}",
                "n_seeds": args.n_seeds,
                "historical_fraction": args.historical_fraction,
                "n_historical": len(sim["historical"]),
                "n_stream": len(sim["stream"]),
                "arms": ",".join(ARM_NAMES),
                "context": "segmento_etario x mes_campanha",
                "n_contexts": len(CONTEXTS),
                "prior": "Beta(1,1)",
                "warm_start": not args.cold_start,
                "epsilon": args.epsilon,
                "stream_order": "calendario" if config.calendar_order else "aleatoria",
                "baseline_arm": baseline_arm,
            }
        )
        for name, row in means.iterrows():
            mlflow.log_metrics(
                {
                    f"conversion_rate_{name}": row["conversao"],
                    f"conversion_rate_std_{name}": stds[name],
                    f"conversions_{name}": row["conversoes"],
                    f"regret_{name}": row["regret"],
                    f"suboptimal_rate_{name}": row["subotimas"],
                }
            )
        for name, res in results.items():
            for step in range(LOG_EVERY - 1, len(res["cumulative_rate"]), LOG_EVERY):
                mlflow.log_metric(f"cum_conversion_rate_{name}", float(res["cumulative_rate"][step]), step=step + 1)
        mlflow.log_metrics(summary)

        golden = run_golden_set(sim["policies"]["thompson_sampling"], sim["rates"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            title = f"Conversão acumulada por política (seed {args.seed})"
            plot_curves(results, tmp_path / "conversion_curves.png", title)
            pd.DataFrame({n: r["cumulative_rate"] for n, r in results.items()}).to_csv(
                tmp_path / "cumulative_conversion.csv", index_label="step"
            )
            state = {f"{s}|{a}": {"successes": c[0], "failures": c[1]} for (s, a), c in ts_store.snapshot().items()}
            (tmp_path / "bandit_state.json").write_text(json.dumps(state, indent=2))
            (tmp_path / "arm_share.json").write_text(
                json.dumps({n: r["arm_share"] for n, r in results.items()}, indent=2)
            )
            golden.to_csv(tmp_path / "golden_set.csv", index=False)
            per_seed.to_csv(tmp_path / "resultados_por_seed.csv", index=False)
            mlflow.log_artifacts(tmp)
        run_id = run.info.run_id

    if not args.no_publish:
        init_db()
        PostgresArmStore(get_session_factory()).replace_all(ts_store.snapshot())
        log.info("Estado do Thompson Sampling publicado na tabela bandit_state")

    print(f"\nRESULTADO DA SIMULAÇÃO (média de {args.n_seeds} seeds, {len(sim['stream'])} clientes por seed)")
    for name, row in means.sort_values("conversao", ascending=False).iterrows():
        print(
            f"  {name:<20} conversão {100 * row['conversao']:6.2f}% (± {100 * stds[name]:.2f})  "
            f"regret {row['regret']:8.1f}  subótimas {100 * row['subotimas']:5.1f}%"
        )
    for name in results:
        if name != "thompson_sampling":
            print(
                f"  TS vs {name:<20} lift {summary[f'lift_vs_{name}_pct']:+6.2f}%  "
                f"(TS vence em {summary[f'ts_wins_vs_{name}']}/{args.n_seeds} seeds)"
            )
    print(f"  MLflow run_id: {run_id}")
    print_golden_set(golden)


if __name__ == "__main__":
    main()
