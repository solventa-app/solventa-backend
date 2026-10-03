terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # El estado es LOCAL por ahora (ignorado por git). Cuando el equipo defina el proyecto GCP,
  # moverlo a un bucket de GCS con versionado: `backend "gcs" { bucket = "..." prefix = "solventa-backend" }`.
}

provider "google" {
  project = var.project_id
  region  = var.region
}
