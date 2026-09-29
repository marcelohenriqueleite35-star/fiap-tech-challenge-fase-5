# -----------------------------------------------------------------------------
# Datathon MAB: infraestrutura Azure
#
# - Azure Container Apps: API (ingress HTTPS público), MLflow (ingress restrito
#   por IP) e o pipeline como Container Apps Job disparado sob demanda.
# - PostgreSQL Flexible Server e Azure Managed Redis sem acesso público:
#   alcançados só de dentro da VNet (subnet delegada / private endpoint).
# - Storage Account: dados brutos e artefatos do MLflow.
# - Azure Container Registry: imagens da aplicação e do MLflow.
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.10"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }

  # State remoto num Storage Account (lock nativo via lease de blob). Os valores vêm de
  # backend.hcl, gerado por scripts/bootstrap_tfstate.sh:
  #   terraform init -backend-config=backend.hcl
  backend "azurerm" {}
}

provider "azurerm" {
  features {
    resource_group {
      # Permite `terraform destroy` mesmo que a Azure tenha criado recursos auxiliares no grupo.
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id

  # Registra os provedores usados por Container Apps e Managed Redis numa assinatura nova.
  resource_providers_to_register = ["Microsoft.App", "Microsoft.Cache", "Microsoft.OperationalInsights"]
}

locals {
  name = "${var.project_name}-${var.environment}"
  # Nomes sem hífen (ACR e Storage exigem só letras minúsculas e números).
  compact_name = replace(local.name, "-", "")
  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}

# Sufixo para nomes que precisam ser únicos globalmente (ACR, Storage, Postgres, Redis).
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}
