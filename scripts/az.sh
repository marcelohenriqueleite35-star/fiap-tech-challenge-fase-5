#!/usr/bin/env sh
# Azure CLI via Docker (imagem de ferramentas com az + terraform). Uso idêntico ao binário:
#   ./scripts/az.sh login --use-device-code
#   ./scripts/az.sh account show
set -eu
# shellcheck source=scripts/lib/docker_tools.sh
. "$(dirname "$0")/lib/docker_tools.sh"
docker_az "$@"
