# Configuración de STAGING que aplica el pipeline (deploy-staging.yml) al mergear a `main`; también la que
# debe usar quien aplique a mano: `terraform plan -var-file=staging.tfvars`. Aquí van las banderas: ACTIVAR UNA DE
# COSTO (Cloud SQL, Redis, Atlas) ES MERGEAR UN CAMBIO EN ESTE ARCHIVO, así que se revisa en el PR como cualquier gasto.
#
# Lo que NO va aquí (llega por entorno, TF_VAR_*): project_id y billing_account_id (variables de GitHub
# GCP_PROJECT_ID y GCP_BILLING_ACCOUNT_ID; en local, `export TF_VAR_project_id=...`). Nunca secretos.
region = "us-central1"

crear_presupuesto = true
crear_pipeline    = true
crear_servicios   = true

# La cuenta de facturación está en COP: 300 USD x TRM 3.194,44 (10-oct-2026). Ver infra/README.md.
presupuesto_moneda = "COP"
presupuesto_monto  = 958332

# Cifrado en reposo (bloque 3, D-04 cerrada): key ring y llave `campos-personales` de Cloud KMS (~USD 0,06/mes).
# OJO: un key ring de Cloud KMS no se puede borrar nunca.
crear_kms       = true
usar_cmek_sql   = false # se activa en PR propio con ventana acordada de migración (export/import)
usar_cmek_redis = false # se activa en el próximo encendido de Memorystore

# Almacenes de datos (con costo por hora aunque no haya tráfico: se apagan a diario con costos-staging.yml).
crear_redis       = true
crear_cloud_sql   = true
crear_mongo_atlas = true # Atlas M0: gratis. Lo necesitan risk y rating (infra/README.md, «MongoDB Atlas»)

# Eventos Pub/Sub (bloque 8), Batch nocturno (bloque 9) y API Gateway 700 ms delante del BFF (bloque 10).
crear_pubsub            = true
crear_metricas_logs     = true
crear_batch             = true
crear_gateway           = true
calentar_camino_critico = false # poner en true solo durante la sesión de medición con k6
