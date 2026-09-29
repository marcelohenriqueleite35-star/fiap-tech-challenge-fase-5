#!/usr/bin/env sh
# Terraform via Docker (imagem de ferramentas com az + terraform). Uso idêntico ao binário:
#   ./scripts/terraform.sh -chdir=terraform init -backend-config=backend.hcl
#   ./scripts/terraform.sh -chdir=terraform apply
set -eu
# shellcheck source=scripts/lib/docker_tools.sh
. "$(dirname "$0")/lib/docker_tools.sh"
docker_terraform "$@"
