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

variable "entorno_github_infra" {
  description = "Entorno de GitHub (Settings > Environments) que puede asumir la identidad de Terraform, con muchos más permisos que la de despliegue. Es donde se pueden exigir revisores antes de cada apply."
  type        = string
  default     = "infra-staging"
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
  description = "Crea una instancia de Cloud SQL PostgreSQL con las bases `auth`, `payments` y `policy`. NO escala a cero: cobra por hora aunque esté sin tráfico. La contraseña se genera con `random_password` y se guarda solo en Secret Manager (nunca en el repo)."
  type        = bool
  default     = false
}

variable "cloud_sql_tier" {
  description = "Tier de la instancia de Cloud SQL. `db-f1-micro` es el más pequeño/barato disponible para PostgreSQL (shared-core, ~0,6 GB RAM) — suficiente para staging del Sprint 1, no para carga real."
  type        = string
  default     = "db-f1-micro"
}

variable "redis_encendida" {
  description = "Si Redis y su conector VPC están encendidos. Es el \"apagado\" de Redis en staging (workflow `Costos staging`): apagarlo borra SOLO la instancia de Memorystore y el conector, que son lo que cobra por hora; la red, la subred y las APIs se quedan (no cuestan y borrar la red falla mientras GCP libera el conector). No tiene efecto si `crear_redis = false`."
  type        = bool
  default     = true
}

variable "instancias_fijas_encendidas" {
  description = "Si los servicios marcados `instancia_fija` en el manifiesto (los que atiende el usuario: BFF, AUTH, RISK, RATING, PAYMENTS, ACL y los stubs de proveedores) corren con 1 instancia mínima en vez de 0, para que la primera petición no espere un arranque en frío. La CPU sigue limitada a las peticiones. Cobran por hora aunque nadie las use (estimado, sin verificar: ~USD 10 al mes por instancia mínima); es el \"apagado\" de esas instancias en staging (workflow `Costos staging`). Apagadas, los servicios vuelven a mínimo 0 con CPU solo mientras atienden una petición. No tiene efecto si `crear_servicios = false`."
  type        = bool
  default     = true
}

