# Notificaciones push e ingesta paramétrica (KAN-259 / HU-M04, Fase 7 del plan de
# docs: `plan-notificaciones-push-fcm.md`, sección D). Esta Fase es SOLO infraestructura: el código
# de `enviar-push` (Fase 2), el adaptador del ACL Worker (Fase 3) y el consumidor de CLAIMS (Fase 4)
# ya existen en el repo y no se tocan aquí.
#
# Qué crea detrás de `var.crear_push` (default false; ver variables.tf):
#
#   1. Tema `notificaciones-push` (D2, ya aprobado) + suscripción push `enviar-push-notificaciones`
#      hacia `POST /eventos` de `enviar-push`. Publican: CLAIMS (productor real de
#      `notificacion.push-solicitada` desde la Fase 4) y el propio `enviar-push` (cuando alguien le
#      llama `POST /v1/push` directo y el servicio reencola en el tema antes de procesar). Consume:
#      `enviar-push`.
#
#   2. Tema `eventos-parametricos` (Fase 3) CON Dead Letter Queue (a diferencia de `perfil-actualizado`
#      en pubsub.tf, que no la tiene: aquí sí la pide el plan porque el proveedor externo puede fallar
#      y el evento no se puede perder). Suscripción push `claims-eventos-parametricos` hacia
#      `POST /eventos/parametricos` de CLAIMS, con `dead_letter_policy` (5 intentos) hacia el tema
#      `eventos-parametricos-dlq`. Publica únicamente el ACL Worker (escritor único del evento, regla 2
#      de CLAUDE.md); consume únicamente CLAIMS.
#
#      Referencia oficial de `dead_letter_policy`: https://cloud.google.com/pubsub/docs/handling-failures#dead_letter_topic
#      y https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/pubsub_subscription#dead_letter_policy
#      El agente de servicio de Pub/Sub (`service-<N>@gcp-sa-pubsub.iam.gserviceaccount.com`) necesita
#      `roles/pubsub.publisher` en el tema DLQ y `roles/pubsub.subscriber` en la suscripción original
#      para poder reenviar los mensajes agotados; sin esos dos permisos Pub/Sub no puede completar el
#      reenvío y los mensajes quedan atascados en la suscripción original.
#
#   3. Dos `google_cloud_scheduler_job` DE DEMOSTRACIÓN (sección G del plan) que llaman cada 8 horas (≈90 consultas/mes: cabe en el plan gratis de AviationStack; clima 1 min después para que los dos pushes no lleguen juntos)
#      a `POST /eventos-parametricos/_consultar` del ACL Worker, uno con el vuelo `AV9340` y otro con la
#      celda de clima de Bogotá, EXACTAMENTE los datos fijos de `sembrar_polizas_parametricas` de
#      POLICY. Esto NO es una enumeración real de pólizas vigentes: hoy el ACL Worker no tiene ningún
#      endpoint que liste pólizas paramétricas activas (no se construyó en ninguna Fase anterior), así
#      que no hay nada real que encolar todavía. Escalar esto a producción (consultar todas las pólizas
#      vigentes, no dos valores fijos) es trabajo de CÓDIGO en otro servicio (p. ej. un endpoint nuevo en
#      POLICY o en el ACL que enumere coberturas por vencer), no de infraestructura; este Terraform solo
#      demuestra el cableado de Cloud Scheduler -> ACL Worker -> Pub/Sub -> CLAIMS de punta a punta.
#
# Nota de diseño (sin confirmar con GCP real): igual que la suscripción `rating-perfil-actualizado` de
# pubsub.tf, este archivo NO otorga explícitamente `roles/iam.serviceAccountTokenCreator` al agente de
# Pub/Sub sobre las cuentas de runtime que firman los tokens OIDC de push (`enviar-push`, `claims`).
# GCP concede ese rol automáticamente al agente de servicio de Pub/Sub cuando el proyecto habilitó la
# API después de abril de 2021; en un proyecto más viejo (o si ese binding por defecto se removió) habría
# que agregarlo a mano, como ya hace `almacenamiento.tf` con `pubsub_token_almacenamiento`. Si al aplicar
# esto el push a `enviar-push` o `claims` falla con un error de permisos al firmar el OIDC, ese es el
# primer lugar a revisar.
#
# Costo: ambos temas y sus suscripciones quedan dentro de la capa gratuita de Pub/Sub (10 GB/mes); los
# dos Cloud Scheduler de demostración compiten por los "3 trabajos gratis por cuenta de facturación"
# junto con `sonda-acl`, `risk-reproceso-nocturno` y `reconciliacion-cobros-policy` (ver infra/README.md,
# "Disparadores externos"): con `crear_tareas`, `crear_batch` y `crear_push` todos encendidos a la vez,
# el equipo ya pasa holgadamente ese tope gratuito y empieza a pagar centavos al mes por trabajo extra.

