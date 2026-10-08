terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      # Genera las contraseñas de Cloud SQL / Mongo Atlas que van a Secret Manager (nunca al repo).
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      # Empaqueta el código de la función `frenar-gasto` (infra/funciones/frenar-gasto/) en el zip
      # que sube Cloud Functions.
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    mongodbatlas = {
      # Solo se usa si var.crear_mongo_atlas = true (RISK). Requiere MONGODB_ATLAS_PUBLIC_KEY y
      # MONGODB_ATLAS_PRIVATE_KEY como variables de entorno del shell que corre terraform — nunca en
      # tfvars. Ver infra/README.md.
      source  = "mongodb/mongodbatlas"
      version = "~> 1.0"
    }
  }

  # El estado es LOCAL por ahora (ignorado por git). Cuando el equipo defina el proyecto GCP,
  # moverlo a un bucket de GCS con versionado: `backend "gcs" { bucket = "..." prefix = "solventa-backend" }`.
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Sin bloque de configuración: lee MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY del entorno.
# No falla si no están definidas mientras ningún recurso de este provider se active (crear_mongo_atlas).
provider "mongodbatlas" {}
