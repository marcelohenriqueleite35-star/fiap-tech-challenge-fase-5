"""Ingestão e tratamento do dataset "telemarketing-jyb-dataset" (Kaggle).

Fluxo:
1. Obtém o CSV bruto (arquivo local, az:// no Azure Blob Storage ou download público do Kaggle).
2. Remove colunas com vazamento temporal e dados sensíveis.
3. Grava duas tabelas no PostgreSQL:
   - client_features: atributos do cliente (fonte da Offline Store do Feast).
   - campaign_history: histórico de contatos (braço executado, mês e conversão),
     usado pela simulação e pelo baseline.

Uso: python -m src.data_processing
"""

import io
import logging
import os
import tempfile
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.config import DATA_PATH, KAGGLE_DOWNLOAD_URL, assign_segment, to_arm
from src.database import get_engine
from src.golden_set import GOLDEN_CLIENTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("data_processing")
# O SDK da Azure registra cada requisição HTTP em INFO.
logging.getLogger("azure").setLevel(logging.WARNING)

# `duration` só é conhecida depois da ligação (vazamento clássico do dataset UCI);
# `campaign` conta os contatos da campanha atual incluindo o último, também posterior à decisão.
LEAKAGE_COLUMNS = ["duration", "campaign"]
# Atributos pessoais que não devem orientar a oferta (LGPD / risco de discriminação).
SENSITIVE_COLUMNS = ["marital", "default"]
# Índice herdado do dataset original.
INDEX_COLUMNS = ["unnamed:_0", "unnamed_0"]
# Indicadores macroeconômicos descrevem o momento da campanha, não o cliente.
MACRO_COLUMNS = ["emp_var_rate", "cons_price_idx", "cons_conf_idx", "euribor3m", "nr_employed"]

FEATURE_COLUMNS = [
    "age",
    "job",
    "education",
    "housing",
    "loan",
    "previous",
    "days_since_last_contact",
    "poutcome",
    "customer_segment",
]


def _download_from_kaggle(target: Path) -> Path:
    """Baixa o zip público do Kaggle e extrai train.csv/test.csv. Se o diretório de
    destino não for gravável (volume montado com outro UID), usa um diretório temporário."""
    log.info("Arquivo %s não encontrado. Baixando dataset do Kaggle...", target)
    with urllib.request.urlopen(KAGGLE_DOWNLOAD_URL, timeout=120) as response:
        payload = response.read()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(target.parent)
        return target
    except PermissionError:
        fallback_dir = Path(tempfile.mkdtemp(prefix="telemarketing-"))
        log.warning("Sem permissão de escrita em %s; usando %s", target.parent, fallback_dir)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(fallback_dir)
        return fallback_dir / target.name


def _resolve_local_path(path: str) -> Path:
    if path.startswith("az://"):
        return _download_from_blob(path)
    local = Path(path)
    if not local.exists():
        local = _download_from_kaggle(local)
    return local


