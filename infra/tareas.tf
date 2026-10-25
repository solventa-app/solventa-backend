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
#   - Reconciliación de fuentes degradadas (W01): Cloud Tasks. El ACL Worker encola una tarea HTTP hacia
#     `POST /reconciliar` del consolidador (en lugar de la cola `rq`, cuyo trabajador es un hilo de fondo), y la
#     cola reintenta con backoff mientras el consolidador responda 503 (la fuente sigue degradada).
#
#   - Emisión de la póliza (W03-6): Cloud Tasks. Al firmar, POLICY encola una tarea HTTP hacia su propio
#     `POST /propuestas/{id}/_emitir` (decisión de UNDER, primer cobro con PAYMENTS y emisión), y la vuelve a encolar
#     mientras el primer cobro siga pendiente. La cola reintenta con backoff si UNDER o PAYMENTS no responden (503).
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

# ------------------------------------------------------------------ emisión de pólizas (Cloud Tasks)
resource "google_cloud_tasks_queue" "emision_polizas" {
  count      = local.hay_tareas ? 1 : 0
  depends_on = [google_project_service.tareas_apis]
  project    = var.project_id
  name       = "emision-polizas"
  location   = var.region

  # Cada tarea es independiente (la emisión es idempotente y la orden de cobro lleva un identificador fijo),
  # así que puede haber varias a la vez.
  rate_limits {
    max_dispatches_per_second = 5
    max_concurrent_dispatches = 5
  }

  # Si UNDER o PAYMENTS no responden, POLICY contesta 503 y Cloud Tasks reintenta unas veces. Agotados, la
  # propuesta queda EN_EMISION y volver a firmarla (idempotente) la vuelve a encolar.
  retry_config {
    max_attempts  = 5
    min_backoff   = "5s"
    max_backoff   = "60s"
    max_doublings = 3
  }
}

# POLICY puede crear tareas en esa cola...
resource "google_cloud_tasks_queue_iam_member" "policy_encola" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.emision_polizas[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.runtime["policy"].email}"
}

# ...firmadas con su propia identidad (para eso necesita poder «actuar como» ella misma)...
resource "google_service_account_iam_member" "policy_firma_tareas" {
  count              = local.hay_tareas ? 1 : 0
  service_account_id = google_service_account.runtime["policy"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime["policy"].email}"
}

# ...y esa identidad puede invocar a POLICY (el servicio es privado).
resource "google_cloud_run_v2_service_iam_member" "policy_se_invoca" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["policy"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["policy"].email}"
}

# --------------------------------------------------------------- reconciliación de fuentes (Cloud Tasks)
resource "google_cloud_tasks_queue" "reconciliacion_fuentes" {
  count      = local.hay_tareas ? 1 : 0
  depends_on = [google_project_service.tareas_apis]
  project    = var.project_id
  name       = "reconciliacion-fuentes"
  location   = var.region

  rate_limits {
    max_dispatches_per_second = 2
    max_concurrent_dispatches = 2
  }

  # Mismo backoff que el `rq.Retry` original: 5 intentos, de 10 s a 300 s. Agotados, la tarea se descarta.
  retry_config {
    max_attempts  = 5
    min_backoff   = "10s"
    max_backoff   = "300s"
    max_doublings = 5
  }
}

# El ACL Worker crea las tareas...
resource "google_cloud_tasks_queue_iam_member" "acl_encola_reconciliacion" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.reconciliacion_fuentes[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.runtime["acl-worker"].email}"
}

# ...firmadas con su propia identidad (la que ya puede invocar al consolidador por `llama` en el manifiesto).
resource "google_service_account_iam_member" "acl_firma_tareas" {
  count              = local.hay_tareas ? 1 : 0
  service_account_id = google_service_account.runtime["acl-worker"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime["acl-worker"].email}"
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

# ------------------------------------------------------------------ reconciliación de cobros de las pólizas (Cloud Scheduler)
# Decisión W03-14 (CA-W03-06): un pago rechazado o pendiente que se cobra después reabre la póliza. Lo disparan el
# back-office (el BFF avisa a POLICY tras `reintentarCobro`) y este trabajo, que llama al barrido de POLICY cada
# `reconciliacion_cobros_cron`. Reutiliza la cuenta `programador` (la de la sonda); solo necesita poder invocar a POLICY,
# que es privado. El barrido es idempotente: una pasada que no termina la retoma la siguiente.
resource "google_cloud_run_v2_service_iam_member" "programador_invoca_policy" {
  count    = local.hay_tareas ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["policy"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.programador[0].email}"
}

resource "google_cloud_scheduler_job" "reconciliacion_cobros_policy" {
  count       = local.hay_tareas ? 1 : 0
  depends_on  = [google_project_service.tareas_apis]
  project     = var.project_id
  region      = var.region
  name        = "reconciliacion-cobros-policy"
  description = "Una pasada de la reconciliación de cobros de POLICY: emite la póliza de las propuestas cuyo pago rechazado o pendiente ya se cobró"
  schedule    = var.reconciliacion_cobros_cron
  time_zone   = "America/Bogota"

  # El barrido recorre un lote en serie y cada propuesta consulta a PAYMENTS: con margen sobre una pasada normal.
  attempt_deadline = "300s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.url["policy"]}/reconciliacion/cobros"

    oidc_token {
      service_account_email = google_service_account.programador[0].email
      audience              = local.url["policy"]
    }
  }
}
