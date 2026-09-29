#!/usr/bin/env sh
# Builda e publica as imagens da API/pipeline e do MLflow no Azure Container Registry.
# Uso: ./scripts/push_images.sh [tag]   (na raiz do projeto, depois de o ACR existir)
#      RESOURCE_GROUP=rg-outro ./scripts/push_images.sh    (se mudou project_name/environment)
set -eu
# shellcheck source=scripts/lib/docker_tools.sh
. "$(dirname "$0")/lib/docker_tools.sh"
use_tools # terraform/az locais ou via Docker

TAG="${1:-latest}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-datathon-mab-dev}"

# Consulta o ACR pelo resource group: funciona mesmo após um apply parcial (-target).
ACR_NAME="$(az acr list --resource-group "$RESOURCE_GROUP" --query '[0].name' --output tsv)"
if [ -z "$ACR_NAME" ]; then
  echo "Nenhum ACR em $RESOURCE_GROUP. Rode antes: terraform -chdir=terraform apply -target=azurerm_container_registry.main" >&2
  exit 1
fi
LOGIN_SERVER="$(az acr show --name "$ACR_NAME" --query loginServer --output tsv)"

# Config temporária do Docker: o login no ACR não depende do credsStore do host (o "desktop"/"pass" do
# Docker Desktop no Linux falha com "pass not initialized"). O token (válido ~3h) é apagado no final.
DOCKER_HOST="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
export DOCKER_HOST
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
echo '{}' >"$DOCKER_CONFIG/config.json"

# Token do Entra ID (o admin do ACR fica desabilitado); o docker login roda no host.
az acr login --name "$ACR_NAME" --expose-token --query accessToken --output tsv |
  docker login "$LOGIN_SERVER" --username 00000000-0000-0000-0000-000000000000 --password-stdin

docker build --platform linux/amd64 -t "$LOGIN_SERVER/datathon-mab-app:$TAG" .
docker build --platform linux/amd64 -t "$LOGIN_SERVER/datathon-mab-mlflow:$TAG" docker/mlflow

docker push "$LOGIN_SERVER/datathon-mab-app:$TAG"
docker push "$LOGIN_SERVER/datathon-mab-mlflow:$TAG"
echo "Imagens publicadas em $LOGIN_SERVER com a tag $TAG"
