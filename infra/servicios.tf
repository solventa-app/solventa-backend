# Servicios de Cloud Run y jobs de migración, generados desde .github/servicios.json (fuente única de
# servicios, la misma que usan el CI y el deploy). Opcional (`crear_servicios`).
#
# Reparto de responsabilidades (ADR-07): Terraform define QUÉ es cada servicio (cuenta de servicio, variables,
# secretos, permisos entre servicios); el pipeline solo cambia la IMAGEN de cada revisión y el tráfico.
# Por eso se ignoran image, labels, client y traffic, y de la plantilla del servicio también `labels`:
# `gcloud run deploy --update-labels commit-sha=...` deja esa etiqueta en la plantilla.
#
# OJO: `template[0].revision` NO se ignora, aunque `gcloud run deploy` le pone nombre a cada revisión (p. ej.
# payments-00006-gos) y por eso el plan muestra `revision -> null` en los servicios que despliega el pipeline. Si se
# ignorara, Terraform reenviaría el nombre viejo en cada actualización y, en cuanto la plantilla cambie (una variable,
# un secreto, un volumen), Cloud Run respondería 409 «Revision named ... with different configuration already
# exists» y quedaría bloqueado todo `apply` de infra. Sin ignorarlo, Cloud Run nombra la revisión nueva. Costo
# asumido: un `apply` de infra puede crear una revisión nueva (misma configuración) en los servicios que despliega
# el pipeline.
#
# Costo: Cloud Run y Cloud Run Jobs escalan a cero (~USD 0 en reposo). Secret Manager cobra ~USD 0,06/mes por
# versión activa. Con `crear_cloud_sql`, los servicios con `database-url-*` (y sus jobs) montan el socket de Cloud SQL;
# Memorystore y su conector VPC todavía no están conectados a los servicios (ver infra/README.md).

data "google_project" "actual" {
  project_id = var.project_id
  depends_on = [google_project_service.apis]
}

locals {
  manifiesto = jsondecode(file("${path.module}/../.github/servicios.json"))
  servicios  = { for s in local.manifiesto : s.nombre => s }
  activos    = { for n, s in local.servicios : n => s if var.crear_servicios }

  con_migracion  = { for n, s in local.activos : n => s if try(s.migracion, null) != null }
  publicos       = { for n, s in local.activos : n => s if try(s.publico, false) }
  camino_critico = toset(["bff", "auth", "acl-worker", "risk", "rating", "stub-open-finance", "stub-open-data"])

  # Servicios que hablan con Postgres: los que declaran un secreto `database-url-*` (su URL usa el socket
  # /cloudsql, que Cloud Run solo monta si se pide). Vale para el servicio y para su job de migración.
  con_postgres = {
    for n, s in local.activos : n => s
    if var.crear_cloud_sql && anytrue([for id in values(try(s.secretos, {})) : startswith(id, "database-url-")])
  }
  cloud_sql_conexion = one(google_sql_database_instance.principal[*].connection_name)

  # Servicios que hablan con Redis: los que declaran un secreto `redis-url-*`. Memorystore solo tiene IP privada, así que
  # Cloud Run llega a ella únicamente a través del conector VPC de redis.tf.
  con_redis = {
    for n, s in local.activos : n => s
    if var.crear_redis && var.redis_encendida && anytrue([for id in values(try(s.secretos, {})) : startswith(id, "redis-url-")])
  }

  # URL determinista de cada servicio: evita la referencia circular entre servicios que se llaman.
  url = { for n, s in local.servicios : n => "https://${n}-${data.google_project.actual.number}.${var.region}.run.app" }

  # Quién llama a quién (de `llama`): da la variable de entorno con la URL y el permiso de invocación.
  llamadas = merge([
    for n, s in local.activos : {
      for d in try(s.llama, []) : "${n}->${d}" => { origen = n, destino = d }
    }
  ]...)

  secretos = var.crear_servicios ? toset(flatten([for s in local.manifiesto : values(try(s.secretos, {}))])) : toset([])
  lecturas_de_secretos = merge([
    for n, s in local.activos : {
      for env, id in try(s.secretos, {}) : "${n}/${env}" => { servicio = n, secreto = id }
    }
  ]...)
}

