# API Gateway administrado delante del BFF (HA-LAT-004: EC001, EC002, EC008; decisión 16, bloque 10).
#
# Cómo queda el tráfico en la nube:
#   navegador -> Vercel (rewrite /graphql) -> API Gateway (deadline 700 ms) -> BFF (privado, IAM)
#
# El BFF deja de ser público (`publico` se quita de `.github/servicios.json`): solo la cuenta de
# servicio `gateway-bff` tiene `roles/run.invoker` sobre `bff`. El archivo `infra/gateway/openapi.yaml`
# define las rutas y el plazo duro de 0.7 s (700 ms), y se mantiene sincronizado con `envoy.yaml`
# mediante `scripts/validar_gateway.py`.
#
# Costo: gratis hasta 2 millones de llamadas al mes.
# Gate: var.crear_gateway (default false).

resource "google_project_service" "gateway_apis" {
  for_each = var.crear_gateway ? toset([
    "apigateway.googleapis.com",
    "servicemanagement.googleapis.com",
    "servicecontrol.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "gateway_bff" {
  count        = var.crear_gateway ? 1 : 0
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "gateway-bff"
  display_name = "API Gateway invocador exclusivo del BFF"
}

# El dummy de siniestros (HU-W04) es privado como el resto: el front lo alcanza por el gateway en /graphql/siniestros.
resource "google_cloud_run_v2_service_iam_member" "gateway_invoca_siniestros" {
  count    = (var.crear_gateway && var.crear_servicios) ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["stub-siniestros"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway_bff[0].email}"
}

# El BFF ya no tiene `allUsers`: solo el API Gateway (y el pipeline en la prueba de humo) puede invocarlo.
resource "google_cloud_run_v2_service_iam_member" "gateway_invoca_bff" {
  count    = (var.crear_gateway && var.crear_servicios) ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio["bff"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway_bff[0].email}"
}

resource "google_api_gateway_api" "bff" {
  provider     = google-beta
  count        = var.crear_gateway ? 1 : 0
  project      = var.project_id
  api_id       = "solventa-bff-api"
  display_name = "Solventa BFF API Gateway"
  depends_on   = [google_project_service.gateway_apis]
}

resource "google_api_gateway_api_config" "bff" {
  provider             = google-beta
  count                = var.crear_gateway ? 1 : 0
  project              = var.project_id
  api                  = google_api_gateway_api.bff[0].api_id
  api_config_id_prefix = "solventa-bff-cfg-"
  display_name         = "Configuración OpenAPI del BFF (deadline 700 ms)"

  openapi_documents {
    document {
      path = "openapi.yaml"
      contents = base64encode(templatefile("${path.module}/gateway/openapi.yaml", {
        bff_url        = local.url["bff"]
        siniestros_url = local.url["stub-siniestros"]
      }))
    }
  }

  gateway_config {
    backend_config {
      google_service_account = google_service_account.gateway_bff[0].id
    }
  }

  # Una configuración de API Gateway es inmutable: cada cambio en openapi.yaml crea una nueva antes
  # de borrar la anterior (permitido por la excepción en scripts/revisar_plan.py).
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "bff" {
  provider     = google-beta
  count        = var.crear_gateway ? 1 : 0
  project      = var.project_id
  region       = var.region
  api_config   = google_api_gateway_api_config.bff[0].id
  gateway_id   = "solventa-gateway"
  display_name = "Solventa API Gateway (700 ms)"
}
