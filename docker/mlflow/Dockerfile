# Servidor MLflow: backend store no PostgreSQL e artefatos em volume local ou Azure Blob Storage (wasbs://).
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "mlflow>=3.4,<4" "psycopg2-binary>=2.9,<3" "azure-storage-blob>=12.19,<13" "azure-identity>=1.15,<2"

COPY entrypoint.py /entrypoint.py

EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=4)"

ENTRYPOINT ["python", "/entrypoint.py"]
