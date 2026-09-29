"""Configuração central do projeto.

Toda configuração vem de variáveis de ambiente, com defaults que funcionam
tanto no docker-compose quanto rodando os scripts direto na máquina
(apontando para os containers expostos em localhost).
"""

import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- PostgreSQL (Offline Store + banco da aplicação) ---
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "datathon")
POSTGRES_USER = os.getenv("POSTGRES_USER", "datathon")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "datathon")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

# --- Redis (Online Store) ---
REDIS_CONNECTION_STRING = os.getenv("REDIS_CONNECTION_STRING", "localhost:6379")


def parse_redis_connection_string(connection_string: str) -> dict:
    """Converte o formato do Feast ("host:porta,ssl=true,password=...") em kwargs do redis-py.
    Divide só no primeiro '=' de cada opção: chaves de acesso da Azure terminam em '='."""
    address, *options = connection_string.split(",")
    host, port = address.rsplit(":", 1)
    kwargs: dict = {"host": host, "port": int(port)}
    for option in options:
        key, _, value = option.partition("=")
        if key == "ssl":
            kwargs["ssl"] = value.lower() == "true"
        elif key == "db":
            kwargs["db"] = int(value)
        elif key in ("password", "username"):
            kwargs[key] = value
    return kwargs


# --- Feast ---
FEAST_REPO_PATH = os.getenv("FEAST_REPO_PATH", str(PROJECT_ROOT / "feature_store"))
FEATURE_VIEW_NAME = "client_features"

# --- Dados brutos ---
# Pode ser um caminho local ou az://<container>/<blob> (Azure Blob Storage, autenticado por
# AZURE_STORAGE_CONNECTION_STRING). Se o arquivo local não existir,
# o dataset público é baixado do Kaggle automaticamente.
DATA_PATH = os.getenv("DATA_PATH", str(PROJECT_ROOT / "data" / "raw" / "train.csv"))
KAGGLE_DATASET = "aguado/telemarketing-jyb-dataset"
KAGGLE_DOWNLOAD_URL = f"https://www.kaggle.com/api/v1/datasets/download/{KAGGLE_DATASET}"

# --- MLflow ---
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "mab-thompson-sampling")

# --- Braços do bandit: canal de contato x janela da semana ---
# Cada braço é uma estratégia de oferta que a campanha consegue executar.
ARMS = {
    "celular_inicio_semana": "Oferta via celular, de segunda a quarta",
    "celular_fim_semana": "Oferta via celular, de quinta a sexta",
    "telefone_inicio_semana": "Oferta via telefone fixo, de segunda a quarta",
    "telefone_fim_semana": "Oferta via telefone fixo, de quinta a sexta",
}
ARM_NAMES = list(ARMS)

# --- Contexto da decisão: segmento do cliente x mês da campanha ---
# O Thompson Sampling mantém um Beta(S+1, F+1) independente por (contexto, braço).
# O mês entra porque a melhor abordagem muda ao longo do ano (sazonalidade das campanhas).
SEGMENTS = ["jovem", "adulto", "senior"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def context_key(segment: str, month: str) -> str:
    """Chave do contexto usada no estado do bandit, ex.: "senior|may"."""
    return f"{segment}|{month}"


CONTEXTS = [context_key(segment, month) for segment in SEGMENTS for month in MONTHS]


def month_of(moment: datetime) -> str:
    return MONTHS[moment.month - 1]


def assign_segment(age: int) -> str:
    """Segmenta o cliente pela faixa etária (feature servida pela Feature Store)."""
    if age < 30:
        return "jovem"
    if age < 60:
        return "adulto"
    return "senior"


def to_arm(contact: str, day_of_week: str) -> str:
    """Converte o contato histórico (canal + dia) no braço equivalente."""
    channel = "celular" if contact == "cellular" else "telefone"
    window = "inicio_semana" if day_of_week in ("mon", "tue", "wed") else "fim_semana"
    return f"{channel}_{window}"
