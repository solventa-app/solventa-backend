# MongoDB Atlas M0 (nivel gratuito) para RISK (escritor único, regla 1 de CLAUDE.md; RATING solo lee
# de la secundaria, nunca escribe aquí).
#
# Decisión (nueva, no estaba escrita en ningún README): Atlas M0 en vez de un replica set
# autogestionado en Compute Engine (que replicaría dev/mongo/init-replica-set.js en GCP real).
# Se eligió Atlas porque:
#   - M0 es gratis (0 USD) y ya estaba sugerido en la tabla de costos de este README.
#   - No obliga al equipo a operar Mongo (parches, failover, backups) con 0 horas de holgura.
# Se descartó la alternativa de Compute Engine (2 VMs + replica set igual que local) por ser más
# trabajo operativo para el mismo resultado, sin presupuesto para justificarlo.
#
# LIMITACIÓN CONOCIDA de M0: no soporta VPC peering ni Private Endpoint (eso es solo M10+, de pago).
# Por eso la lista de acceso de red queda abierta a 0.0.0.0/0 — la seguridad depende de
# usuario/contraseña (en Secret Manager, nunca en el repo) + TLS obligatorio de Atlas. Aceptable para
# staging del Sprint 1 sin datos reales; revisar antes de manejar datos de riesgo reales (regla 6 de
# CLAUDE.md).
#
# Gate: var.crear_mongo_atlas (default false). Requiere que YA EXISTAN la cuenta/organización de
# Atlas y sus API keys (MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY en el entorno del shell
# que corre terraform) — nadie las ha creado todavía. Ver infra/README.md.

resource "mongodbatlas_project" "risk" {
  count  = var.crear_mongo_atlas ? 1 : 0
  name   = var.mongodb_atlas_project_name
  org_id = var.mongodb_atlas_org_id
}

resource "mongodbatlas_cluster" "risk" {
  count      = var.crear_mongo_atlas ? 1 : 0
  project_id = mongodbatlas_project.risk[0].id
  name       = "solventa-risk"

  provider_name               = "TENANT"
  backing_provider_name       = "GCP"
  provider_region_name        = "CENTRAL_US" # us-central1 en nomenclatura de Atlas
  provider_instance_size_name = "M0"
}

resource "random_password" "mongo_atlas" {
  count   = var.crear_mongo_atlas ? 1 : 0
  length  = 24
  special = false
}

resource "mongodbatlas_database_user" "risk" {
  count              = var.crear_mongo_atlas ? 1 : 0
  username           = "risk-service"
  password           = random_password.mongo_atlas[0].result
  project_id         = mongodbatlas_project.risk[0].id
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = "risk"
  }
}

resource "mongodbatlas_project_ip_access_list" "abierta" {
  count      = var.crear_mongo_atlas ? 1 : 0
  project_id = mongodbatlas_project.risk[0].id
  cidr_block = "0.0.0.0/0"
  # Atlas rechaza comentarios de más de 80 caracteres (INVALID_NETWORK_PERMISSION_COMMENT).
  comment    = "M0 sin peering ni private endpoint; Cloud Run sin IP saliente fija"
}

# El contenedor del secreto `mongo-uri-risk` lo crea servicios.tf (único dueño; ver cloud-sql.tf). Aquí solo se
# agrega la versión con el valor real, y solo si los servicios existen. RATING usa su propio secreto
# (`mongo-uri-rating`, usuario de SOLO LECTURA) que también escribe Terraform (abajo). El acceso a los secretos se da por secreto
# a la cuenta de cada servicio en servicios.tf, no a nivel de proyecto.
resource "google_secret_manager_secret_version" "mongo_uri" {
  count      = (var.crear_mongo_atlas && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["mongo-uri-risk"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "mongodb+srv://risk-service:${random_password.mongo_atlas[0].result}@${replace(mongodbatlas_cluster.risk[0].srv_address, "mongodb+srv://", "")}/risk?retryWrites=true&w=majority"
}

# RATING nunca escribe en Riesgo (regla 1 de CLAUDE.md): su usuario es de SOLO LECTURA sobre la base `risk`. La URL no
# lleva readPreference porque rating la fija en el código (secundaria + sesión causal, D-02). La contraseña vive en
# Secret Manager; matiz de ADR-09: también queda en el estado de Terraform.
resource "random_password" "mongo_atlas_rating" {
  count   = var.crear_mongo_atlas ? 1 : 0
  length  = 24
  special = false
}

resource "mongodbatlas_database_user" "rating" {
  count              = var.crear_mongo_atlas ? 1 : 0
  username           = "rating-service"
  password           = random_password.mongo_atlas_rating[0].result
  project_id         = mongodbatlas_project.risk[0].id
  auth_database_name = "admin"

  roles {
    role_name     = "read"
    database_name = "risk"
  }
}

resource "google_secret_manager_secret_version" "mongo_uri_rating" {
  count      = (var.crear_mongo_atlas && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["mongo-uri-rating"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "mongodb+srv://rating-service:${random_password.mongo_atlas_rating[0].result}@${replace(mongodbatlas_cluster.risk[0].srv_address, "mongodb+srv://", "")}/risk?retryWrites=false&w=majority"
}
