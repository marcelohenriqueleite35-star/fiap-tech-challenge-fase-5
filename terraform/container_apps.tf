# Azure Container Apps: API e MLflow como apps; pipeline como Job disparado sob demanda.
# A mesma imagem roda local (docker-compose) e aqui, só trocando as variáveis de ambiente.

locals {
  app_image    = "${azurerm_container_registry.main.login_server}/datathon-mab-app:${var.image_tag}"
  mlflow_image = "${azurerm_container_registry.main.login_server}/datathon-mab-mlflow:${var.image_tag}"
  mlflow_app   = "${local.name}-mlflow"

  common_env = {
    POSTGRES_HOST    = azurerm_postgresql_flexible_server.main.fqdn
    POSTGRES_PORT    = "5432"
    POSTGRES_DB      = var.db_name
    POSTGRES_USER    = var.db_username
    POSTGRES_SSLMODE = "require"
    # Dentro do ambiente, os apps se encontram pelo nome (porta 80 -> target_port).
    MLFLOW_TRACKING_URI = "http://${local.mlflow_app}"
    FEAST_REPO_PATH     = "/app/feature_store"
  }

  # Variáveis de ambiente -> nome do secret do Container App
  common_secret_env = {
    POSTGRES_PASSWORD       = "postgres-password"
    REDIS_CONNECTION_STRING = "redis-connection-string"
  }

  # for_each não aceita valores sensíveis: itera pelos nomes e busca o valor.
  secret_names = ["postgres-password", "redis-connection-string", "storage-connection-string"]
  secrets = {
    postgres-password = random_password.db.result
    # Formato do Feast: host:porta,ssl=true,password=<chave>
    redis-connection-string = join(",", [
      "${azurerm_managed_redis.main.hostname}:${azurerm_managed_redis.main.default_database[0].port}",
      "ssl=true",
      "password=${azurerm_managed_redis.main.default_database[0].primary_access_key}",
    ])
    storage-connection-string = azurerm_storage_account.main.primary_connection_string
  }

  # A subnet do ambiente entra na allowlist para o pipeline/API falarem com o MLflow internamente.
  mlflow_allowed_ranges = concat(var.mlflow_allowed_cidrs, azurerm_subnet.aca.address_prefixes)
}

resource "azurerm_container_app_environment" "main" {
  name                           = "cae-${local.name}"
  location                       = azurerm_resource_group.main.location
  resource_group_name            = azurerm_resource_group.main.name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id       = azurerm_subnet.aca.id
  internal_load_balancer_enabled = false
  tags                           = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  lifecycle {
    # A Azure cria e nomeia o resource group de infraestrutura do ambiente.
    ignore_changes = [infrastructure_resource_group_name]
  }
}

# --- Identidade usada pelos apps para puxar imagens do ACR ---
resource "azurerm_user_assigned_identity" "apps" {
  name                = "id-${local.name}-apps"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "apps_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

# Atribuições de papel levam alguns segundos para propagar; sem a espera, o primeiro pull falha.
resource "time_sleep" "acr_pull_propagation" {
  depends_on      = [azurerm_role_assignment.apps_acr_pull]
  create_duration = "60s"
}

# --- MLflow Tracking Server ---
resource "azurerm_container_app" "mlflow" {
  name                         = local.mlflow_app
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.secret_names
    content {
      name  = secret.value
      value = local.secrets[secret.value]
    }
  }

  ingress {
    external_enabled           = true
    target_port                = 5000
    transport                  = "auto"
    allow_insecure_connections = true # chamadas internas em http://<app>

    dynamic "ip_security_restriction" {
      for_each = local.mlflow_allowed_ranges
      content {
        name             = "allow-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "mlflow"
      image  = local.mlflow_image
      cpu    = var.mlflow_cpu
      memory = var.mlflow_memory

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "MLFLOW_ARTIFACTS_DESTINATION"
        value = "wasbs://${azurerm_storage_container.this["mlflow"].name}@${azurerm_storage_account.main.name}.blob.core.windows.net/artifacts"
      }
      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-password"
      }
      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection-string"
      }

      liveness_probe {
        transport        = "HTTP"
        port             = 5000
        path             = "/health"
        initial_delay    = 30
        interval_seconds = 15
      }
    }
  }

  lifecycle {
    # O CD (GitHub Actions) troca a imagem a cada deploy.
    ignore_changes = [template[0].container[0].image]
  }

  depends_on = [time_sleep.acr_pull_propagation]
}

# --- API FastAPI ---
resource "azurerm_container_app" "api" {
  name                         = "${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.secret_names
    content {
      name  = secret.value
      value = local.secrets[secret.value]
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    http_scale_rule {
      name                = "http-concurrency"
      concurrent_requests = "50"
    }

    container {
      name   = "api"
      image  = local.app_image
      cpu    = var.api_cpu
      memory = var.api_memory

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.common_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      liveness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/health"
        initial_delay    = 30
        interval_seconds = 15
      }

      readiness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/health"
        interval_seconds = 10
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  depends_on = [time_sleep.acr_pull_propagation]
}

# --- Pipeline (ingestão -> Feast materialize -> simulação/MLflow), disparado sob demanda ---
resource "azurerm_container_app_job" "pipeline" {
  name                         = "${local.name}-pipeline"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.secret_names
    content {
      name  = secret.value
      value = local.secrets[secret.value]
    }
  }

  template {
    container {
      name    = "pipeline"
      image   = local.app_image
      cpu     = var.pipeline_cpu
      memory  = var.pipeline_memory
      command = ["sh", "scripts/run_pipeline.sh"]

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.common_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      env {
        name  = "DATA_PATH"
        value = "az://${azurerm_storage_container.this["data"].name}/raw/train.csv"
      }
      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection-string"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  depends_on = [time_sleep.acr_pull_propagation]
}
