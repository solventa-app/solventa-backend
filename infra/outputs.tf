output "registry_url" {
  description = "Prefijo para publicar imágenes: <registry_url>/<servicio>:<tag>."
  value       = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.imagenes.repository_id}"
}

output "kms_llave_campos_personales" {
  description = "ID de la llave de cifrado de campo (null si crear_kms = false)."
  value       = var.crear_kms ? google_kms_crypto_key.campos_personales[0].id : null
}

output "wif_provider" {
  description = "Valor de la variable de GitHub GCP_WIF_PROVIDER (null si crear_pipeline = false)."
  value       = var.crear_pipeline ? google_iam_workload_identity_pool_provider.github[0].name : null
}

output "deploy_sa" {
  description = "Valor de la variable de GitHub GCP_DEPLOY_SA (null si crear_pipeline = false)."
  value       = var.crear_pipeline ? google_service_account.deploy[0].email : null
}

output "terraform_sa" {
  description = "Valor de la variable de GitHub GCP_TERRAFORM_SA (null si crear_pipeline = false)."
  value       = var.crear_pipeline ? google_service_account.terraform[0].email : null
}

output "secretos_a_cargar" {
  description = "Secretos cuyo valor real hay que cargar a mano con `gcloud secrets versions add` (vacío si crear_servicios = false)."
  value       = sort(tolist(local.secretos))
}

output "urls_servicios" {
  description = "URL de cada servicio de Cloud Run (vacío si crear_servicios = false)."
  value       = { for n, s in google_cloud_run_v2_service.servicio : n => s.uri }
}

output "redis_host" {
  description = "Host privado de Memorystore (null si Redis no existe o está apagado; solo alcanzable desde el conector VPC)."
  value       = (var.crear_redis && var.redis_encendida) ? google_redis_instance.cache[0].host : null
}

output "cloud_sql_connection_name" {
  description = "Connection name de la instancia de Cloud SQL (null si crear_cloud_sql = false)."
  value       = var.crear_cloud_sql ? google_sql_database_instance.principal[0].connection_name : null
}

output "mongo_atlas_srv_address" {
  description = "Dirección SRV del cluster de Mongo Atlas (null si crear_mongo_atlas = false). La URI completa con credenciales vive solo en Secret Manager (secreto mongo-uri-risk), nunca aquí."
  value       = var.crear_mongo_atlas ? mongodbatlas_cluster.risk[0].srv_address : null
}

output "topic_alertas_presupuesto" {
  description = "ID del topic de Pub/Sub de alertas de presupuesto (null si crear_presupuesto = false)."
  value       = var.crear_presupuesto ? google_pubsub_topic.alertas_presupuesto[0].id : null
}

output "topic_perfil_actualizado" {
  description = "ID del topic de Pub/Sub para el evento PerfilActualizado (null si crear_pubsub = false)."
  value       = var.crear_pubsub ? google_pubsub_topic.perfil_actualizado[0].id : null
}

output "job_risk_reproceso" {
  description = "Nombre del Cloud Run Job del batch nocturno de reprocesamiento de perfiles (null si crear_batch = false)."
  value       = (var.crear_batch && var.crear_servicios) ? google_cloud_run_v2_job.risk_reproceso[0].name : null
}

output "gateway_url" {
  description = "URL pública del API Gateway delante del BFF con corte duro de 700 ms (null si crear_gateway = false)."
  value       = var.crear_gateway ? "https://${google_api_gateway_gateway.bff[0].default_hostname}" : null
}

output "gateway_socios_url" {
  description = "URL pública del API Gateway de los socios (HU-W07); null si crear_gateway_socios = false. Su `/v1/.well-known/jwks.json` es el valor de `jwks_socios_url`."
  value       = local.gateway_socios ? "https://${google_api_gateway_gateway.socios[0].default_hostname}" : null
}
