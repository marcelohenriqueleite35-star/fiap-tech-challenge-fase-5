"""Registra as definições do Feast e materializa a Offline Store (Postgres) no Redis.

Equivale a rodar `feast apply` seguido de `feast materialize <inicio> <fim>`
dentro de feature_store/.

Uso: python feature_store/materialize.py [--days 3650]
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from feast import FeatureStore

REPO_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_PATH))

from features import client, client_features_view  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3650, help="janela de materialização em dias")
    args = parser.parse_args()

    store = FeatureStore(repo_path=str(REPO_PATH))

    print("[feast] apply: registrando entidade e feature view no registry...")
    store.apply([client, client_features_view])

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    print(f"[feast] materialize: {start:%Y-%m-%d} -> {end:%Y-%m-%d %H:%M} (Postgres -> Redis)")
    store.materialize(start_date=start, end_date=end, feature_views=[client_features_view.name])

    sample = store.get_online_features(
        features=["client_features:customer_segment", "client_features:age"],
        entity_rows=[{"client_id": 900001}],
    ).to_dict()
    print(f"[feast] leitura de verificação no Redis (cliente 900001): {sample}")


if __name__ == "__main__":
    main()
