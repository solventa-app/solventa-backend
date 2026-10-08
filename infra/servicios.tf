# Servicios de Cloud Run y jobs de migración, generados desde .github/servicios.json (fuente única de
# servicios, la misma que usan el CI y el deploy). Opcional (`crear_servicios`).
#
# Reparto de responsabilidades (ADR-07): Terraform define QUÉ es cada servicio (cuenta de servicio, variables,
# secretos, permisos entre servicios); el pipeline solo cambia la IMAGEN de cada revisión y el tráfico.
# Por eso se ignoran image, labels, client y traffic.
#
# Costo: Cloud Run y Cloud Run Jobs escalan a cero (~USD 0 en reposo). Secret Manager cobra ~USD 0,06/mes por
# versión activa. Nada aquí usa VPC, Memorystore ni Cloud SQL (siguen fuera de Terraform: ver infra/README.md).

data "google_project" "actual" {
  project_id = var.project_id
  depends_on = [google_project_service.apis]
}

locals {
  manifiesto = jsondecode(file("${path.module}/../.github/servicios.json"))
  servicios  = { for s in local.manifiesto : s.nombre => s }
  activos    = { for n, s in local.servicios : n => s if var.crear_servicios }

  con_migracion = { for n, s in local.activos : n => s if try(s.migracion, null) != null }
  publicos      = { for n, s in local.activos : n => s if try(s.publico, false) }

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

  depends_on = [
    google_secret_manager_secret_version.marcador,
    google_secret_manager_secret_iam_member.lectura,
  ]

  template {
    service_account = google_service_account.runtime[each.key].email

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instancias
    }

    containers {
      image = var.imagen_inicial

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      dynamic "env" {
        for_each = try(each.value.entorno, {})
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

      containers {
        image   = var.imagen_inicial
        command = [each.value.migracion[0]]
        args    = slice(tolist(each.value.migracion), 1, length(each.value.migracion))

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
