# Cloud SQL PostgreSQL para AUTH y PAYMENTS (escritor único por almacén, regla 1 de CLAUDE.md) —
# una instancia compartida, dos bases, mismo patrón que dev/postgres/*.sql en local.
#
# OJO costo: NO escala a cero, cobra por hora aunque no haya tráfico (~USD 9-12/mes con db-f1-micro
# en us-central1, más almacenamiento; ver infra/README.md). La contraseña se genera con
# `random_password` y vive SOLO en Secret Manager (regla 4 de CLAUDE.md: nunca en el repo).
#
# Gate: var.crear_cloud_sql (default false).

resource "google_project_service" "sql_apis" {
  for_each = var.crear_cloud_sql ? toset([
    "sqladmin.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "random_password" "postgres" {
  count   = var.crear_cloud_sql ? 1 : 0
  length  = 24
  special = false
}

resource "google_sql_database_instance" "principal" {
  count = var.crear_cloud_sql ? 1 : 0

  project          = var.project_id
  name             = "solventa-backend-postgres"
  region           = var.region
  database_version = "POSTGRES_16"

  # Staging: sin protección de borrado para poder destruir la instancia sin pasos manuales extra.
  deletion_protection = false

  settings {
    tier              = var.cloud_sql_tier
    availability_type = "ZONAL"
    disk_autoresize   = true
    disk_size         = 10 # GB, mínimo

    backup_configuration {
      enabled = false # staging: evita el costo adicional de respaldos automáticos
    }
  }

  depends_on = [google_project_service.sql_apis]
}

resource "google_sql_database" "auth" {
  count    = var.crear_cloud_sql ? 1 : 0
  project  = var.project_id
  name     = "auth"
  instance = google_sql_database_instance.principal[0].name
}

resource "google_sql_database" "payments" {
  count    = var.crear_cloud_sql ? 1 : 0
  project  = var.project_id
  name     = "payments"
  instance = google_sql_database_instance.principal[0].name
}

resource "google_sql_user" "postgres" {
  count    = var.crear_cloud_sql ? 1 : 0
  project  = var.project_id
  name     = "postgres"
  instance = google_sql_database_instance.principal[0].name
  password = random_password.postgres[0].result
}

# --- Secretos de conexión (Secret Manager, nunca en el repo) ---

resource "google_secret_manager_secret" "database_url_auth" {
  count     = var.crear_cloud_sql ? 1 : 0
  project   = var.project_id
  secret_id = "database-url-auth"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "database_url_auth" {
  count  = var.crear_cloud_sql ? 1 : 0
  secret = google_secret_manager_secret.database_url_auth[0].id
  # Socket unix de Cloud SQL montado en /cloudsql (ver cloud-run.tf: volumes + volume_mounts).
  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/auth?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

resource "google_secret_manager_secret" "database_url_payments" {
  count     = var.crear_cloud_sql ? 1 : 0
  project   = var.project_id
  secret_id = "database-url-payments"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "database_url_payments" {
  count       = var.crear_cloud_sql ? 1 : 0
  secret      = google_secret_manager_secret.database_url_payments[0].id
  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/payments?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

# --- IAM: el runtime de Cloud Run (service account default de Compute) necesita poder conectarse
# a Cloud SQL y leer estos secretos. Ver cloud-run.tf para el IAM de Secret Manager (compartido con Mongo).

data "google_project" "actual" {
  project_id = var.project_id
}

resource "google_project_iam_member" "cloud_run_sql_client" {
  count   = var.crear_cloud_sql ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${data.google_project.actual.number}-compute@developer.gserviceaccount.com"
}
