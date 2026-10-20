# Cifrado en reposo con Cloud KMS (HA-SEG-002, D-04 cerrada en bloque 3):
#   1. Cifrado de sobre (envelope encryption) en aplicación para campos personales: AUTH y RISK cifran
#      con AES-256-GCM y RATING descifra al leer de la réplica. Solo las cuentas de runtime de `auth`,
#      `risk` y `rating` reciben `roles/cloudkms.cryptoKeyEncrypterDecrypter` sobre `campos-personales`.
#   2. CMEK opcional en Cloud SQL (`usar_cmek_sql`) y Memorystore Redis (`usar_cmek_redis`) con la
#      misma llave. MongoDB Atlas se queda en M0 sin CMEK (cubierto por el cifrado de campo).
#
# OJO: un key ring de Cloud KMS NO se puede borrar nunca.

locals {
  # Mínimo privilegio (bloque 3): solo AUTH (escribe/lee), RISK (escribe) y RATING (lee) tocan la llave.
  servicios_con_kms = (var.crear_kms && var.crear_servicios) ? toset(["auth", "risk", "rating"]) : toset([])
}

resource "google_project_service" "kms" {
  count              = var.crear_kms ? 1 : 0
  project            = var.project_id
  service            = "cloudkms.googleapis.com"
  disable_on_destroy = false
}

resource "google_kms_key_ring" "solventa" {
  count      = var.crear_kms ? 1 : 0
  depends_on = [google_project_service.kms]
  project    = var.project_id
  name       = "solventa-backend"
  location   = var.region
}

resource "google_kms_crypto_key" "campos_personales" {
  count    = var.crear_kms ? 1 : 0
  name     = "campos-personales"
  key_ring = google_kms_key_ring.solventa[0].id

  rotation_period = "7776000s" # 90 días

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "cifrado_servicios" {
  for_each      = local.servicios_con_kms
  crypto_key_id = google_kms_crypto_key.campos_personales[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

# Agentes de servicio de GCP para CMEK de disco en Cloud SQL y Memorystore Redis (cuando se activan).
resource "google_kms_crypto_key_iam_member" "cmek_sql" {
  count         = (var.crear_kms && var.crear_cloud_sql && var.usar_cmek_sql) ? 1 : 0
  crypto_key_id = google_kms_crypto_key.campos_personales[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
}

resource "google_kms_crypto_key_iam_member" "cmek_redis" {
  count         = (var.crear_kms && var.crear_redis && var.usar_cmek_redis) ? 1 : 0
  crypto_key_id = google_kms_crypto_key.campos_personales[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.actual.number}@cloud-redis.iam.gserviceaccount.com"
}
