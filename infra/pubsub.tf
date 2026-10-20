# Pub/Sub para el evento de dominio `PerfilActualizado` (decisión 9, bloque 8).
# Solo se provisiona este evento en el Sprint 1: RISK publica al actualizar el perfil y RATING se
# suscribe para invalidar su caché del score. Capa gratuita de Pub/Sub: hasta 10 GB/mes.
#
# Gate: var.crear_pubsub (default false).

resource "google_pubsub_topic" "perfil_actualizado" {
  count      = var.crear_pubsub ? 1 : 0
  project    = var.project_id
  name       = "perfil-actualizado"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "rating_perfil_actualizado" {
  count                = var.crear_pubsub ? 1 : 0
  project              = var.project_id
  name                 = "rating-perfil-actualizado"
  topic                = google_pubsub_topic.perfil_actualizado[0].id
  ack_deadline_seconds = 20

  dynamic "push_config" {
    for_each = var.crear_servicios ? [1] : []
    content {
      push_endpoint = "${local.url["rating"]}/eventos/perfil-actualizado"
      oidc_token {
        service_account_email = google_service_account.runtime["rating"].email
        audience              = "rating"
      }
    }
  }
}

# Permiso de publicación únicamente para RISK (escritor único del perfil).
resource "google_pubsub_topic_iam_member" "risk_publica_perfil" {
  count   = (var.crear_pubsub && var.crear_servicios) ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.perfil_actualizado[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.runtime["risk"].email}"
}

# Permiso de suscripción únicamente para RATING (lector del perfil y del score cacheado).
resource "google_pubsub_subscription_iam_member" "rating_consume_perfil" {
  count        = (var.crear_pubsub && var.crear_servicios) ? 1 : 0
  project      = var.project_id
  subscription = google_pubsub_subscription.rating_perfil_actualizado[0].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.runtime["rating"].email}"
}
