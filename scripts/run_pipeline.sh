#!/usr/bin/env sh
# Pipeline completo: ingestão -> Offline Store -> materialização no Redis -> simulação + MLflow.
set -eu

echo "==> [1/3] Processamento de dados (Kaggle -> PostgreSQL / Offline Store)"
python -m src.data_processing

echo "==> [2/3] Feast apply + materialize (PostgreSQL -> Redis / Online Store)"
python feature_store/materialize.py

echo "==> [3/3] Simulação Thompson Sampling vs. Baseline + MLflow + Golden Set"
python -m src.train_and_experiment "$@"
