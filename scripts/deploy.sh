#!/usr/bin/env sh
# Deploy completo na Azure (passo a passo do README, seção 2.5), usando só o Docker.
#
# Uso (na raiz do projeto, depois de `./scripts/az.sh login --use-device-code`):
#   ./scripts/deploy.sh                 # todos os passos, em ordem
#   ./scripts/deploy.sh <passo> [...]   # só os passos indicados, ex.: ./scripts/deploy.sh images apply
#
# Passos: providers bootstrap tfvars init acr images apply data pipeline test   (+ update e destroy, fora do padrão)
#
# Nova versão do código num ambiente que já existe (o Terraform não troca a imagem; quem troca é o update):
#   IMAGE_TAG=v2 ./scripts/deploy.sh images update pipeline test
#
# Variáveis opcionais:
#   LOCATION=centralus    região do state e dos recursos novos no tfvars (eastus2 bloqueia Postgres em assinatura nova)
#   IMAGE_TAG=latest      tag das imagens publicadas no ACR
#   AUTO_APPROVE=1        não pede confirmação nos applies/destroy do Terraform
#   SKIP_PIPELINE_WAIT=1  dispara o pipeline sem esperar ele terminar
set -eu
# shellcheck source=scripts/lib/docker_tools.sh
. "$(dirname "$0")/lib/docker_tools.sh"
use_tools # terraform/az locais ou via Docker

cd "$PROJECT_ROOT"

LOCATION="${LOCATION:-centralus}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TF="terraform -chdir=terraform"
APPROVE=""
if [ "${AUTO_APPROVE:-0}" = "1" ]; then
  APPROVE="-auto-approve"
fi

step() { printf '\n==> %s\n' "$*"; }
fail() {
  echo "ERRO: $*" >&2
  exit 1
}
trap 'echo; echo "Interrompido." >&2; exit 130' INT

# Inicializa o Terraform (backend azurerm) se ainda não foi feito nesta máquina.
ensure_init() {
  [ -f terraform/backend.hcl ] || fail "terraform/backend.hcl não existe: nada foi implantado ainda. Rode: ./scripts/deploy.sh"
  if [ ! -f terraform/.terraform/terraform.tfstate ]; then
    do_init
  fi
}

tf_output() {
  ensure_init
  $TF output -raw "$1"
}

# Provedores de recursos usados pelo projeto (assinaturas novas vêm com todos desregistrados).
PROVIDERS="Microsoft.Storage Microsoft.Network Microsoft.ManagedIdentity Microsoft.ContainerRegistry
Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL Microsoft.Cache"

