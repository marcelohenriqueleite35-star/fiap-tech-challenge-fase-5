# shellcheck shell=sh
# Terraform e Azure CLI via Docker: permite fazer o deploy tendo apenas o Docker instalado.
#
# - Os dois rodam na mesma imagem (docker/tools/Dockerfile), construída na primeira execução.
# - O projeto é montado em /workspace e o diretório atual é preservado (rode a partir da raiz ou de subpastas).
# - ~/.azure é montado para reaproveitar o login (`./scripts/az.sh login --use-device-code` grava nele).
# - Se existir .env.azure (service principal), as credenciais ARM_* dele são carregadas.
# - Os containers rodam com o seu UID/GID, então .terraform/ e o lock file não ficam com dono root.
# - Variáveis ARM_*, AZURE_*, TF_VAR_* e TF_LOG* do seu shell são repassadas.
#
# Se o binário local existir, ele é usado; USE_DOCKER_TOOLS=1 força o uso do Docker.

TOOLS_IMAGE="${TOOLS_IMAGE:-datathon-mab-tools:local}"

# Sem as dicas do Docker Desktop ("What's next: ... Gordon") depois de cada erro.
export DOCKER_CLI_HINTS=false

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Credenciais de service principal (opcional): .env.azure com ARM_TENANT_ID, ARM_CLIENT_ID,
# ARM_CLIENT_SECRET e ARM_SUBSCRIPTION_ID. As variáveis ARM_* são repassadas aos containers:
# o Terraform autentica direto com elas e o deploy.sh faz o `az login --service-principal`.
if [ -f "$PROJECT_ROOT/.env.azure" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_ROOT/.env.azure"
  set +a
fi

_ensure_tools_image() {
  if ! docker image inspect "$TOOLS_IMAGE" >/dev/null 2>&1; then
    echo "Construindo a imagem de ferramentas ($TOOLS_IMAGE)..." >&2
    docker build -q -t "$TOOLS_IMAGE" "$PROJECT_ROOT/docker/tools" >&2
  fi
}

_docker_tool() {
  _ensure_tools_image

  case "$PWD/" in
    "$PROJECT_ROOT"/*) workdir="/workspace${PWD#"$PROJECT_ROOT"}" ;;
    *) workdir="/workspace" ;;
  esac

  mkdir -p "$HOME/.azure"

  # TTY só quando interativo: em pipes/$(...) um TTY inseriria \r na saída, e com TTY o stderr
  # do container sai junto com o stdout (um `2>/dev/null` deixaria de funcionar).
  tty_flag=""
  if [ -t 0 ] && [ -t 1 ] && [ -t 2 ]; then
    tty_flag="-t"
  fi

  env_flags=""
  for var in $(env | sed -n -E 's/^((ARM_|AZURE_|TF_VAR_|TF_LOG)[A-Za-z0-9_]*)=.*/\1/p'); do
    env_flags="$env_flags -e $var"
  done

  # shellcheck disable=SC2086  # tty_flag e env_flags devem ser divididos em palavras
  docker run --rm -i $tty_flag \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$HOME/.azure:/tmp/.azure" \
    -v "$PROJECT_ROOT:/workspace" \
    -w "$workdir" \
    $env_flags \
    "$TOOLS_IMAGE" "$@"
}

docker_terraform() {
  _docker_tool terraform "$@"
}

docker_az() {
  _docker_tool az "$@"
}

# Define `terraform` e `az` como funções quando não há binário local (ou quando forçado).
# shellcheck disable=SC2329  # funções usadas pelos scripts que fazem source deste arquivo
use_tools() {
  if [ "${USE_DOCKER_TOOLS:-0}" = "1" ] || ! command -v terraform >/dev/null 2>&1; then
    terraform() { docker_terraform "$@"; }
  fi
  if [ "${USE_DOCKER_TOOLS:-0}" = "1" ] || ! command -v az >/dev/null 2>&1; then
    az() { docker_az "$@"; }
  fi
}
