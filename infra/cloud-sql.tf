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

  # CMEK del disco (HA-SEG-002): solo se puede fijar al crear la instancia. En staging se activa con
  # `usar_cmek_sql = true` en una ventana acordada (export/import de `auth`, `payments` y `policy`).
  encryption_key_name = (var.crear_kms && var.usar_cmek_sql) ? google_kms_crypto_key.campos_personales[0].id : null

  settings {
    edition           = "ENTERPRISE" # los niveles shared-core (db-f1-micro) solo existen en Enterprise, no en Enterprise Plus
    tier              = var.cloud_sql_tier
    availability_type = "ZONAL"
    # Detener la instancia (NEVER) deja de cobrar la instancia y conserva datos y migraciones; borrarla las perdería
    # (sin respaldos) y, según la documentación de Cloud SQL, su nombre no se puede reutilizar durante un tiempo.
    activation_policy = var.cloud_sql_encendida ? "ALWAYS" : "NEVER"
    disk_autoresize   = true
    disk_size         = 10 # GB, mínimo

    backup_configuration {
      enabled = false # staging: evita el costo adicional de respaldos automáticos
    }
  }

  depends_on = [
    google_project_service.sql_apis,
    google_kms_crypto_key_iam_member.cmek_sql,
  ]
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

resource "google_sql_database" "policy" {
  count    = var.crear_cloud_sql ? 1 : 0
  project  = var.project_id
  name     = "policy"
  instance = google_sql_database_instance.principal[0].name
}

resource "google_sql_database" "almacenamiento" {
  count    = var.crear_cloud_sql ? 1 : 0
  project  = var.project_id
  name     = "almacenamiento"
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
# El CONTENEDOR del secreto lo crea servicios.tf (único dueño: así activar crear_cloud_sql antes o después de
# crear_servicios no choca con el mismo secret_id). Aquí solo se agrega la versión con el valor real, y solo
# si los servicios existen. depends_on con la versión marcadora asegura que la real sea la más nueva
# ("latest" es la que lee Cloud Run).
# Socket unix de Cloud SQL: servicios.tf lo monta en /cloudsql en los servicios y jobs con `database-url-*`.

resource "google_secret_manager_secret_version" "database_url_auth" {
  count      = (var.crear_cloud_sql && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["database-url-auth"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/auth?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

resource "google_secret_manager_secret_version" "database_url_payments" {
  count      = (var.crear_cloud_sql && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["database-url-payments"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/payments?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

resource "google_secret_manager_secret_version" "database_url_policy" {
  count      = (var.crear_cloud_sql && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["database-url-policy"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/policy?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

resource "google_secret_manager_secret_version" "database_url_almacenamiento" {
  count      = (var.crear_cloud_sql && var.crear_servicios) ? 1 : 0
  secret     = google_secret_manager_secret.secreto["database-url-almacenamiento"].id
  depends_on = [google_secret_manager_secret_version.marcador]

  secret_data = "postgresql://postgres:${random_password.postgres[0].result}@/almacenamiento?host=/cloudsql/${google_sql_database_instance.principal[0].connection_name}"
}

# --- IAM: cada servicio con Postgres corre con su propia cuenta (rt-<servicio>, ver servicios.tf) y esa es la
# que necesita conectarse a Cloud SQL. El acceso a los secretos ya se da por secreto en servicios.tf.

resource "google_project_iam_member" "cloud_sql_client" {
  for_each = local.con_postgres
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
