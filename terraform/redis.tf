# Azure Managed Redis: Online Store do Feast. O Azure Cache for Redis (Basic/Standard/Premium)
# está em aposentadoria (setembro/2028), então o projeto já usa o sucessor.
# - clustering_policy EnterpriseCluster: um único endpoint, compatível com clientes Redis
#   sem suporte a cluster (o Feast usa redis_type = redis).
# - Autenticação por access key, porque o Feast conecta com "password=".
# - Sem acesso público: só pelo private endpoint na VNet.
resource "azurerm_managed_redis" "main" {
  name                      = "redis-${local.name}-${random_string.suffix.result}"
  location                  = azurerm_resource_group.main.location
  resource_group_name       = azurerm_resource_group.main.name
  sku_name                  = var.redis_sku
  high_availability_enabled = var.environment == "prod"
  public_network_access     = "Disabled"
  tags                      = local.tags

  default_database {
    clustering_policy                  = "EnterpriseCluster"
    client_protocol                    = "Encrypted"
    access_keys_authentication_enabled = true
    eviction_policy                    = "NoEviction" # features não podem sumir do cache
  }
}

resource "azurerm_private_dns_zone" "redis" {
  name                = "privatelink.redis.azure.net"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis" {
  name                  = "redis-vnet-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.redis.name
  virtual_network_id    = azurerm_virtual_network.main.id
}

resource "azurerm_private_endpoint" "redis" {
  name                = "pe-redis-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "redis"
    private_connection_resource_id = azurerm_managed_redis.main.id
    subresource_names              = ["redisEnterprise"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "redis"
    private_dns_zone_ids = [azurerm_private_dns_zone.redis.id]
  }
}
