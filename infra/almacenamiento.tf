# Almacenamiento de archivos (KAN-250, ADR-10): un bucket privado de Cloud Storage cuyos metadatos lleva el
# servicio ALMACENAMIENTO (escritor único de la base `almacenamiento`). Los clientes suben y bajan con URLs
# firmadas V4 que el servicio firma con su propia cuenta (IAM signBlob, sin llaves descargadas).
#
#   - Contenido cifrado con CMEK (llave `archivos` de kms.tf) cuando `crear_kms`; si no, la llave de Google.
#   - Retención por tipo: la regla de ciclo de vida borra `<tipo>/...` N días después de `customTime`, que el
#     servicio fija al cerrar el trámite (`POST /archivos/{id}/retencion`). N sale de contracts/tipos-archivo.json.
#   - Subidas nunca confirmadas (PENDIENTE): las borra `POST /archivos/_purgar-vencidos` a los 90 días, disparado a
#     diario por Cloud Scheduler (`purga_almacenamiento`, requiere también `crear_tareas`).
#   - Cada objeto creado o borrado avisa a ALMACENAMIENTO por Pub/Sub (push con OIDC a `/eventos/gcs`), que lo
#     valida y publica `archivo-disponible` o `archivo-rechazado` para la historia que lo pidió.
#
# Costo: Cloud Storage Standard en us-central1 (~USD 0,02/GB-mes; la capa gratuita cubre 5 GB en regiones de EE. UU.)
# y Pub/Sub en capa gratuita. Gate: var.crear_almacenamiento (default false).

locals {
  almacenamiento_activo = var.crear_almacenamiento && var.crear_servicios
  tipos_archivo         = jsondecode(file("${path.module}/../contracts/tipos-archivo.json")).tipos
  retencion_por_tipo = {
    for t in local.tipos_archivo : t.tipo => t.retencion_dias if try(t.retencion_dias, null) != null
  }
  bucket_archivos = "${var.project_id}-archivos"
}

data "google_storage_project_service_account" "gcs" {
  count   = var.crear_almacenamiento ? 1 : 0
  project = var.project_id
}

# El agente de Cloud Storage cifra y descifra con la llave `archivos` (CMEK del bucket).
resource "google_kms_crypto_key_iam_member" "cmek_archivos" {
  count         = (var.crear_almacenamiento && var.crear_kms) ? 1 : 0
  crypto_key_id = google_kms_crypto_key.archivos[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs[0].email_address}"
}

