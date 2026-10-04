variable "project_id" {
  description = "ID del proyecto de GCP donde se despliega (staging)."
  type        = string
}

variable "region" {
  description = "Región única de despliegue (ADR-04: una sola región)."
  type        = string
  default     = "us-central1"
}

variable "repository_id" {
  description = "Nombre del repositorio de Artifact Registry con las imágenes de los servicios."
  type        = string
  default     = "solventa-backend"
}

variable "crear_kms" {
  description = "Crea el key ring y la llave de Cloud KMS para el cifrado de campo (D-04). OJO: un key ring NO se puede borrar."
  type        = bool
  default     = false
}

variable "crear_pipeline" {
  description = "Crea la identidad del pipeline de despliegue: Workload Identity Federation para GitHub Actions y su service account (sin llaves). No tiene costo."
  type        = bool
  default     = false
}

variable "crear_servicios" {
  description = "Crea un Cloud Run service por servicio de .github/servicios.json, un Cloud Run Job de migración por servicio con base, sus service accounts y los secretos. Escalan a cero: ~USD 0 en reposo (ver infra/README.md)."
  type        = bool
  default     = false
}

variable "repositorio_github" {
  description = "Repositorio de GitHub autorizado a desplegar (formato organización/repositorio)."
  type        = string
  default     = "solventa-app/solventa-backend"
}

variable "entorno_github" {
  description = "Entorno de GitHub (Settings > Environments) que puede asumir la identidad de despliegue."
  type        = string
  default     = "staging"
}

variable "imagen_inicial" {
  description = "Imagen con la que se crean los servicios la primera vez; el pipeline la reemplaza por la del commit (Terraform ignora el cambio de imagen)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "max_instancias" {
  description = "Tope de instancias por servicio de Cloud Run: limita el gasto ante una ráfaga o un bucle de llamadas."
  type        = number
  default     = 2
}