check_login() {
  # get-access-token valida o login de verdade (az account show só lê o cache local, mesmo expirado).
  az account get-access-token --output none >/dev/null 2>&1 && return
  # Sem login ativo: usa o service principal do .env.azure, se houver.
  if [ -n "${ARM_CLIENT_ID:-}" ] && [ -n "${ARM_CLIENT_SECRET:-}" ] && [ -n "${ARM_TENANT_ID:-}" ]; then
    echo "Entrando na Azure com o service principal $ARM_CLIENT_ID (.env.azure)"
    # O segredo vai para o container só como variável de ambiente (não aparece no `ps` do host).
    # shellcheck disable=SC2016  # as variáveis são expandidas dentro do container
    sp_login='az login --service-principal --username "$ARM_CLIENT_ID" --password "$ARM_CLIENT_SECRET" --tenant "$ARM_TENANT_ID" --output none'
    # `command -v` também encontra a função az() definida por use_tools; só vale um executável de verdade.
    case "$(command -v az)" in
      /*) local_az=1 ;;
      *) local_az=0 ;;
    esac
    if [ "$local_az" = 1 ] && [ "${USE_DOCKER_TOOLS:-0}" != "1" ]; then
      sh -c "$sp_login"
    else
      _docker_tool sh -c "$sp_login"
    fi || fail "login com service principal falhou (confira o .env.azure)"
    [ -z "${ARM_SUBSCRIPTION_ID:-}" ] || az account set --subscription "$ARM_SUBSCRIPTION_ID"
    return
  fi
  fail "sem login na Azure. Rode ./scripts/az.sh login --use-device-code ou preencha o .env.azure (README, seção 2.5)"
}

# 0. Registro dos provedores na assinatura (uma vez só; sem ele a Azure responde "SubscriptionNotFound")
do_providers() {
  step "0. Provedores de recursos da assinatura"
  pending=""
  for ns in $PROVIDERS; do
    state="$(az provider show --namespace "$ns" --query registrationState --output tsv)"
    if [ "$state" != "Registered" ]; then
      [ "$state" = "Registering" ] || az provider register --namespace "$ns" --output none
      pending="$pending $ns"
    fi
  done
  if [ -z "$pending" ]; then
    echo "Todos já registrados"
    return
  fi
  echo "Registrando:$pending (costuma levar de 1 a 5 min)"
  i=0
  while [ -n "$pending" ]; do
    [ "$i" -lt 60 ] || fail "tempo esgotado registrando:$pending"
    sleep 10
    still=""
    for ns in $pending; do
      state="$(az provider show --namespace "$ns" --query registrationState --output tsv)"
      [ "$state" = "Registered" ] || still="$still $ns"
    done
    pending="$still"
    echo "Pendentes:${pending:- nenhum}"
    i=$((i + 1))
  done
}

# 1. Storage Account do tfstate (uma vez só; gera terraform/backend.hcl)
do_bootstrap() {
  step "1. State do Terraform (Storage Account + backend.hcl)"
  if [ -f terraform/backend.hcl ]; then
    echo "terraform/backend.hcl já existe, pulando"
  else
    ./scripts/bootstrap_tfstate.sh "$LOCATION"
  fi
}

# 2. terraform.tfvars a partir do exemplo, com a assinatura e o seu IP público
do_tfvars() {
  step "2. Variáveis (terraform/terraform.tfvars)"
  if [ -f terraform/terraform.tfvars ]; then
    echo "terraform/terraform.tfvars já existe, mantendo"
    return
  fi
  subscription_id="$(az account show --query id --output tsv)"
  my_ip="$(curl -4 -fsS https://ifconfig.me)" || fail "não consegui descobrir seu IPv4 público (curl -4 ifconfig.me)"
  sed -e "s|00000000-0000-0000-0000-000000000000|$subscription_id|" \
    -e "s|203.0.113.10/32|$my_ip/32|" \
    -e "s|\"centralus\"|\"$LOCATION\"|" \
    -e "s|image_tag            = \"latest\"|image_tag            = \"$IMAGE_TAG\"|" \
    terraform/terraform.tfvars.example >terraform/terraform.tfvars
  echo "Gerado com subscription_id=$subscription_id e mlflow_allowed_cidrs=[$my_ip/32]"
  echo "Para habilitar o CD, preencha github_repository nele e rode de novo: ./scripts/deploy.sh apply"
}

do_init() {
  step "   terraform init"
  $TF init -input=false -backend-config=backend.hcl
}

# 3a. Primeiro só o registry: os Container Apps precisam das imagens para subir
do_acr() {
  step "3. Container Registry (apply parcial)"
  ensure_init
  # shellcheck disable=SC2086  # APPROVE vazio não deve virar argumento
  $TF apply -input=false $APPROVE -target=azurerm_container_registry.main
}

# 3b. Build e push das imagens
do_images() {
  step "3. Imagens no ACR (tag $IMAGE_TAG)"
  ./scripts/push_images.sh "$IMAGE_TAG"
}

# 4. Todo o resto (~15-25 min)
do_apply() {
  step "4. Infraestrutura completa (~15-25 min)"
  ensure_init
  # shellcheck disable=SC2086
  $TF apply -input=false $APPROVE
}

# Nova revisão da API, do MLflow e do job com as imagens da tag IMAGE_TAG (o mesmo que o CD faz)
do_update() {
  step "Atualizando os Container Apps para a tag $IMAGE_TAG"
  [ "$IMAGE_TAG" != "latest" ] || fail "use uma tag nova, ex.: IMAGE_TAG=v2 (com 'latest' a Azure não cria revisão nova)"
  rg="$(tf_output resource_group_name)"
  registry="$(tf_output acr_login_server)"
  # O nome do job é "<prefixo>-pipeline"; a API e o MLflow usam o mesmo prefixo.
  prefix="$(tf_output pipeline_job_name | sed 's/-pipeline$//')"
  az containerapp update --name "$prefix-mlflow" --resource-group "$rg" \
    --image "$registry/datathon-mab-mlflow:$IMAGE_TAG" --output none
  az containerapp update --name "$prefix-api" --resource-group "$rg" \
    --image "$registry/datathon-mab-app:$IMAGE_TAG" --output none
  az containerapp job update --name "$prefix-pipeline" --resource-group "$rg" \
    --image "$registry/datathon-mab-app:$IMAGE_TAG" --output none
  echo "API, MLflow e pipeline na tag $IMAGE_TAG"
}

# 5. Dataset no Blob Storage (o pipeline na nuvem lê de az://data/raw/)
do_data() {
  step "5. Dataset no Blob Storage"
  for f in train.csv test.csv; do
    [ -f "data/raw/$f" ] || fail "data/raw/$f não existe. Rode o pipeline local antes (README, seção 2.4)"
  done
  sa="$(tf_output storage_account_name)"
  for f in train.csv test.csv; do
    az storage blob upload --account-name "$sa" --container-name data --name "raw/$f" \
      --file "data/raw/$f" --auth-mode key --overwrite --output none
    echo "raw/$f enviado para $sa"
  done
}

# 6. Pipeline no Container Apps Job (dados -> Feast -> simulação/MLflow)
do_pipeline() {
  step "6. Pipeline na nuvem"
  rg="$(tf_output resource_group_name)"
  job="$(tf_output pipeline_job_name)"
  execution="$(az containerapp job start --name "$job" --resource-group "$rg" --query name --output tsv)"
  echo "Execução iniciada: $execution"
  echo "Logs: ./scripts/az.sh containerapp job logs show -n $job -g $rg --container pipeline --follow --format text"
  if [ "${SKIP_PIPELINE_WAIT:-0}" = "1" ]; then
    return
  fi
  i=0
  while [ "$i" -lt 90 ]; do
    status="$(az containerapp job execution show --name "$job" --resource-group "$rg" \
      --job-execution-name "$execution" --query properties.status --output tsv)"
    echo "Status: $status"
    case "$status" in
      Succeeded) return ;;
      Failed | Stopped | Degraded) fail "pipeline terminou com status $status (veja os logs acima)" ;;
    esac
    i=$((i + 1))
    sleep 20
  done
  fail "tempo esgotado (30 min) aguardando o pipeline"
}

# 7. Smoke test na API pública (sem feedback, para não alterar os contadores do bandit)
do_test() {
  step "7. Smoke test"
  api="$(tf_output api_url)"
  python3 scripts/smoke_test.py --url "$api" --wait 300 --no-feedback
  echo
  echo "API:    $api/docs"
  echo "MLflow: $(tf_output mlflow_url)"
}

do_destroy() {
  step "Destruindo o ambiente (o state em rg-datathon-mab-tfstate é mantido)"
  ensure_init
  # shellcheck disable=SC2086
  $TF destroy -input=false $APPROVE
}

run_step() {
  case "$1" in
    providers) do_providers ;;
    bootstrap) do_bootstrap ;;
    tfvars) do_tfvars ;;
    init) do_init ;;
    acr) do_acr ;;
    images) do_images ;;
    apply) do_apply ;;
    update) do_update ;;
    data) do_data ;;
    pipeline) do_pipeline ;;
    test) do_test ;;
    destroy) do_destroy ;;
    *) fail "passo desconhecido: $1 (use: providers bootstrap tfvars init acr images apply update data pipeline test destroy)" ;;
  esac
}

check_login
if [ "$#" -eq 0 ]; then
  set -- providers bootstrap tfvars init acr images apply data pipeline test
fi
for s in "$@"; do
  run_step "$s"
done
echo
echo "Concluído: $*"
