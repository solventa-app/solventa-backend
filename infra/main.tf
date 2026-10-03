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

# Cifrado de campo de datos personales (HA-SEG-002, D-04). Opcional: los key rings son permanentes.
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
