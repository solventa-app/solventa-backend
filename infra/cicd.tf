# Identidad del pipeline de despliegue (deploy-staging.yml / rollback-staging.yml). Sin llaves de service
# account: Workload Identity Federation limitada a ESTE repositorio y al entorno de GitHub `staging`.
# Todo es opcional (`crear_pipeline`) y no tiene costo.

resource "google_iam_workload_identity_pool" "github" {
  count                     = var.crear_pipeline ? 1 : 0
  depends_on                = [google_project_service.apis]
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.crear_pipeline ? 1 : 0
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"         = "assertion.sub"
    "attribute.repository"   = "assertion.repository"
    "attribute.environment"  = "assertion.environment"
    "attribute.workflow_ref" = "assertion.workflow_ref"
  }

  # Ningún otro repositorio puede usar este proveedor, aunque conozca su nombre.
  attribute_condition = "assertion.repository == '${var.repositorio_github}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deploy" {
  count        = var.crear_pipeline ? 1 : 0
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "gha-deploy-backend"
  display_name = "Despliegue del backend desde GitHub Actions"
}

# Solo los jobs que declaran `environment: <entorno_github>` pueden asumir la identidad: así las reglas del
# entorno (revisores, ramas permitidas) protegen también el acceso a GCP.
resource "google_service_account_iam_member" "deploy_desde_github" {
  count              = var.crear_pipeline ? 1 : 0
  service_account_id = google_service_account.deploy[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.environment/${var.entorno_github}"
}

# Mínimo necesario: desplegar y ejecutar jobs en Cloud Run, publicar imágenes y llamar a los servicios
# privados para la prueba de humo. No puede leer secretos ni tocar Terraform, KMS ni bases de datos.
resource "google_project_iam_member" "deploy_cloud_run" {
  count   = var.crear_pipeline ? 1 : 0
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.deploy[0].email}"
}

resource "google_project_iam_member" "deploy_invocador" {
  count   = var.crear_pipeline ? 1 : 0
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.deploy[0].email}"
}

resource "google_artifact_registry_repository_iam_member" "deploy_registro" {
  count      = var.crear_pipeline ? 1 : 0
  project    = var.project_id
  location   = google_artifact_registry_repository.imagenes.location
  repository = google_artifact_registry_repository.imagenes.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy[0].email}"
}
