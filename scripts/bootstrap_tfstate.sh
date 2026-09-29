#!/usr/bin/env sh
# Cria (uma vez só) o Storage Account que guarda o terraform.tfstate e gera terraform/backend.hcl.
# Ele fica FORA do Terraform do projeto (resource group próprio): precisa existir antes do
# `terraform init` e não pode ser apagado por um `terraform destroy`.
#
# Uso: ./scripts/bootstrap_tfstate.sh [regiao]     (padrão: centralus)
set -eu
# shellcheck source=scripts/lib/docker_tools.sh
. "$(dirname "$0")/lib/docker_tools.sh"
use_tools # terraform/az locais ou via Docker

LOCATION="${1:-centralus}"
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
RESOURCE_GROUP="rg-datathon-mab-tfstate"
# Nome único globalmente, derivado da assinatura (3-24 caracteres, minúsculas e números).
STORAGE_ACCOUNT="sttfdatathon$(echo "$SUBSCRIPTION_ID" | tr -d '-' | cut -c1-10)"
CONTAINER="tfstate"

echo "Assinatura: $SUBSCRIPTION_ID"

# Assinatura nova: sem o provedor registrado, a Azure responde "SubscriptionNotFound" ao criar o storage.
if [ "$(az provider show --namespace Microsoft.Storage --query registrationState --output tsv)" != "Registered" ]; then
  echo "Registrando o provedor Microsoft.Storage (pode levar alguns minutos)..."
  az provider register --namespace Microsoft.Storage --wait
fi
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

if az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "Storage account $STORAGE_ACCOUNT já existe"
else
  echo "Criando storage account $STORAGE_ACCOUNT em $LOCATION"
  az storage account create \
    --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
    --allow-blob-public-access false --https-only true --output none
fi

# Versionamento permite recuperar versões anteriores do state (que contém a senha do Postgres).
az storage account blob-service-properties update \
  --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --enable-versioning true --output none

ACCOUNT_KEY="$(az storage account keys list --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
  --query '[0].value' --output tsv)"
az storage container create --name "$CONTAINER" \
  --account-name "$STORAGE_ACCOUNT" --account-key "$ACCOUNT_KEY" --output none

cat >terraform/backend.hcl <<EOF
subscription_id      = "$SUBSCRIPTION_ID"
resource_group_name  = "$RESOURCE_GROUP"
storage_account_name = "$STORAGE_ACCOUNT"
container_name       = "$CONTAINER"
key                  = "datathon-mab.terraform.tfstate"
EOF

echo "Pronto. Agora rode: terraform -chdir=terraform init -backend-config=backend.hcl"
