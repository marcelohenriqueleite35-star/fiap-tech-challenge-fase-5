# Imagem de ferramentas de deploy: Azure CLI + Terraform juntos, para que o provider azurerm
# autentique pelo mesmo `az login` (ele chama o binário `az` para obter tokens).
# Construída automaticamente por scripts/lib/docker_tools.sh.
FROM docker.io/hashicorp/terraform:1.16.4 AS terraform

FROM mcr.microsoft.com/azure-cli:latest

COPY --from=terraform /bin/terraform /usr/local/bin/terraform

# Extensões fora do HOME: o ~/.azure do usuário é montado por cima em tempo de execução.
ENV AZURE_EXTENSION_DIR=/opt/az-extensions
RUN az extension add --name containerapp --yes \
    && chmod -R a+rX /opt/az-extensions
