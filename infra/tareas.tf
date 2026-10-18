# Disparadores externos del trabajo en segundo plano (decisión W05-7, plan de HU-W05, bloque 0).
#
# En Cloud Run (`cpu_idle = true`, mínimo 0 instancias) un bucle `asyncio` no corre de forma confiable: la CPU se
# limita fuera de una petición y el servicio escala a cero. Por eso la lógica de fondo se dispara desde fuera:
#
#   - Reintento de cobros pendientes (CA-W05-05): Cloud Tasks. PAYMENTS programa una tarea HTTP hacia su propio
#     `POST /cobros/_reintentar` cuando un cobro queda `pendiente`, y cada pasada programa la siguiente mientras
#     quede alguno. La tarea se autentica con un token OIDC de la cuenta de runtime de PAYMENTS.
#   - Sonda de recuperación del circuito (regla de arquitectura #5): Cloud Scheduler cada minuto llama a
#     `POST /sonda/ejecutar` del ACL Worker.
#
# Pendiente (mismo patrón, otro PR): la reconciliación de fuentes degradadas del consolidador.
#
# Solo `variables de entorno` + estos recursos: el código de los servicios no cambia entre local (bucle) y nube.
# Gate: var.crear_tareas (default false) y var.crear_servicios.
#
# Costo: dentro de la capa gratuita (Cloud Tasks 1 M de operaciones al mes; Cloud Scheduler 3 trabajos gratis por
# cuenta de facturación). Una llamada por minuto al ACL Worker son ~43.000 peticiones al mes, de unos milisegundos.

locals {
  # Servicios que declaran `"disparador_externo": true` en el manifiesto. Solo existen con la bandera encendida.
  con_disparador_externo = {
    for n, s in local.activos : n => s if var.crear_tareas && try(s.disparador_externo, false)
  }
  hay_tareas = var.crear_tareas && var.crear_servicios
}

resource "google_project_service" "tareas_apis" {
  for_each = local.hay_tareas ? toset([
    "cloudtasks.googleapis.com",
    "cloudscheduler.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------------------- reintento de cobros (Cloud Tasks)
resource "google_cloud_tasks_queue" "reintento_cobros" {
  count      = local.hay_tareas ? 1 : 0
  depends_on = [google_project_service.tareas_apis]
  project    = var.project_id
  name       = "reintento-cobros"
  location   = var.region

  # Una pasada a la vez: cada una concilia con la pasarela antes de cobrar, así que no hay doble cobro, pero
  # tampoco hace falta paralelismo.
  rate_limits {
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 1
  }

  # Si la llamada a PAYMENTS falla (arranque en frío, error), Cloud Tasks reintenta unas veces; la pasada es
  # idempotente.
  retry_config {
    max_attempts = 3
    min_backoff  = "10s"
    max_backoff  = "60s"
  }
}

# PAYMENTS puede crear tareas en esa cola...
resource "google_cloud_tasks_queue_iam_member" "payments_encola" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.reintento_cobros[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.runtime["payments"].email}"
}

# ...firmadas con su propia identidad (para eso necesita poder «actuar como» ella misma)...
resource "google_service_account_iam_member" "payments_firma_tareas" {
  count              = local.hay_tareas ? 1 : 0
  service_account_id = google_service_account.runtime["payments"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime["payments"].email}"
}

# ...y esa identidad puede invocar a PAYMENTS (el servicio es privado).
resource "google_cloud_run_v2_service_iam_member" "payments_se_invoca" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["payments"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["payments"].email}"
}

# ------------------------------------------------------------------ sonda del circuito (Cloud Scheduler)
resource "google_service_account" "programador" {
  count        = local.hay_tareas ? 1 : 0
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "sched-sonda"
  display_name = "Cloud Scheduler: sonda del circuito del ACL Worker"
}

resource "google_cloud_run_v2_service_iam_member" "programador_invoca_acl" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["acl-worker"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.programador[0].email}"
}

resource "google_cloud_scheduler_job" "sonda_acl" {
  count       = local.hay_tareas ? 1 : 0
  depends_on  = [google_project_service.tareas_apis]
  project     = var.project_id
  region      = var.region
  name        = "sonda-acl"
  description = "Una pasada de la sonda de recuperación del circuito del ACL Worker (fuera del camino del usuario)"
  schedule    = "* * * * *"
  time_zone   = "America/Bogota"

  attempt_deadline = "30s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.url["acl-worker"]}/sonda/ejecutar"

    oidc_token {
      service_account_email = google_service_account.programador[0].email
      audience              = local.url["acl-worker"]
    }
  }
}
