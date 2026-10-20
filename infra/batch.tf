# Batch nocturno de reprocesamiento masivo de perfiles en RISK (HA-ESC-004, EC017; decisión 15, bloque 9).
#
# Qué crea detrás de `var.crear_batch`:
#   1. Cloud Run Job `risk-reproceso`: usa la misma imagen y cuenta (`rt-risk`) del servicio `risk`,
#      aislado del tráfico en línea (instancias propias) y con comando `python -m app.batch_reproceso`.
#   2. Cloud Scheduler `risk-reproceso-nocturno` (capa gratuita: 3 tareas/mes): dispara el job a las
#      23:30 America/Bogota (`30 23 * * *`), para que termine antes del apagado automático de costos
#      de las 2:00 a.m. (`costos-staging.yml`).
#
# Gate: var.crear_batch (default false).

resource "google_project_service" "batch_apis" {
  for_each = var.crear_batch ? toset([
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_cloud_run_v2_job" "risk_reproceso" {
  count               = (var.crear_batch && var.crear_servicios) ? 1 : 0
  project             = var.project_id
  name                = "risk-reproceso"
  location            = var.region
  deletion_protection = false

  depends_on = [
    google_project_service.batch_apis,
    google_secret_manager_secret_version.marcador,
    google_secret_manager_secret_iam_member.lectura,
  ]

  template {
    task_count = var.batch_tareas # cada tarea procesa su partición de perfiles (CLOUD_RUN_TASK_INDEX/COUNT)

    template {
      service_account = google_service_account.runtime["risk"].email
      max_retries     = 1
      timeout         = "7200s" # tope de 2 h de EC017

      dynamic "vpc_access" {
        for_each = contains(keys(local.con_redis), "risk") ? [1] : []
        content {
          connector = google_vpc_access_connector.conector[0].id
          egress    = "PRIVATE_RANGES_ONLY"
        }
      }

      containers {
        image   = var.imagen_inicial
        command = ["python"]
        args    = ["-m", "app.batch_reproceso"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        dynamic "env" {
          for_each = try(local.servicios["risk"].entorno, {})
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = try(local.servicios["risk"].secretos, {})
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secreto[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      labels,
      client,
      client_version,
    ]
  }
}

resource "google_service_account" "scheduler_batch" {
  count        = (var.crear_batch && var.crear_servicios) ? 1 : 0
  depends_on   = [google_project_service.batch_apis]
  project      = var.project_id
  account_id   = "scheduler-risk-batch"
  display_name = "Cloud Scheduler que lanza el batch nocturno risk-reproceso"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_ejecuta_batch" {
  count    = (var.crear_batch && var.crear_servicios) ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.risk_reproceso[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_batch[0].email}"
}

resource "google_cloud_scheduler_job" "risk_reproceso_nocturno" {
  count            = (var.crear_batch && var.crear_servicios) ? 1 : 0
  project          = var.project_id
  region           = var.region
  name             = "risk-reproceso-nocturno"
  description      = "Lanza el Cloud Run Job risk-reproceso fuera de horario pico (23:30 America/Bogota, antes del apagado de las 02:00)."
  schedule         = "30 23 * * *"
  time_zone        = "America/Bogota"
  attempt_deadline = "320s"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.risk_reproceso[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler_batch[0].email
    }
  }

  depends_on = [
    google_project_service.batch_apis,
    google_cloud_run_v2_job_iam_member.scheduler_ejecuta_batch,
  ]
}
