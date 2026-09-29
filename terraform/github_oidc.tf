# Identidade assumida pelo GitHub Actions via OIDC (federated credential, sem segredos no GitHub).
# Criada apenas quando var.github_repository ("owner/repo") é informado.
locals {
  github_enabled = var.github_repository != ""
}

resource "azurerm_user_assigned_identity" "github" {
  count               = local.github_enabled ? 1 : 0
  name                = "id-${local.name}-github"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# Apenas jobs no environment "production" deste repositório (que pode exigir aprovação) obtêm token.
resource "azurerm_federated_identity_credential" "github" {
  count                     = local.github_enabled ? 1 : 0
  name                      = "github-production"
  user_assigned_identity_id = azurerm_user_assigned_identity.github[0].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:${var.github_repository}:environment:production"
}

# Push de imagens no ACR.
resource "azurerm_role_assignment" "github_acr_push" {
  count                = local.github_enabled ? 1 : 0
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.github[0].principal_id
}

# Atualizar Container Apps, disparar o Job e ler o FQDN, restrito a este resource group.
resource "azurerm_role_assignment" "github_rg_contributor" {
  count                = local.github_enabled ? 1 : 0
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.github[0].principal_id
}