resource "google_service_account" "runtime" {
  for_each     = local.activos
  depends_on   = [google_project_service.apis]
  project      = var.project_id
  account_id   = "rt-${each.key}"
  display_name = "Runtime de ${each.key}"
}

# Trazas distribuidas: cada servicio escribe las suyas en Cloud Trace (OpenTelemetry → API de Telemetry). Se activan con la
# variable TRAZAS_GCP del servicio (.github/servicios.json). Solo escritura: la lectura es de las personas del equipo.
resource "google_project_iam_member" "trazas" {
  for_each = local.activos
  project  = var.project_id
  role     = "roles/telemetry.tracesWriter"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

# --------------------------------------------------------------------------------------------- secretos
# Terraform crea el contenedor del secreto (y una versión marcadora) pero NUNCA su valor real: este se carga
# a mano con `gcloud secrets versions add` (infra/README.md) y no pasa por el estado de Terraform. La
# versión marcadora existe para que Cloud Run pueda crear la primera revisión; una migración con el valor
# marcador falla fuerte y a propósito.
resource "google_secret_manager_secret" "secreto" {
  for_each   = local.secretos
  depends_on = [google_project_service.apis]
  project    = var.project_id
  secret_id  = each.value

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "marcador" {
  for_each    = local.secretos
  secret      = google_secret_manager_secret.secreto[each.value].id
  secret_data = "pendiente-de-cargar"

  lifecycle {
    ignore_changes = [secret_data]
  }
}

resource "google_secret_manager_secret_iam_member" "lectura" {
  for_each  = local.lecturas_de_secretos
  project   = var.project_id
  secret_id = google_secret_manager_secret.secreto[each.value.secreto].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime[each.value.servicio].email}"
}

# ------------------------------------------------------------------------------------ servicios (Cloud Run)
resource "google_cloud_run_v2_service" "servicio" {
  for_each            = local.activos
  project             = var.project_id
  name                = each.key
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false # staging: se puede destruir al terminar la sesión (control de costos)
  # El pipeline pide el token de identidad con este audience para la prueba de humo; vale también para
  # las URL con etiqueta de revisión.
  custom_audiences = [each.key]

  # Las versiones con el valor REAL de cada conexión (Cloud SQL, Redis, Atlas) deben existir antes de crear la revisión
  # nueva: Cloud Run lee `latest` al arrancar y, si la real no está, lee el marcador y la revisión no arranca.
  depends_on = [
    google_secret_manager_secret_version.marcador,
    google_secret_manager_secret_iam_member.lectura,
    google_secret_manager_secret_version.database_url_auth,
    google_secret_manager_secret_version.database_url_payments,
    google_secret_manager_secret_version.redis_url,
    google_secret_manager_secret_version.mongo_uri,
    google_secret_manager_secret_version.mongo_uri_rating,
  ]

  template {
    service_account = google_service_account.runtime[each.key].email

    scaling {
      min_instance_count = (var.calentar_camino_critico && contains(local.camino_critico, each.key)) ? 1 : 0
      max_instance_count = var.max_instancias
    }

    dynamic "volumes" {
      for_each = contains(keys(local.con_postgres), each.key) ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.cloud_sql_conexion]
        }
      }
    }

    # Solo el tráfico a rangos privados (Memorystore) sale por el conector; el resto, por la red normal de Cloud Run.
    dynamic "vpc_access" {
      for_each = contains(keys(local.con_redis), each.key) ? [1] : []
      content {
        connector = google_vpc_access_connector.conector[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }

    containers {
      image = var.imagen_inicial

      dynamic "volume_mounts" {
        for_each = contains(keys(local.con_postgres), each.key) ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
        # Más CPU solo mientras arranca el contenedor: acorta el arranque en frío (mínimo 0 instancias).
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = try(each.value.entorno, {})
        content {
          name  = env.key
          value = env.value
        }
      }

      # Cifrado de sobre (kms.tf, bloque 3): la llave de KMS con que AUTH, RISK y RATING desenvuelven
      # `CIFRADO_CLAVE_ENVUELTA`. Mientras ese secreto tenga el marcador, usan `CIFRADO_CLAVE`.
      dynamic "env" {
        for_each = contains(tolist(local.servicios_con_kms), each.key) ? {
          KMS_KEY_NAME = google_kms_crypto_key.campos_personales[0].id
        } : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      # Disparadores externos (tareas.tf): quién dispara el trabajo de fondo, y lo que Cloud Tasks necesita para
      # firmar y dirigir la tarea. Solo con `crear_tareas` y en los servicios que lo declaran.
      dynamic "env" {
        for_each = contains(keys(local.con_disparador_externo), each.key) ? {
          DISPARADOR    = "externo"
          GCP_PROYECTO  = var.project_id
          GCP_REGION    = var.region
          URL_PROPIA    = local.url[each.key]
          TAREAS_CUENTA = google_service_account.runtime[each.key].email
        } : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = toset(try(each.value.llama, []))
        content {
          name  = local.servicios[env.value].url_env
          value = local.url[env.value]
        }
      }

      dynamic "env" {
        for_each = try(each.value.secretos, {})
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secreto[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].labels,
      labels,
      client,
      client_version,
      traffic,
    ]
  }
}

# Un servicio solo puede ser invocado por quienes llama según el manifiesto (más el BFF, público).
resource "google_cloud_run_v2_service_iam_member" "invocador_llamada" {
  for_each = local.llamadas
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio[each.value.destino].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime[each.value.origen].email}"
}

# La única puerta de entrada pública es el BFF (TLS lo termina Cloud Run). Si la organización bloquea
# `allUsers` (política de uso compartido restringido por dominio), este recurso fallará: es esperable.
resource "google_cloud_run_v2_service_iam_member" "invocador_publico" {
  for_each = local.publicos
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.servicio[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --------------------------------------------------------------------------- jobs de migración (Cloud Run)
# Se ejecutan con la MISMA imagen que se va a desplegar (el pipeline la actualiza y lanza el job antes de
# mover el tráfico). Sin reintentos automáticos: una migración fallida se inspecciona, no se repite a ciegas.
resource "google_cloud_run_v2_job" "migracion" {
  for_each            = local.con_migracion
  project             = var.project_id
  name                = "migrar-${each.key}"
  location            = var.region
  deletion_protection = false

  depends_on = [
    google_secret_manager_secret_version.marcador,
    google_secret_manager_secret_iam_member.lectura,
  ]

  template {
    template {
      service_account = google_service_account.runtime[each.key].email
      max_retries     = 0
      timeout         = "600s"

      dynamic "volumes" {
        for_each = contains(keys(local.con_postgres), each.key) ? [1] : []
        content {
          name = "cloudsql"
          cloud_sql_instance {
            instances = [local.cloud_sql_conexion]
          }
        }
      }

      containers {
        image   = var.imagen_inicial
        command = [each.value.migracion[0]]
        args    = slice(tolist(each.value.migracion), 1, length(each.value.migracion))

        dynamic "volume_mounts" {
          for_each = contains(keys(local.con_postgres), each.key) ? [1] : []
          content {
            name       = "cloudsql"
            mount_path = "/cloudsql"
          }
        }

        dynamic "env" {
          for_each = try(each.value.entorno, {})
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = try(each.value.secretos, {})
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secreto[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      labels,
      client,
      client_version,
    ]
  }
}

# El pipeline despliega revisiones y ejecuta jobs "como" la cuenta de runtime de cada servicio.
resource "google_service_account_iam_member" "deploy_actua_como_runtime" {
  for_each           = { for n, s in local.activos : n => s if var.crear_pipeline }
  service_account_id = google_service_account.runtime[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy[0].email}"
}
