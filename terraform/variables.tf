variable "subscription_id" {
  description = "ID da assinatura Azure (az account show --query id -o tsv)"
  type        = string
}

variable "project_name" {
  description = "Prefixo dos recursos"
  type        = string
  default     = "datathon-mab"
}

variable "environment" {
  description = "Ambiente (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Região Azure (precisa oferecer Container Apps e Azure Managed Redis)"
  type        = string
  default     = "centralus"
}

variable "vnet_cidr" {
  description = "CIDR da VNet (usa um /23 e dois /24)"
  type        = string
  default     = "10.30.0.0/16"
}

# --- PostgreSQL Flexible Server ---
variable "db_name" {
  description = "Banco principal (Offline Store, registry do Feast e tabelas da aplicação)"
  type        = string
  default     = "datathon"
}

variable "db_username" {
  description = "Usuário administrador (não pode ser admin, root, postgres, azure_superuser...)"
  type        = string
  default     = "datathon"
}

variable "postgres_version" {
  type    = string
  default = "16"
}

variable "postgres_sku" {
  description = "SKU do Flexible Server (Burstable é o mais barato)"
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  type    = number
  default = 32768
}

# --- Azure Managed Redis ---
variable "redis_sku" {
  description = "SKU do Azure Managed Redis (Balanced_B0 é o menor)"
  type        = string
  default     = "Balanced_B0"
}

# --- Container Apps (combinações válidas no Consumption: 0.5/1Gi, 1/2Gi, 2/4Gi...) ---
variable "image_tag" {
  description = "Tag inicial das imagens no ACR (o CD passa a usar o SHA do commit)"
  type        = string
  default     = "latest"
}

variable "api_cpu" {
  type    = number
  default = 0.5
}

variable "api_memory" {
  type    = string
  default = "1Gi"
}

variable "api_min_replicas" {
  description = "Réplicas mínimas da API (1 evita cold start na demo)"
  type        = number
  default     = 1
}

variable "api_max_replicas" {
  type    = number
  default = 3
}

variable "mlflow_cpu" {
  type    = number
  default = 0.5
}

variable "mlflow_memory" {
  type    = string
  default = "1Gi"
}

variable "pipeline_cpu" {
  type    = number
  default = 1
}

variable "pipeline_memory" {
  type    = string
  default = "2Gi"
}

variable "mlflow_allowed_cidrs" {
  description = "CIDRs com acesso à UI do MLflow. Restrinja ao seu IP (ex.: 203.0.113.10/32)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "github_repository" {
  description = "Repositório GitHub no formato owner/repo, para a identidade OIDC do CD. Vazio = não cria."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "Retenção do Log Analytics (mínimo 30)"
  type        = number
  default     = 30
}
