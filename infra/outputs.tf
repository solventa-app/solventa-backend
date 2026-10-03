output "registry_url" {
  description = "Prefijo para publicar imágenes: <registry_url>/<servicio>:<tag>."
  value       = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.imagenes.repository_id}"
}

output "kms_llave_campos_personales" {
  description = "ID de la llave de cifrado de campo (null si crear_kms = false)."
  value       = var.crear_kms ? google_kms_crypto_key.campos_personales[0].id : null
}