variable "cloud_sql_encendida" {
  description = "Si la instancia de Cloud SQL está en marcha (`ALWAYS`) o DETENIDA (`NEVER`). Detenerla conserva las bases, los usuarios y las migraciones y solo deja de cobrar la instancia (queda el disco); es el \"apagado\" de staging, que se maneja con el workflow `Costos staging`. No tiene efecto si `crear_cloud_sql = false`."
  type        = bool
  default     = true
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

variable "presupuesto_moneda" {
  description = "Moneda del presupuesto (ISO 4217). Debe coincidir con la moneda de la cuenta de facturación: GCP rechaza el presupuesto si no (la cuenta del equipo está en COP)."
  type        = string
  default     = "USD"

  validation {
    condition     = can(regex("^[A-Z]{3}$", var.presupuesto_moneda))
    error_message = "presupuesto_moneda debe ser un código ISO 4217 de 3 letras mayúsculas (USD, COP...)."
  }
}

variable "presupuesto_monto" {
  description = "Monto total del presupuesto a vigilar, en presupuesto_moneda. Default: el crédito de 300 USD / 90 días del equipo; con otra moneda hay que convertirlo (ver terraform.tfvars.example)."
  type        = number
  default     = 300
}

variable "presupuesto_correos_alerta" {
  description = "Correos que reciben la alerta de presupuesto (50/80/100%), además de los administradores de facturación por defecto de GCP."
  type        = list(string)
  default     = []
}

# --------------------------------------------------------------------------------------------------
# Cifrado CMEK en almacenes (Bloque 3, HA-SEG-002)
# --------------------------------------------------------------------------------------------------

variable "usar_cmek_sql" {
  description = "Activa CMEK (`encryption_key_name`) en Cloud SQL usando la llave `campos-personales` de Cloud KMS. Como Cloud SQL solo fija CMEK al crear la instancia, se activa en un PR propio con ventana acordada de export/import."
  type        = bool
  default     = false
}

variable "usar_cmek_redis" {
  description = "Activa CMEK (`customer_managed_key`) en Memorystore Redis con la llave `campos-personales` de Cloud KMS. Se aplica en el próximo encendido de Redis."
  type        = bool
  default     = false
}

# --------------------------------------------------------------------------------------------------
# Pub/Sub (`PerfilActualizado`), Batch nocturno (`risk-reproceso`) y API Gateway (Bloques 8, 9 y 10)
# --------------------------------------------------------------------------------------------------

variable "crear_pubsub" {
  description = "Crea el tema `perfil-actualizado` y la suscripción de `rating` en Cloud Pub/Sub (decisión 9, bloque 8; capa gratuita de 10 GB/mes)."
  type        = bool
  default     = false
}

variable "crear_metricas_logs" {
  description = "Crea las métricas basadas en logs (ofertas por tipo, circuito abierto y cobros por estado) que leen los tableros de Grafana (bloque 1). Capa gratuita de Cloud Monitoring."
  type        = bool
  default     = false
}

variable "batch_tareas" {
  description = "Tareas paralelas del Cloud Run Job `risk-reproceso` (`task_count`). Cada una procesa una partición de los perfiles por el hash de `cliente_id`. 1 en staging; para 10 M de perfiles en < 2 h se proyectan ~19 (docs/evidencias/EC017-2026-10-18.md)."
  type        = number
  default     = 1
}

variable "crear_batch" {
  description = "Crea el Cloud Run Job `risk-reproceso` y la tarea nocturna de Cloud Scheduler (`30 23 * * *` America/Bogota, antes del apagado de las 2:00 a.m.) para HA-ESC-004 / EC017 (bloque 9)."
  type        = bool
  default     = false
}

variable "crear_gateway" {
  description = "Crea el API Gateway de GCP delante del BFF con timeout duro de 700 ms (`infra/gateway/openapi.yaml`) y la cuenta `gateway-bff` como único invocador del BFF (HA-LAT-004, bloque 10)."
  type        = bool
  default     = false
}

variable "calentar_camino_critico" {
  description = "Pone `min_instance_count = 1` en los servicios del camino crítico de HU-W01 solo durante las sesiones de medición para evitar arranques en frío frente al corte de 700 ms del gateway. Mantener en `false` fuera de las sesiones."
  type        = bool
  default     = false
}

variable "crear_tareas" {
  description = "Crea las colas de Cloud Tasks del reintento de cobros (`reintento-cobros`) y de la emisión de pólizas (`emision-polizas`) los trabajos de Cloud Scheduler que disparan la sonda del circuito del ACL Worker (`sonda-acl`) y la reconciliación de cobros de POLICY (`reconciliacion-cobros-policy`), y pone `DISPARADOR=externo` en los servicios que lo declaran (`disparador_externo` en .github/servicios.json). En Cloud Run los bucles de fondo no corren de forma confiable (CPU limitada fuera de una petición, escala a cero). Capa gratuita: 1 millón de operaciones de Cloud Tasks al mes y 3 trabajos de Scheduler por cuenta de facturación."
  type        = bool
  default     = false
}

variable "reconciliacion_cobros_cron" {
  description = "Cada cuánto el trabajo de Cloud Scheduler `reconciliacion-cobros-policy` llama a `POST /reconciliacion/cobros` de POLICY, que emite la póliza de las propuestas cuyo pago rechazado o pendiente ya se cobró (CA-W03-06, decisión W03-14). Formato cron, zona America/Bogota. Solo aplica con `crear_tareas = true`. Cuántas propuestas revisa cada pasada lo fija `RECONCILIACION_LOTE_MAX` de POLICY."
  type        = string
  default     = "*/15 * * * *"
}

variable "url_front" {
  description = "Origen del front web (sin barra final), p. ej. https://solventa.vercel.app. Lo usan el stream de avisos (Origin permitido), la pasarela de prueba (a dónde regresa el titular tras pagar) y POLICY (la URL del enlace de firma). El valor por defecto es el local; en staging se fija en staging.tfvars."
  type        = string
  default     = "http://localhost:5173"
}

variable "purgar_contactos_cron" {
  description = "Cada cuánto el trabajo de Cloud Scheduler `purgar-contactos-policy` llama a `POST /_purgar-contactos` de POLICY: borra nombre, correo e imagen de la firma de las propuestas cuyo consentimiento venció (W03-26). Formato cron, zona America/Bogota. Solo aplica con `crear_tareas = true`."
  type        = string
  default     = "0 3 * * *"
}

variable "crear_almacenamiento" {
  description = "Crea el bucket privado de archivos (CMEK con la llave `archivos` si crear_kms), sus avisos por Pub/Sub a ALMACENAMIENTO y los temas `archivo-disponible` y `archivo-rechazado` (KAN-250, ADR-10). Cobra por GB guardado (~USD 0,02/GB-mes)."
  type        = bool
  default     = false
}

variable "almacenamiento_origenes_cors" {
  description = "Orígenes del frontend que pueden subir y bajar archivos directo al bucket con las URLs firmadas (CORS)."
  type        = list(string)
  default     = ["http://localhost:3000", "http://localhost:5173"]
}