def _download_from_blob(path: str) -> Path:
    """Baixa az://<container>/<blob> (e o test.csv vizinho, se existir) do Azure Blob Storage.
    A autenticação vem de AZURE_STORAGE_CONNECTION_STRING."""
    from azure.core.exceptions import ResourceNotFoundError
    from azure.storage.blob import BlobServiceClient

    container, blob_name = path.removeprefix("az://").split("/", 1)
    service = BlobServiceClient.from_connection_string(os.environ["AZURE_STORAGE_CONNECTION_STRING"])
    local_dir = Path(tempfile.mkdtemp(prefix="telemarketing-"))

    for name, required in ((blob_name, True), (str(Path(blob_name).with_name("test.csv")), False)):
        target = local_dir / Path(name).name
        try:
            log.info("Baixando az://%s/%s", container, name)
            target.write_bytes(service.get_blob_client(container, name).download_blob().readall())
        except ResourceNotFoundError:
            if required:
                raise
            log.info("az://%s/%s não encontrado; seguindo só com o treino", container, name)
    return local_dir / Path(blob_name).name


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Lê o CSV do Kaggle. O arquivo publicado começa com um bloco de bytes NUL
    e usa ';' como separador, então os NULs são removidos antes do parse."""
    content = path.read_bytes().replace(b"\x00", b"").decode("utf-8")
    df = pd.read_csv(io.StringIO(content), sep=";")
    df.columns = [c.strip().lower().replace(".", "_").replace(" ", "_") for c in df.columns]
    return df


def build_client_features(df: pd.DataFrame, event_ts: datetime) -> pd.DataFrame:
    features = pd.DataFrame(
        {
            "client_id": df["id"].astype("int64"),
            "age": df["age"].astype("int64"),
            "job": df["job"].astype(str),
            "education": df["education"].astype(str),
            "housing": df["housing"].astype(str),
            "loan": df["loan"].astype(str),
            "previous": df["previous"].astype("int64"),
            # pdays = 999 significa "nunca contatado"; vira -1 para não distorcer a escala.
            "days_since_last_contact": df["pdays"].where(df["pdays"] != 999, -1).astype("int64"),
            "poutcome": df["poutcome"].astype(str),
        }
    )
    features["customer_segment"] = features["age"].map(assign_segment)
    features["event_timestamp"] = event_ts
    features["created_timestamp"] = event_ts
    return features


def build_campaign_history(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": df["id"].astype("int64"),
            "arm": [to_arm(c, d) for c, d in zip(df["contact"], df["day_of_week"], strict=True)],
            # Mês da campanha: parte do contexto da decisão (conhecido antes do contato).
            "month": df["month"].astype(str).str.lower(),
            "converted": df["y"].astype(str).str.lower().isin(["yes", "1"]).astype("int64"),
        }
    )


def drop_unwanted_columns(df: pd.DataFrame) -> pd.DataFrame:
    to_drop = [c for c in LEAKAGE_COLUMNS + SENSITIVE_COLUMNS + INDEX_COLUMNS + MACRO_COLUMNS if c in df.columns]
    log.info("Removendo colunas (vazamento/sensíveis/índice/macro): %s", to_drop)
    return df.drop(columns=to_drop)


def load_to_postgres(features: pd.DataFrame, history: pd.DataFrame) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        features.to_sql("client_features", conn, if_exists="replace", index=False, method="multi", chunksize=5000)
        history.to_sql("campaign_history", conn, if_exists="replace", index=False, method="multi", chunksize=5000)
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_client_features_client_id ON client_features (client_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_history_client_id ON campaign_history (client_id)"))


def run() -> None:
    train_path = _resolve_local_path(DATA_PATH)
    train = drop_unwanted_columns(read_raw_csv(train_path))
    log.info("Treino: %d linhas, colunas restantes: %s", len(train), list(train.columns))

    event_ts = datetime.now(UTC)
    frames = [build_client_features(train, event_ts)]

    # test.csv (sem alvo) representa a base de clientes a ser atendida em produção:
    # entra na Feature Store para ser servido pela API, mas não no histórico.
    test_path = train_path.with_name("test.csv")
    if test_path.exists():
        test = drop_unwanted_columns(read_raw_csv(test_path))
        frames.append(build_client_features(test, event_ts))
        log.info("Base de produção (test.csv): %d clientes", len(test))

    golden = pd.DataFrame(GOLDEN_CLIENTS)
    golden["customer_segment"] = golden["age"].map(assign_segment)
    golden["event_timestamp"] = event_ts
    golden["created_timestamp"] = event_ts
    frames.append(golden)

    features = pd.concat(frames, ignore_index=True).drop_duplicates("client_id", keep="first")
    history = build_campaign_history(train)

    load_to_postgres(features, history)
    log.info(
        "Offline Store atualizada: %d clientes em client_features, %d contatos em campaign_history "
        "(conversão histórica %.2f%%)",
        len(features),
        len(history),
        100 * history["converted"].mean(),
    )


if __name__ == "__main__":
    run()
