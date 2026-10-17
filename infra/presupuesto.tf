# Protección de gasto — DISEÑO NUEVO, no portado.
#
# CLAUDE.md y el README anterior de infra/ decían que había que activar "la protección de gasto de
# ../solventa-arquitectura/proteccion-costos/" antes de cualquier despliegue real. Se verificó que
# esa carpeta NO EXISTE en ningún lugar del repo de arquitectura (ni experimentos, ni raíz): no había
# nada que copiar o adaptar. Lo que sigue es un diseño propio con el patrón estándar de GCP
# (presupuesto -> Pub/Sub -> Cloud Function), no una migración de algo ya probado.
#
# Qué hace: presupuesto de facturación con alertas en 50/80/100% del crédito (var.presupuesto_monto en
# var.presupuesto_moneda, default 300 USD; la moneda debe ser la de la cuenta de facturación) + una Cloud Function (infra/funciones/frenar-gasto/) que, al recibir la alerta del
# 100%, bloquea el tráfico de entrada de TODOS los servicios de Cloud Run del proyecto.
#
# Límites honestos de este diseño (no es una garantía absoluta, ver regla 2 de CLAUDE.md):
#   - Solo actúa sobre Cloud Run. Memorystore, Cloud SQL y Mongo Atlas (si están creados) seguirían
#     cobrando por hora hasta que alguien los destruya a mano.
#   - Las notificaciones de presupuesto de GCP no son instantáneas: puede haber gasto real entre que
#     se cruza el umbral y que la función actúa.
#   - Bloquear el tráfico no es destructivo (se revierte a mano), pero tampoco impide que sigan
#     corriendo recursos que no dependen de recibir tráfico.
#
# Gate: var.crear_presupuesto (default false). Requiere var.billing_account_id.

resource "google_project_service" "presupuesto_apis" {
  for_each = var.crear_presupuesto ? toset([
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com", # leer/asignar la política IAM de la cuenta de facturación (cicd.tf) pasa por esta API
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_pubsub_topic" "alertas_presupuesto" {
  count   = var.crear_presupuesto ? 1 : 0
  project = var.project_id
  name    = "alertas-presupuesto"

  depends_on = [google_project_service.presupuesto_apis]
}

resource "google_monitoring_notification_channel" "correo" {
  for_each     = var.crear_presupuesto ? toset(var.presupuesto_correos_alerta) : []
  project      = var.project_id
  display_name = "Alerta de presupuesto - ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
}

resource "google_billing_budget" "presupuesto" {
  provider        = google.facturacion # proyecto de cuota explícito, ver versions.tf
  count           = var.crear_presupuesto ? 1 : 0
  billing_account = var.billing_account_id
  display_name    = "solventa-backend-staging"
  depends_on      = [google_project_service.presupuesto_apis]

  budget_filter {
    # La API guarda el filtro con el NÚMERO de proyecto; con el ID, cada plan veía una diferencia.
    projects = ["projects/${data.google_project.actual.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.presupuesto_moneda
      units         = tostring(var.presupuesto_monto)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.8
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    pubsub_topic                     = google_pubsub_topic.alertas_presupuesto[0].id
    monitoring_notification_channels = [for c in google_monitoring_notification_channel.correo : c.id]
    disable_default_iam_recipients   = false
  }
}

# --- Cloud Function `frenar-gasto` (2a gen, disparada por el topic de arriba) ---

data "archive_file" "frenar_gasto" {
  count       = var.crear_presupuesto ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/funciones/frenar-gasto"
  output_path = "${path.module}/.builds/frenar-gasto.zip"
}

resource "google_storage_bucket" "fuente_funciones" {
  count   = var.crear_presupuesto ? 1 : 0
  project = var.project_id
  # Nombre único a nivel global: se prefija con el project_id.
  name                        = "${var.project_id}-solventa-fuente-funciones"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true # staging: permitir destroy sin vaciar el bucket a mano

  depends_on = [google_project_service.presupuesto_apis]
}

resource "google_storage_bucket_object" "frenar_gasto_zip" {
  count  = var.crear_presupuesto ? 1 : 0
  name   = "frenar-gasto-${data.archive_file.frenar_gasto[0].output_md5}.zip"
  bucket = google_storage_bucket.fuente_funciones[0].name
  source = data.archive_file.frenar_gasto[0].output_path
}

resource "google_service_account" "frenar_gasto" {
  count        = var.crear_presupuesto ? 1 : 0
  project      = var.project_id
  account_id   = "frenar-gasto"
  display_name = "Protección de gasto: bloquea Cloud Run al 100% del presupuesto"
}

resource "google_project_iam_member" "frenar_gasto_run_admin" {
  count   = var.crear_presupuesto ? 1 : 0
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.frenar_gasto[0].email}"
}

# En proyectos nuevos la cuenta de Compute por defecto ya no trae roles. Cloud Functions la usa para construir y
# para entregar el evento de Pub/Sub, y sin ellos el build falla ("missing permission on the build service
# account"). Se usan cuentas propias con el rol mínimo en vez de ampliar la cuenta por defecto.
resource "google_service_account" "frenar_gasto_build" {
  count        = var.crear_presupuesto ? 1 : 0
  project      = var.project_id
  account_id   = "frenar-gasto-build"
  display_name = "Build de la función frenar-gasto (Cloud Build)"
}

resource "google_project_iam_member" "frenar_gasto_build" {
  count   = var.crear_presupuesto ? 1 : 0
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${google_service_account.frenar_gasto_build[0].email}"
}

# El build lee el código desde un bucket que Cloud Functions crea y administra (no es de Terraform) y exige
# Storage Object Viewer sobre él para la cuenta de build; `cloudbuild.builds.builder` no lo cubre. Se da sobre ESE
# bucket y no sobre el proyecto: a nivel de proyecto también leería el bucket del estado de Terraform, que contiene
# secretos. El bucket nace con el primer intento de desplegar una función 2ª generación en la región: en un proyecto
# nuevo el primer `apply` falla aquí (y deja la función tainted) y el siguiente funciona.
resource "google_storage_bucket_iam_member" "frenar_gasto_build_fuente" {
  count  = var.crear_presupuesto ? 1 : 0
  bucket = "gcf-v2-sources-${data.google_project.actual.number}-${var.region}"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.frenar_gasto_build[0].email}"
}

# Recibir el evento del topic (Eventarc). run.admin ya cubre invocar la función.
resource "google_project_iam_member" "frenar_gasto_eventos" {
  count   = var.crear_presupuesto ? 1 : 0
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.frenar_gasto[0].email}"
}

resource "google_cloudfunctions2_function" "frenar_gasto" {
  count    = var.crear_presupuesto ? 1 : 0
  project  = var.project_id
  name     = "frenar-gasto"
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "frenar_gasto"
    service_account = google_service_account.frenar_gasto_build[0].id
    source {
      storage_source {
        bucket = google_storage_bucket.fuente_funciones[0].name
        object = google_storage_bucket_object.frenar_gasto_zip[0].name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 60
    environment_variables = { REGION = var.region }
    service_account_email = google_service_account.frenar_gasto[0].email
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.alertas_presupuesto[0].id
    retry_policy          = "RETRY_POLICY_DO_NOT_RETRY"
    service_account_email = google_service_account.frenar_gasto[0].email
  }

  depends_on = [
    google_project_service.presupuesto_apis,
    google_project_iam_member.frenar_gasto_build,
    google_storage_bucket_iam_member.frenar_gasto_build_fuente,
    google_project_iam_member.frenar_gasto_eventos,
  ]
}
