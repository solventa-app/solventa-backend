# Memorystore (Redis BASIC) + conector VPC, compartido por acl-worker (cache + circuito + cola de
# reconciliación), rating (sesión causal D-02) y consolidador-fuentes — mismo patrón de bases lógicas
# que redis:6379/0,1,2 en docker-compose.yml, ahora sobre una sola instancia real.
#
# OJO costo: NO escala a cero. Redis BASIC 1GB + conector VPC (mínimo 2 instancias e2-micro) son
# ~USD 0,12/h juntos (ver infra/README.md). Crear solo para la sesión de pruebas (`crear_redis = true`)
# y destruir (`terraform destroy -target=...`) al terminar.
#
# Gate: var.crear_redis (default false).

resource "google_project_service" "redis_apis" {
  for_each = var.crear_redis ? toset([
    "redis.googleapis.com",
    "vpcaccess.googleapis.com",
    "compute.googleapis.com",
  ]) : []
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Red dedicada (no se asume que exista una red "default" en el proyecto destino).
resource "google_compute_network" "privada" {
  count                   = var.crear_redis ? 1 : 0
  project                 = var.project_id
  name                    = "solventa-backend-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.redis_apis]
}

resource "google_compute_subnetwork" "conector" {
  count         = var.crear_redis ? 1 : 0
  project       = var.project_id
  name          = "solventa-conector-subred"
  region        = var.region
  network       = google_compute_network.privada[0].id
  ip_cidr_range = var.vpc_connector_rango
}

resource "google_vpc_access_connector" "conector" {
  count         = var.crear_redis ? 1 : 0
  project       = var.project_id
  name          = "solventa-conector"
  region        = var.region
  machine_type  = "e2-micro"
  min_instances = 2
  max_instances = 3

  subnet {
    name = google_compute_subnetwork.conector[0].name
  }

  depends_on = [google_project_service.redis_apis]
}

resource "google_redis_instance" "cache" {
  count              = var.crear_redis ? 1 : 0
  project            = var.project_id
  name               = "solventa-backend-redis"
  tier               = "BASIC"
  memory_size_gb     = var.redis_memory_size_gb
  region             = var.region
  authorized_network = google_compute_network.privada[0].id

  depends_on = [google_project_service.redis_apis]
}
