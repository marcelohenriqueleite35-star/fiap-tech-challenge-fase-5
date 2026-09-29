# Azure Container Registry: imagens datathon-mab-app (API + pipeline) e datathon-mab-mlflow.
# No primeiro deploy, crie só o registry (terraform apply -target=azurerm_container_registry.main),
# publique as imagens (scripts/push_images.sh) e depois rode o apply completo.
resource "azurerm_container_registry" "main" {
  name                = substr("acr${local.compact_name}${random_string.suffix.result}", 0, 50)
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false # pull via managed identity; push via Entra ID (az acr login)
  tags                = local.tags
}
