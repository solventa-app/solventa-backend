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
crear_pubsub        = true
crear_metricas_logs = true
crear_batch         = true
crear_gateway       = true
# Gateway de los socios de distribución (HU-W07, `infra/README.md`, «API Gateway de los socios»). Paso 2: se crea con
# `jwks_socios_url` vacío; paso 3: se pone `<gateway_socios_url>/v1/.well-known/jwks.json` y se vuelve a liberar.
crear_gateway_socios    = true
jwks_socios_url         = ""
calentar_camino_critico = false # poner en true solo durante la sesión de medición con k6

# Disparadores externos (plan de HU-W05, bloque 0): Cloud Tasks para el reintento de cobros y Cloud Scheduler para la
# sonda del circuito. Capa gratuita. Sin esto, en Cloud Run no corren los bucles de fondo.
crear_tareas = true

# Instancias fijas (mínimo 1) en los servicios que atiende el usuario: sin arranque en frío en el login ni en la
# oferta. Cobran por hora (estimado ~USD 3 al día todas juntas); «Costos staging → apagar» las devuelve a mínimo 0 y
# «encender» las restituye.
instancias_fijas_encendidas = true

# Almacenamiento de archivos (KAN-250, ADR-10): bucket privado con CMEK, URLs firmadas y avisos por Pub/Sub. Requiere
# crear_cloud_sql (base `almacenamiento`). Encendido para el onboarding de HU-M01 (las 4 piezas suben al bucket antes de
# ir a Didit). ~USD 0,02/GB-mes, con 5 GB gratis en EE. UU.
crear_almacenamiento = true

# Firma, pago y avisos de HU-W03 (F-B8). Origen del front web (Vercel, sin barra final): de aquí salen el Origin permitido
# del stream de avisos, la lista de regreso de la pasarela de prueba y la URL de POLICY (enlace de firma) y del BFF.
url_front = "https://solventa-frontend-pink.vercel.app"
# Mismo origen para las URLs firmadas del bucket (CORS del navegador; la app Android sube sin CORS).
almacenamiento_origenes_cors = ["https://solventa-frontend-pink.vercel.app"]
