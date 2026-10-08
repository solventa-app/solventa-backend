# Cloud Run para los 10 servicios/stubs del backend (escalan a cero: casi $0 en reposo).
#
# Gate: var.crear_servicios (default false). Requiere que las imágenes ya existan en Artifact
# Registry con el tag var.image_tag — Cloud Run no construye nada, solo las despliega.
#
# Por qué 4 bloques de recursos y no un solo `for_each` con los 10: cada servicio necesita la URL de
# otro (ACL_URL, AUTH_URL, etc.), igual que en docker-compose.yml. Terraform NO permite que las
# instancias de un `for_each` se referencien entre sí a través de un `local` compartido por todas
# (produce "Error: Cycle", comprobado al validar este archivo) — así que se agrupan en 4 niveles de
# dependencia (cada uno su propio `for_each`, sin repetir 10 bloques completos):
#   nivel_0: sin dependencias de otro Cloud Run (los 3 stubs, auth, rating)
#   nivel_1: acl-worker (depende de los 3 stubs de nivel_0)
#   nivel_2: risk, payments, consolidador-fuentes (dependen de acl-worker de nivel_1)
#   nivel_3: bff (depende de auth/rating de nivel_0 y risk/payments de nivel_2)
# Redis/Cloud SQL/Mongo son recursos de otro tipo (no Cloud Run): referenciarlos desde cualquier
# nivel no crea ciclo.

locals {
  imagen_base = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.imagenes.repository_id}"

  # Servicios que necesitan el conector VPC para llegar a Memorystore (mismas bases lógicas 0/1/2
  # que redis:6379/0,1,2 en docker-compose.yml).
  necesita_redis = ["acl-worker", "rating", "consolidador-fuentes"]

  # Servicios que necesitan el socket de Cloud SQL (/cloudsql) — AUTH y PAYMENTS, escritores únicos
  # de su base (regla 1 de CLAUDE.md).
  necesita_sql = ["auth", "payments"]

  nivel_0 = {
    "stub-open-finance" = { memoria = "256Mi" }
    "stub-open-data"    = { memoria = "256Mi" }
    "stub-pasarela"     = { memoria = "256Mi" }
    "auth"              = { memoria = "512Mi" }
    "rating"            = { memoria = "512Mi" }
  }
  nivel_1 = {
    "acl-worker" = { memoria = "512Mi" }
  }
  nivel_2 = {
    "risk"                 = { memoria = "512Mi" }
    "payments"             = { memoria = "512Mi" }
    "consolidador-fuentes" = { memoria = "512Mi" }
  }
  nivel_3 = {
    "bff" = { memoria = "512Mi" }
  }

  # --- Variables de entorno literales (no dependen de otro recurso) ---
  env_estaticas = {
    "acl-worker" = {
      TIMEOUT_MS               = "700" # umbral duro de EC009/EC010
      BREAKER_UMBRAL_FALLOS    = "3"
      BREAKER_TTL_SEGUNDOS     = "5"
      SONDA_INTERVALO_SEGUNDOS = "2" # la sonda corre fuera del camino síncrono (regla 5)
      CACHE_TTL_SEGUNDOS       = "300"
    }
    "rating" = {
      RATING_MAX_TIME_MS = "150" # D-02: cota de espera de la sesión causal (EC011/EC012)
    }
  }

  # --- Variables de entorno desde Secret Manager (nunca valores en claro en el estado) ---
  env_secretos = merge(
    var.crear_cloud_sql ? {
      "auth"     = { DATABASE_URL = google_secret_manager_secret.database_url_auth[0].secret_id }
      "payments" = { DATABASE_URL = google_secret_manager_secret.database_url_payments[0].secret_id }
    } : {},
    var.crear_mongo_atlas ? {
      "risk" = { MONGO_URI = google_secret_manager_secret.mongo_uri[0].secret_id }
    } : {}
  )

  # --- Referencias entre niveles (solo de un nivel hacia uno ANTERIOR: nunca al revés) ---
  referencias_nivel_0 = {
    "rating" = var.crear_redis ? { REDIS_URL = "redis://${google_redis_instance.cache[0].host}:6379/0" } : {}
  }
  referencias_nivel_1 = {
    "acl-worker" = merge(
      {
        OPEN_FINANCE_URL = google_cloud_run_v2_service.nivel_0["stub-open-finance"].uri
        OPEN_DATA_URL    = google_cloud_run_v2_service.nivel_0["stub-open-data"].uri
        PASARELA_URL     = google_cloud_run_v2_service.nivel_0["stub-pasarela"].uri
      },
      var.crear_redis ? {
        REDIS_URL                     = "redis://${google_redis_instance.cache[0].host}:6379/1"
        COLA_RECONCILIACION_REDIS_URL = "redis://${google_redis_instance.cache[0].host}:6379/2"
      } : {}
    )
  }
  referencias_nivel_2 = {
    "risk" = {
      ACL_URL = google_cloud_run_v2_service.nivel_1["acl-worker"].uri
    }
    "payments" = {
      ACL_URL = google_cloud_run_v2_service.nivel_1["acl-worker"].uri
    }
    "consolidador-fuentes" = merge(
      { ACL_URL = google_cloud_run_v2_service.nivel_1["acl-worker"].uri },
      var.crear_redis ? { REDIS_URL = "redis://${google_redis_instance.cache[0].host}:6379/2" } : {}
    )
  }
  referencias_nivel_3 = {
    "bff" = {
      AUTH_URL     = google_cloud_run_v2_service.nivel_0["auth"].uri
      RISK_URL     = google_cloud_run_v2_service.nivel_2["risk"].uri
      RATING_URL   = google_cloud_run_v2_service.nivel_0["rating"].uri
      PAYMENTS_URL = google_cloud_run_v2_service.nivel_2["payments"].uri
    }
  }
}

