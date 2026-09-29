output "api_url" {
  description = "URL pública da API (Swagger em /docs)"
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "mlflow_url" {
  description = "UI do MLflow (liberada só para mlflow_allowed_cidrs)"
  value       = "https://${azurerm_container_app.mlflow.ingress[0].fqdn}"
}

output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "storage_account_name" {
  value = azurerm_storage_account.main.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "redis_hostname" {
  value = azurerm_managed_redis.main.hostname
}

output "pipeline_job_name" {
  value = azurerm_container_app_job.pipeline.name
}

output "log_analytics_workspace_id" {
  description = "Workspace ID para consultar logs (az monitor log-analytics query -w ...)"
  value       = azurerm_log_analytics_workspace.main.workspace_id
}

output "run_pipeline_command" {
  description = "Dispara o pipeline (dados -> Feast -> simulação/MLflow) no Container Apps Job"
  value       = "az containerapp job start --name ${azurerm_container_app_job.pipeline.name} --resource-group ${azurerm_resource_group.main.name}"
}

# --- Valores para o environment "production" do GitHub (CD) ---
output "github_azure_client_id" {
  description = "Variável AZURE_CLIENT_ID do environment production"
  value       = one(azurerm_user_assigned_identity.github[*].client_id)
}

output "github_azure_tenant_id" {
  description = "Variável AZURE_TENANT_ID do environment production"
  value       = azurerm_user_assigned_identity.apps.tenant_id
}

output "github_azure_subscription_id" {
  description = "Variável AZURE_SUBSCRIPTION_ID do environment production"
  value       = var.subscription_id
}
