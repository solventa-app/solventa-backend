# Workload Identity Federation para que `.github/workflows/deploy-staging.yml` se autentique contra
# GCP sin llaves de service account (regla 4 de CLAUDE.md). Repo autorizado: var.github_repositorio
# (default: solventa-app/solventa-backend, el `origin` real del equipo).
#
# Gate: var.crear_wif (default false). Costo: $0 (WIF no cobra; solo paga lo que la SA haga, que aquí
# es exactamente lo mismo que ya paga el equipo por Cloud Run/Artifact Registry).

resource "google_project_service" "wif_apis" {
  for_each = var.crear_wif ? toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = var.crear_wif ? 1 : 0
  project                   = var.project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.wif_apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.crear_wif ? 1 : 0
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Restringe la federación a ESTE repo exacto: ningún otro repo de la organización puede asumir
  # la identidad, aunque tenga el mismo pool.
  attribute_condition = "assertion.repository == '${var.github_repositorio}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "despliegue_github" {
  count        = var.crear_wif ? 1 : 0
  project      = var.project_id
  account_id   = "despliegue-github-actions"
  display_name = "Despliegue desde GitHub Actions (WIF, sin llave de service account)"
}

resource "google_service_account_iam_member" "wif_puede_impersonar" {
  count              = var.crear_wif ? 1 : 0
  service_account_id = google_service_account.despliegue_github[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repositorio}"
}

# Permisos mínimos para build+push+deploy (gcloud run deploy, no terraform apply desde CI — ver
# infra/README.md y .github/workflows/deploy-staging.yml sobre por qué).
resource "google_project_iam_member" "despliegue_run_admin" {
  count   = var.crear_wif ? 1 : 0
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.despliegue_github[0].email}"
}

resource "google_project_iam_member" "despliegue_artifact_writer" {
  count   = var.crear_wif ? 1 : 0
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.despliegue_github[0].email}"
}

# roles/run.admin no incluye "actuar como" la cuenta de ejecución de los servicios (default de
# Compute) — necesario para que `gcloud run deploy` actualice la revisión.
resource "google_service_account_iam_member" "despliegue_actuar_como_runtime" {
  count              = var.crear_wif ? 1 : 0
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.actual.number}-compute@developer.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.despliegue_github[0].email}"
}