resource "google_cloud_run_v2_service" "nivel_0" {
  for_each = var.crear_servicios ? local.nivel_0 : {}

  project  = var.project_id
  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = "${local.imagen_base}/${each.key}:${var.image_tag}"
      # PORT es una env var reservada que Cloud Run inyecta solo (8080); todos los Dockerfile ya
      # leen $PORT (igual que experimento-1-acl-kyc/infra).

      resources {
        limits = {
          cpu    = "1"
          memory = each.value.memoria
        }
      }

      dynamic "env" {
        for_each = lookup(local.env_estaticas, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = lookup(local.referencias_nivel_0, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = lookup(local.env_secretos, each.key, {})
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = contains(local.necesita_sql, each.key) && var.crear_cloud_sql ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    dynamic "volumes" {
      for_each = contains(local.necesita_sql, each.key) && var.crear_cloud_sql ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.principal[0].connection_name]
        }
      }
    }

    dynamic "vpc_access" {
      for_each = contains(local.necesita_redis, each.key) && var.crear_redis ? [1] : []
      content {
        connector = google_vpc_access_connector.conector[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "nivel_1" {
  for_each = var.crear_servicios ? local.nivel_1 : {}

  project  = var.project_id
  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = "${local.imagen_base}/${each.key}:${var.image_tag}"

      resources {
        limits = {
          cpu    = "1"
          memory = each.value.memoria
        }
      }

      dynamic "env" {
        for_each = lookup(local.env_estaticas, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = lookup(local.referencias_nivel_1, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    dynamic "vpc_access" {
      for_each = contains(local.necesita_redis, each.key) && var.crear_redis ? [1] : []
      content {
        connector = google_vpc_access_connector.conector[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "nivel_2" {
  for_each = var.crear_servicios ? local.nivel_2 : {}

  project  = var.project_id
  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = "${local.imagen_base}/${each.key}:${var.image_tag}"

      resources {
        limits = {
          cpu    = "1"
          memory = each.value.memoria
        }
      }

      dynamic "env" {
        for_each = lookup(local.referencias_nivel_2, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = lookup(local.env_secretos, each.key, {})
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = contains(local.necesita_sql, each.key) && var.crear_cloud_sql ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    dynamic "volumes" {
      for_each = contains(local.necesita_sql, each.key) && var.crear_cloud_sql ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.principal[0].connection_name]
        }
      }
    }

    dynamic "vpc_access" {
      for_each = contains(local.necesita_redis, each.key) && var.crear_redis ? [1] : []
      content {
        connector = google_vpc_access_connector.conector[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }
  }

  depends_on = [google_project_service.apis, google_cloud_run_v2_service.nivel_1]
}

resource "google_cloud_run_v2_service" "nivel_3" {
  for_each = var.crear_servicios ? local.nivel_3 : {}

  project  = var.project_id
  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = "${local.imagen_base}/${each.key}:${var.image_tag}"

      resources {
        limits = {
          cpu    = "1"
          memory = each.value.memoria
        }
      }

      dynamic "env" {
        for_each = lookup(local.referencias_nivel_3, each.key, {})
        content {
          name  = env.key
          value = env.value
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
  }

  depends_on = [google_project_service.apis, google_cloud_run_v2_service.nivel_0, google_cloud_run_v2_service.nivel_2]
}

# --- Invocación pública (ver variable allow_unauthenticated: deliberado para el Sprint 1, sin datos
# reales — mismo patrón ya usado en solventa-arquitectura/experimento-1-acl-kyc. No usar así en
# producción). Un bloque por nivel para no repetir el `merge()` de 4 mapas de recursos distintos.

resource "google_cloud_run_v2_service_iam_member" "publico_nivel_0" {
  for_each = var.crear_servicios && var.allow_unauthenticated ? local.nivel_0 : {}
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.nivel_0[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "publico_nivel_1" {
  for_each = var.crear_servicios && var.allow_unauthenticated ? local.nivel_1 : {}
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.nivel_1[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "publico_nivel_2" {
  for_each = var.crear_servicios && var.allow_unauthenticated ? local.nivel_2 : {}
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.nivel_2[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "publico_nivel_3" {
  for_each = var.crear_servicios && var.allow_unauthenticated ? local.nivel_3 : {}
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.nivel_3[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
