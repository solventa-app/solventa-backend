# Configuración de STAGING que aplica el pipeline (deploy-staging.yml) al mergear a `develop`; también la que
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

# Con costo por hora aunque no haya tráfico: se activan en su fase, con el cambio revisado en un PR.
crear_kms         = false
crear_redis       = false
crear_cloud_sql   = false
crear_mongo_atlas = false
