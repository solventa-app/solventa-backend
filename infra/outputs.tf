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

output "secretos_a_cargar" {
  description = "Secretos cuyo valor real hay que cargar a mano con `gcloud secrets versions add` (vacío si crear_servicios = false)."
  value       = sort(tolist(local.secretos))
}

output "urls_servicios" {
  description = "URL de cada servicio de Cloud Run (vacío si crear_servicios = false)."
  value       = { for n, s in google_cloud_run_v2_service.servicio : n => s.uri }
}
