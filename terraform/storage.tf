# Storage Account: dataset bruto (container "data") e artefatos do MLflow (container "mlflow").
resource "azurerm_storage_account" "main" {
  name                            = substr("st${local.compact_name}${random_string.suffix.result}", 0, 24)
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "this" {
  for_each              = toset(["data", "mlflow"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}
