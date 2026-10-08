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

# --------------------------------------------------------------------------------------------------
# Memorystore (Redis) + conector VPC — compartido por acl-worker/rating/consolidador-fuentes.
# No escala a cero: ~USD 0,12/h juntos (Redis BASIC 1GB + conector). Crear y destruir por sesión.
# --------------------------------------------------------------------------------------------------

variable "crear_redis" {
  description = "Crea Memorystore (Redis BASIC) + el conector VPC que usan acl-worker, rating y consolidador-fuentes. NO escala a cero: ~USD 0,12/h juntos. Crear solo para la sesión de pruebas y destruir al terminar."
  type        = bool
  default     = false
}

variable "redis_memory_size_gb" {
  description = "Tamaño de la instancia de Memorystore Redis, en GB. 1 es el mínimo soportado por el tier BASIC."
  type        = number
  default     = 1
}

variable "vpc_connector_rango" {
  description = "Rango CIDR /28 dedicado al conector VPC de Serverless (Redis). No debe solaparse con nada más en el proyecto."
  type        = string
  default     = "10.8.0.0/28"
}

# --------------------------------------------------------------------------------------------------
# Cloud SQL PostgreSQL — AUTH y PAYMENTS (una instancia, dos bases). Cobra aunque no haya tráfico.
# --------------------------------------------------------------------------------------------------

variable "crear_cloud_sql" {
  description = "Crea una instancia de Cloud SQL PostgreSQL con las bases `auth` y `payments`. NO escala a cero: cobra por hora aunque esté sin tráfico. La contraseña se genera con `random_password` y se guarda solo en Secret Manager (nunca en el repo)."
  type        = bool
  default     = false
}

variable "cloud_sql_tier" {
  description = "Tier de la instancia de Cloud SQL. `db-f1-micro` es el más pequeño/barato disponible para PostgreSQL (shared-core, ~0,6 GB RAM) — suficiente para staging del Sprint 1, no para carga real."
  type        = string
  default     = "db-f1-micro"
}

# --------------------------------------------------------------------------------------------------
# MongoDB Atlas (RISK). Opt-in porque requiere una cuenta/organización de Atlas que HOY no existe.
# Decisión documentada en infra/README.md: Atlas M0 (gratis) en vez de un replica set autogestionado
# en Compute Engine, para no operar nosotros mismos parches/backups/failover de Mongo.
# --------------------------------------------------------------------------------------------------

variable "crear_mongo_atlas" {
  description = "Crea el proyecto/cluster M0 (gratis) de MongoDB Atlas para RISK. Requiere que YA EXISTAN la cuenta/organización de Atlas y sus API keys (MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY en el entorno, nunca en tfvars) — ver infra/README.md. Nadie ha confirmado esa cuenta todavía."
  type        = bool
  default     = false
}

variable "mongodb_atlas_org_id" {
  description = "ID de la organización de Atlas donde crear el proyecto de RISK. Obligatorio solo si crear_mongo_atlas = true."
  type        = string
  default     = ""
}

variable "mongodb_atlas_project_name" {
  description = "Nombre del proyecto de Atlas a crear."
  type        = string
  default     = "solventa-backend"
}

# --------------------------------------------------------------------------------------------------
# Protección de gasto — diseño NUEVO (no existe nada en ../solventa-arquitectura/proteccion-costos/
# para copiar: se verificó que esa carpeta no existe). Presupuesto + Pub/Sub + Cloud Function que
# bloquea el tráfico de Cloud Run al 100%. No es una garantía absoluta de gasto cero.
# --------------------------------------------------------------------------------------------------

variable "crear_presupuesto" {
  description = "Crea el presupuesto de facturación (alertas 50/80/100%) y la función `frenar-gasto` que bloquea el tráfico de los 10 servicios de Cloud Run cuando se notifica el 100%. Requiere billing_account_id. Debe estar activo ANTES de cualquier despliegue real (regla 2 de CLAUDE.md)."
  type        = bool
  default     = false
}

variable "billing_account_id" {
  description = "ID de la cuenta de facturación del equipo (formato XXXXXX-XXXXXX-XXXXXX). Obligatorio solo si crear_presupuesto = true."
  type        = string
  default     = ""
}

variable "presupuesto_monto_usd" {
  description = "Monto total del presupuesto a vigilar, en USD. Default: el crédito de USD 300 / 90 días del equipo."
  type        = number
  default     = 300
}

variable "presupuesto_correos_alerta" {
  description = "Correos que reciben la alerta de presupuesto (50/80/100%), además de los administradores de facturación por defecto de GCP."
  type        = list(string)
  default     = []
}
