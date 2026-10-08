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
  comment    = "M0 no soporta peering/private endpoint; Cloud Run no tiene IP saliente fija sin Cloud NAT adicional."
}

resource "google_secret_manager_secret" "mongo_uri" {
  count     = var.crear_mongo_atlas ? 1 : 0
  project   = var.project_id
  secret_id = "mongo-uri-risk"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "mongo_uri" {
  count       = var.crear_mongo_atlas ? 1 : 0
  secret      = google_secret_manager_secret.mongo_uri[0].id
  secret_data = "mongodb+srv://risk-service:${random_password.mongo_atlas[0].result}@${replace(mongodbatlas_cluster.risk[0].srv_address, "mongodb+srv://", "")}/risk?retryWrites=true&w=majority"
}

resource "google_project_iam_member" "cloud_run_secretos" {
  count   = (var.crear_cloud_sql || var.crear_mongo_atlas) ? 1 : 0
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${data.google_project.actual.number}-compute@developer.gserviceaccount.com"
}
