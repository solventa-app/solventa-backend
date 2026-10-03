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
