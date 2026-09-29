"""Garante que o banco `mlflow` existe no PostgreSQL e sobe o servidor MLflow.

Funciona igual no docker-compose e no Azure Container Apps com PostgreSQL Flexible Server (onde só um banco é criado
pelo Terraform), sem precisar de scripts de init no Postgres.
"""

import os

import psycopg2
from psycopg2 import sql

host = os.environ["POSTGRES_HOST"]
port = os.environ.get("POSTGRES_PORT", "5432")
user = os.environ["POSTGRES_USER"]
password = os.environ["POSTGRES_PASSWORD"]
admin_db = os.environ.get("POSTGRES_DB", "postgres")
mlflow_db = os.environ.get("MLFLOW_DB_NAME", "mlflow")
sslmode = os.environ.get("POSTGRES_SSLMODE", "prefer")

conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=admin_db, sslmode=sslmode)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (mlflow_db,))
    if cur.fetchone() is None:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(mlflow_db)))
        print(f"[mlflow] banco '{mlflow_db}' criado")
conn.close()

backend_uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{mlflow_db}?sslmode={sslmode}"
artifacts = os.environ.get("MLFLOW_ARTIFACTS_DESTINATION", "/mlflow/artifacts")

args = [
    "mlflow",
    "server",
    "--host",
    "0.0.0.0",
    "--port",
    "5000",
    "--backend-store-uri",
    backend_uri,
    "--artifacts-destination",
    artifacts,
    "--serve-artifacts",
    # Aceita Host headers de outros containers e do ingress (proteção de DNS rebinding do MLflow 3.x).
    "--allowed-hosts",
    os.environ.get("MLFLOW_ALLOWED_HOSTS", "*"),
    # O padrão do MLflow são 4 workers (~150 MB cada): estoura VMs pequenas como a do Docker Desktop.
    "--workers",
    os.environ.get("MLFLOW_WORKERS", "1"),
]
# O executor de jobs do MLflow 3.x (scorers/judges de GenAI) sobe 7 processos huey (~720 MB)
# que este projeto não usa.
os.environ.setdefault("MLFLOW_SERVER_ENABLE_JOB_EXECUTION", "false")
os.execvp(args[0], args)
