terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
    google-beta = {
      # Necesario para los recursos de Google Cloud API Gateway (infra/gateway.tf, HA-LAT-004).
      source  = "hashicorp/google-beta"
      version = "~> 8.7"
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
      version = "~> 2.19"
    }
  }

  # Estado remoto en GCS (bucket con versionado y bloqueo de acceso público). El bucket NO lo crea Terraform
  # (huevo y gallina): se creó a mano, ver "Estado de Terraform" en infra/README.md. El CI usa
  # `init -backend=false`, así que no necesita acceso al bucket.
  backend "gcs" {
    bucket = "solventa-511815-tfstate"
    prefix = "solventa-backend"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# Solo para google_billing_budget: la API de presupuestos exige un proyecto de cuota explícito cuando se usan
# credenciales de usuario (si no, responde 403 aunque la API esté habilitada). El resto de recursos NO lo usa:
# con user_project_override global, habilitar las APIs de un proyecto nuevo fallaría hasta que Cloud Resource
# Manager esté habilitada, que es justo una de las APIs que se habilitan ahí.
provider "google" {
  alias                 = "facturacion"
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

# Sin bloque de configuración: lee MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY del entorno.
# No falla si no están definidas mientras ningún recurso de este provider se active (crear_mongo_atlas).
provider "mongodbatlas" {}