resource "google_storage_bucket" "archivos" {
  count                       = var.crear_almacenamiento ? 1 : 0
  project                     = var.project_id
  name                        = local.bucket_archivos
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Lo borrado por error se recupera durante 7 días (lo mínimo que admite la protección).
  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  dynamic "encryption" {
    for_each = var.crear_kms ? [1] : []
    content {
      default_kms_key_name = google_kms_crypto_key.archivos[0].id
    }
  }

  # El navegador sube con PUT directo a la URL firmada: solo desde los orígenes del frontend.
  cors {
    origin          = var.almacenamiento_origenes_cors
    method          = ["PUT", "GET", "HEAD"]
    response_header = ["Content-Type", "x-goog-content-length-range", "x-goog-if-generation-match"]
    max_age_seconds = 3600
  }

  dynamic "lifecycle_rule" {
    for_each = local.retencion_por_tipo
    content {
      condition {
        matches_prefix         = ["${lifecycle_rule.key}/"]
        days_since_custom_time = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }

  # Sin regla por edad: una regla `age` no distingue estados y borraría también lo DISPONIBLE sin retención (la firma
  # de una póliza emitida, HU-W03). Las subidas nunca confirmadas las borra la purga diaria (`purga_almacenamiento`).

  depends_on = [google_kms_crypto_key_iam_member.cmek_archivos]
}

# ALMACENAMIENTO es el único que escribe y borra objetos, y firma sus propias URLs (signBlob sobre sí mismo).
resource "google_storage_bucket_iam_member" "almacenamiento_objetos" {
  count  = local.almacenamiento_activo ? 1 : 0
  bucket = google_storage_bucket.archivos[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime["almacenamiento"].email}"
}

resource "google_service_account_iam_member" "almacenamiento_firma" {
  count              = local.almacenamiento_activo ? 1 : 0
  service_account_id = google_service_account.runtime["almacenamiento"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime["almacenamiento"].email}"
}

# --------------------------------------------------------------------------- avisos del bucket por Pub/Sub
resource "google_pubsub_topic" "archivos_gcs" {
  count      = var.crear_almacenamiento ? 1 : 0
  project    = var.project_id
  name       = "archivos-gcs"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic_iam_member" "gcs_publica" {
  count   = var.crear_almacenamiento ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.archivos_gcs[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs[0].email_address}"
}

resource "google_storage_notification" "archivos" {
  count          = var.crear_almacenamiento ? 1 : 0
  bucket         = google_storage_bucket.archivos[0].name
  topic          = google_pubsub_topic.archivos_gcs[0].id
  payload_format = "JSON_API_V1"
  event_types    = ["OBJECT_FINALIZE", "OBJECT_DELETE"]
  depends_on     = [google_pubsub_topic_iam_member.gcs_publica]
}

resource "google_pubsub_subscription" "almacenamiento_gcs" {
  count                = local.almacenamiento_activo ? 1 : 0
  project              = var.project_id
  name                 = "almacenamiento-gcs"
  topic                = google_pubsub_topic.archivos_gcs[0].id
  ack_deadline_seconds = 30

  push_config {
    push_endpoint = "${local.url["almacenamiento"]}/eventos/gcs"
    oidc_token {
      service_account_email = google_service_account.runtime["almacenamiento"].email
      audience              = "almacenamiento"
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# El push llega firmado con la cuenta de ALMACENAMIENTO: puede invocarse a sí mismo, y el agente de Pub/Sub
# emite ese token OIDC.
resource "google_cloud_run_v2_service_iam_member" "almacenamiento_push" {
  count    = local.almacenamiento_activo ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["almacenamiento"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["almacenamiento"].email}"
}

resource "google_service_account_iam_member" "pubsub_token_almacenamiento" {
  count              = local.almacenamiento_activo ? 1 : 0
  service_account_id = google_service_account.runtime["almacenamiento"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# --------------------------------------------------------------------------- eventos para las historias
# `archivo-disponible` y `archivo-rechazado` (contracts/events/archivo.*.schema.json). Solo ALMACENAMIENTO publica;
# cada historia que los necesite agrega aquí su suscripción.
resource "google_pubsub_topic" "archivo" {
  for_each   = var.crear_almacenamiento ? toset(["archivo-disponible", "archivo-rechazado"]) : toset([])
  project    = var.project_id
  name       = each.key
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic_iam_member" "almacenamiento_publica" {
  for_each = local.almacenamiento_activo ? google_pubsub_topic.archivo : {}
  project  = var.project_id
  topic    = each.value.name
  role     = "roles/pubsub.publisher"
  member   = "serviceAccount:${google_service_account.runtime["almacenamiento"].email}"
}

# --------------------------------------------------------------------------- purga diaria (Cloud Scheduler)
# Borra las subidas PENDIENTE de más de 90 días (y, de respaldo, lo que ya venció por retención). Reutiliza la cuenta
# `programador` de tareas.tf, que solo necesita invocar a ALMACENAMIENTO (privado). Idempotente: lo que no alcance un
# lote lo toma la pasada siguiente. Costo: un trabajo más de Cloud Scheduler (los 3 primeros por cuenta son gratis;
# del cuarto en adelante, USD 0,10 al mes cada uno).
resource "google_cloud_run_v2_service_iam_member" "programador_invoca_almacenamiento" {
  count    = (local.almacenamiento_activo && local.hay_tareas) ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["almacenamiento"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.programador[0].email}"
}

resource "google_cloud_scheduler_job" "purga_almacenamiento" {
  count       = (local.almacenamiento_activo && local.hay_tareas) ? 1 : 0
  depends_on  = [google_project_service.tareas_apis]
  project     = var.project_id
  region      = var.region
  name        = "purga-almacenamiento"
  description = "Una pasada de la purga de ALMACENAMIENTO: subidas nunca confirmadas de más de 90 días y retenciones vencidas"
  schedule    = "30 3 * * *"
  time_zone   = "America/Bogota"

  attempt_deadline = "300s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.url["almacenamiento"]}/archivos/_purgar-vencidos?limite=1000"

    oidc_token {
      service_account_email = google_service_account.programador[0].email
      audience              = local.url["almacenamiento"]
    }
  }
}
