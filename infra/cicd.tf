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

# ------------------------------------------------------------------------------------------------------------
# Identidad de TERRAFORM en el pipeline (ADR-09). Aplica la infraestructura al mergear a `develop`, así que tiene
# muchos más permisos que la de despliegue: por eso es otra cuenta y otro entorno de GitHub
# (`entorno_github_infra`). Sin llaves: Workload Identity Federation limitada a este repositorio (condición del
# proveedor, arriba) y a ese entorno.

resource "google_service_account" "terraform" {
  count        = var.crear_pipeline ? 1 : 0
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "gha-terraform-backend"
  display_name = "Terraform del backend desde GitHub Actions"
}

resource "google_service_account_iam_member" "terraform_desde_github" {
  count              = var.crear_pipeline ? 1 : 0
  service_account_id = google_service_account.terraform[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.environment/${var.entorno_github_infra}"
}

# Propietario del PROYECTO de staging (decisión del equipo, ADR-09); no de la cuenta de facturación ni de la
# organización. Es amplio a propósito: Terraform crea cuentas de servicio, asigna roles y administra Cloud Run,
# Secret Manager, Cloud SQL, Redis, KMS y Pub/Sub, y Terraform ya gestiona el IAM del proyecto, lo que de por sí
# equivale a poder asignarse propietario. El riesgo se acota con: WIF limitada a este repositorio y al entorno
# `infra-staging`, `develop` protegida (PR + revisión, sin excepciones), el guardia contra destrucciones de
# scripts/revisar_plan.py y que solo vive en un proyecto de staging.
resource "google_project_iam_member" "terraform_propietario" {
  count   = var.crear_pipeline ? 1 : 0
  project = var.project_id
  role    = "roles/owner"
  member  = "serviceAccount:${google_service_account.terraform[0].email}"
}

# Para que `plan` pueda leer el presupuesto (la cuenta de facturación es compartida con otros proyectos). Solo
# lectura: cambiar el presupuesto sigue siendo manual, de quien administra la facturación.
resource "google_billing_account_iam_member" "terraform_lee_presupuesto" {
  provider           = google.facturacion
  count              = (var.crear_pipeline && var.crear_presupuesto) ? 1 : 0
  billing_account_id = var.billing_account_id
  role               = "roles/billing.viewer"
  member             = "serviceAccount:${google_service_account.terraform[0].email}"

  # El proveedor `facturacion` cobra la cuota al proyecto (versions.tf): la Cloud Billing API debe estar habilitada.
  depends_on = [google_project_service.presupuesto_apis]
}
