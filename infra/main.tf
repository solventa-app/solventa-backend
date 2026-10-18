# Base de la infraestructura de staging del backend. Deliberadamente mínima: solo lo que no cuesta
# o escala a cero. Los servicios de Cloud Run, Memorystore (Redis) y Cloud SQL se agregan por historia
# (ver infra/README.md); el patrón de Cloud Run + Memorystore + conector VPC está probado en
# solventa-arquitectura/experimento-1-acl-kyc/infra/.

locals {
  apis = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "pubsub.googleapis.com",
    # Trazas distribuidas (OpenTelemetry → API de Telemetry); sin costo en el volumen de staging.
    "telemetry.googleapis.com",
    "cloudtrace.googleapis.com",
    # Identidad del pipeline (WIF) y lectura del número de proyecto: no tienen costo.
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  registry_host = "${var.region}-docker.pkg.dev"
}

resource "google_project_service" "apis" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Repositorio Docker para las imágenes de los servicios y stubs.
resource "google_artifact_registry_repository" "imagenes" {
  depends_on    = [google_project_service.apis]
  project       = var.project_id
  location      = var.region
  repository_id = var.repository_id
  format        = "DOCKER"
  description   = "Imágenes del backend de Solventa (MISW4501)."
}
