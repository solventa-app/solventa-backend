output "registry_url" {
  description = "Prefijo para publicar imágenes: <registry_url>/<servicio>:<tag>."
  value       = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.imagenes.repository_id}"
}

output "kms_llave_campos_personales" {
  description = "ID de la llave de cifrado de campo (null si crear_kms = false)."
  value       = var.crear_kms ? google_kms_crypto_key.campos_personales[0].id : null
}

output "urls_servicios" {
  description = "URL pública de cada servicio de Cloud Run (vacío si crear_servicios = false). Los 10 servicios están repartidos en 4 recursos por nivel de dependencia (ver cloud-run.tf)."
  value = merge(
    { for nombre, servicio in google_cloud_run_v2_service.nivel_0 : nombre => servicio.uri },
    { for nombre, servicio in google_cloud_run_v2_service.nivel_1 : nombre => servicio.uri },
    { for nombre, servicio in google_cloud_run_v2_service.nivel_2 : nombre => servicio.uri },
    { for nombre, servicio in google_cloud_run_v2_service.nivel_3 : nombre => servicio.uri },
  )
}

output "redis_host" {
  description = "Host privado de Memorystore (null si crear_redis = false; solo alcanzable desde el conector VPC)."
  value       = var.crear_redis ? google_redis_instance.cache[0].host : null
}

output "cloud_sql_connection_name" {
  description = "Connection name de la instancia de Cloud SQL (null si crear_cloud_sql = false)."
  value       = var.crear_cloud_sql ? google_sql_database_instance.principal[0].connection_name : null
}

output "mongo_atlas_srv_address" {
  description = "Dirección SRV del cluster de Mongo Atlas (null si crear_mongo_atlas = false). La URI completa con credenciales vive solo en Secret Manager (secreto mongo-uri-risk), nunca aquí."
  value       = var.crear_mongo_atlas ? mongodbatlas_cluster.risk[0].srv_address : null
}

output "wif_provider" {
  description = "Nombre completo del provider de Workload Identity Federation — va en el secreto WIF_PROVIDER de GitHub Actions (null si crear_wif = false)."
  value       = var.crear_wif ? google_iam_workload_identity_pool_provider.github[0].name : null
}

output "wif_service_account" {
  description = "Email de la service account de despliegue — va en el secreto WIF_SERVICE_ACCOUNT de GitHub Actions (null si crear_wif = false)."
  value       = var.crear_wif ? google_service_account.despliegue_github[0].email : null
}

output "topic_alertas_presupuesto" {
  description = "ID del topic de Pub/Sub de alertas de presupuesto (null si crear_presupuesto = false)."
  value       = var.crear_presupuesto ? google_pubsub_topic.alertas_presupuesto[0].id : null
}