locals {
  push_activo = var.crear_push && var.crear_servicios

  # Datos fijos de la semilla `python -m app.sembrar_polizas_parametricas` de POLICY (ver
  # services/policy/app/sembrar_polizas_parametricas.py): el vuelo AV9340 (fecha_salida fija
  # 2026-11-11, la que usa la semilla) y la celda de clima de Bogotá (4.62,-74.09), variable
  # VIENTO_KMH (umbral 1 km/h en la semilla: se cumple siempre, para la demo). El esquema del cuerpo es `ConsultaEventoParametricoRequest` de
  # services/acl-worker/app/main.py.
  cuerpo_demo_vuelo_parametrico = jsonencode({
    tipo         = "VUELO"
    numero_vuelo = var.vuelo_demo_parametrico
    fecha        = "hoy" # el ACL la resuelve a la fecha de hoy en Bogotá
  })
  cuerpo_demo_clima_parametrico = jsonencode({
    tipo     = "CLIMA"
    clave    = "4.62,-74.09"
    latitud  = 4.62
    longitud = -74.09
    variable = "VIENTO_KMH"
  })
}

resource "google_project_service" "push_apis" {
  for_each = var.crear_push ? toset([
    "pubsub.googleapis.com",         # ya habilitada siempre en main.tf; se repite aquí por si push.tf se usa solo
    "cloudscheduler.googleapis.com", # puede faltar si ni crear_tareas ni crear_batch están encendidos
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------------------- notificaciones-push (Fase 2, D2)
resource "google_pubsub_topic" "notificaciones_push" {
  count      = var.crear_push ? 1 : 0
  project    = var.project_id
  name       = "notificaciones-push"
  depends_on = [google_project_service.apis, google_project_service.push_apis]
}

resource "google_pubsub_subscription" "enviar_push_notificaciones" {
  count                = var.crear_push ? 1 : 0
  project              = var.project_id
  name                 = "enviar-push-notificaciones"
  topic                = google_pubsub_topic.notificaciones_push[0].id
  ack_deadline_seconds = 20

  dynamic "push_config" {
    for_each = var.crear_servicios ? [1] : []
    content {
      push_endpoint = "${local.url["enviar-push"]}/eventos"
      oidc_token {
        service_account_email = google_service_account.runtime["enviar-push"].email
        audience              = "enviar-push"
      }
    }
  }
}

# Publican: CLAIMS (Fase 4, al liquidar la indemnización paramétrica) y el propio `enviar-push`
# (su `POST /v1/push` reencola en el tema antes de procesar por `/eventos`).
resource "google_pubsub_topic_iam_member" "claims_publica_push" {
  count   = local.push_activo ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.notificaciones_push[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.runtime["claims"].email}"
}

resource "google_pubsub_topic_iam_member" "enviar_push_publica" {
  count   = local.push_activo ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.notificaciones_push[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.runtime["enviar-push"].email}"
}

# Suscriptor: únicamente `enviar-push` (el que de verdad manda el push por SNS/FCM).
resource "google_pubsub_subscription_iam_member" "enviar_push_consume" {
  count        = local.push_activo ? 1 : 0
  project      = var.project_id
  subscription = google_pubsub_subscription.enviar_push_notificaciones[0].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.runtime["enviar-push"].email}"
}

# La suscripción push firma su OIDC con la cuenta de `enviar-push`, así que esa cuenta necesita
# `run.invoker` sobre su propio servicio (nadie la "llama" en `.github/servicios.json`, así que
# `invocador_llamada` de servicios.tf no la crea). Sin esto, Pub/Sub recibe 403 y el push nunca sale.
resource "google_cloud_run_v2_service_iam_member" "enviar_push_invoca_enviar_push" {
  count    = local.push_activo ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["enviar-push"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["enviar-push"].email}"
}

# --------------------------------------------------------------------------- eventos-parametricos (Fase 3, con DLQ)
resource "google_pubsub_topic" "eventos_parametricos" {
  count      = var.crear_push ? 1 : 0
  project    = var.project_id
  name       = "eventos-parametricos"
  depends_on = [google_project_service.apis, google_project_service.push_apis]
}

resource "google_pubsub_topic" "eventos_parametricos_dlq" {
  count      = var.crear_push ? 1 : 0
  project    = var.project_id
  name       = "eventos-parametricos-dlq"
  depends_on = [google_project_service.apis, google_project_service.push_apis]
}

resource "google_pubsub_subscription" "claims_eventos_parametricos" {
  count                = var.crear_push ? 1 : 0
  project              = var.project_id
  name                 = "claims-eventos-parametricos"
  topic                = google_pubsub_topic.eventos_parametricos[0].id
  ack_deadline_seconds = 20

  dynamic "push_config" {
    for_each = var.crear_servicios ? [1] : []
    content {
      push_endpoint = "${local.url["claims"]}/eventos/parametricos"
      oidc_token {
        service_account_email = google_service_account.runtime["claims"].email
        audience              = "claims"
      }
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.eventos_parametricos_dlq[0].id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# Permiso de publicación únicamente para el ACL Worker (escritor único del evento, regla 2).
resource "google_pubsub_topic_iam_member" "acl_publica_parametricos" {
  count   = (var.crear_push && var.crear_servicios) ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.eventos_parametricos[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.runtime["acl-worker"].email}"
}

# Permiso de suscripción únicamente para CLAIMS (lector/consumidor del evento).
resource "google_pubsub_subscription_iam_member" "claims_consume_parametricos" {
  count        = (var.crear_push && var.crear_servicios) ? 1 : 0
  project      = var.project_id
  subscription = google_pubsub_subscription.claims_eventos_parametricos[0].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.runtime["claims"].email}"
}

# Igual que `enviar_push_invoca_enviar_push`: la suscripción push hacia CLAIMS firma con la cuenta
# de CLAIMS, que necesita `run.invoker` sobre su propio servicio.
resource "google_cloud_run_v2_service_iam_member" "claims_invoca_claims" {
  count    = local.push_activo ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["claims"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["claims"].email}"
}

# El agente de Pub/Sub necesita poder publicar en el tema DLQ y leer la suscripción original para
# reenviar ahí los mensajes que agotaron sus 5 intentos (ver nota de la cabecera).
resource "google_pubsub_topic_iam_member" "pubsub_publica_dlq_parametricos" {
  count   = var.crear_push ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.eventos_parametricos_dlq[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "pubsub_consume_original_parametricos" {
  count        = var.crear_push ? 1 : 0
  project      = var.project_id
  subscription = google_pubsub_subscription.claims_eventos_parametricos[0].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# --------------------------------------------------------------------------- Cloud Scheduler DE DEMOSTRACIÓN
# Ver la nota larga de la cabecera: esto reemplaza, solo para la demo de punta a punta, la enumeración
# real de "pólizas paramétricas vigentes" que el plan pide y que hoy NO existe en ningún servicio. Cada
# trabajo llama directo a `POST /eventos-parametricos/_consultar` del ACL Worker con el dato fijo de la
# semilla de POLICY. Reutiliza el patrón de tareas.tf / batch.tf (cuenta dedicada + oidc_token).
resource "google_service_account" "scheduler_parametricos" {
  count        = local.push_activo ? 1 : 0
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "sched-parametricos-demo"
  display_name = "Cloud Scheduler de demostración: consulta periódica al ACL Worker (eventos paramétricos)"
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_parametricos_invoca_acl" {
  count    = local.push_activo ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["acl-worker"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_parametricos[0].email}"
}

resource "google_cloud_scheduler_job" "consulta_demo_vuelo_parametrico" {
  count       = local.push_activo ? 1 : 0
  depends_on  = [google_project_service.push_apis]
  project     = var.project_id
  region      = var.region
  name        = "consulta-demo-vuelo-parametrico"
  description = "DEMOSTRACIÓN (no enumera pólizas reales, ver push.tf): consulta cada 8 h el vuelo AV9340 de la semilla de POLICY contra el ACL Worker."
  schedule    = "0 */8 * * *"
  time_zone   = "America/Bogota"

  attempt_deadline = "60s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.url["acl-worker"]}/eventos-parametricos/_consultar"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(local.cuerpo_demo_vuelo_parametrico)

    oidc_token {
      service_account_email = google_service_account.scheduler_parametricos[0].email
      audience              = local.url["acl-worker"]
    }
  }
}

resource "google_cloud_scheduler_job" "consulta_demo_clima_parametrico" {
  count       = local.push_activo ? 1 : 0
  depends_on  = [google_project_service.push_apis]
  project     = var.project_id
  region      = var.region
  name        = "consulta-demo-clima-parametrico"
  description = "DEMOSTRACIÓN (no enumera pólizas reales, ver push.tf): consulta cada 8 h (1 min después del vuelo) la celda de clima de Bogotá (4.62,-74.09) de la semilla de POLICY contra el ACL Worker."
  schedule    = "1 */8 * * *"
  time_zone   = "America/Bogota"

  attempt_deadline = "60s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.url["acl-worker"]}/eventos-parametricos/_consultar"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(local.cuerpo_demo_clima_parametrico)

    oidc_token {
      service_account_email = google_service_account.scheduler_parametricos[0].email
      audience              = local.url["acl-worker"]
    }
  }
}
