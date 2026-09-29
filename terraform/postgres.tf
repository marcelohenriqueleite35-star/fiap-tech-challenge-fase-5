# PostgreSQL Flexible Server: Offline Store do Feast, registry do Feast, tabelas do bandit e
# backend do MLflow (o banco "mlflow" é criado pelo entrypoint do container do MLflow).
# Acesso privado: sem endpoint público, resolvido por uma zona DNS privada ligada à VNet.
resource "random_password" "db" {
  length      = 32
  special     = false # a senha entra em URLs de conexão (SQLAlchemy/Feast)
  min_upper   = 2
  min_lower   = 2
  min_numeric = 2
}

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "postgres-vnet-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-${local.name}-${random_string.suffix.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  version             = var.postgres_version
  sku_name            = var.postgres_sku
  storage_mb          = var.postgres_storage_mb
  auto_grow_enabled   = true

  administrator_login    = var.db_username
  administrator_password = random_password.db.result

  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  backup_retention_days = 7
  tags                  = local.tags

  lifecycle {
    # A Azure escolhe a zona de disponibilidade; não recriar o servidor por causa disso.
    ignore_changes = [zone]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
