# infra/

Terraform de GCP para el backend (staging, `us-central1`). El flujo completo de CI/CD está en
[ADR-07](../docs/adr/ADR-07-cicd-y-migraciones.md).

**Todo lo de este directorio es código, no infraestructura desplegada.** Nadie ha corrido
`terraform apply` todavía: no hay proyecto GCP confirmado, no hay protección de gasto activa en
ningún proyecto real, y las cuentas externas que algunas piezas necesitan (MongoDB Atlas) no
existen. Ver la sección "Antes de desplegar" antes de intentarlo.

## Qué crea

Lo nuevo va detrás de banderas **apagadas por defecto** (como `crear_kms`): un `terraform plan` sobre el estado actual no
agrega nada hasta que alguien las enciende.

| Recurso | Bandera | Costo aproximado | Nota |
|---|---|---|---|
| APIs (Cloud Run, Artifact Registry, Secret Manager, Pub/Sub, IAM, IAM Credentials, STS, Resource Manager) | siempre | $0 | `disable_on_destroy = false` |
| Repositorio de Artifact Registry | siempre | centavos por GB | imágenes de los servicios/stubs |
| Cloud KMS (key ring `solventa-backend` + llave `campos-personales` + IAM para AUTH, RISK y RATING) | `crear_kms` | ~USD 0,06/mes por versión de llave + ~USD 0,03 / 10.000 ops | `kms.tf`; un key ring **no se puede borrar** nunca; activado en `staging.tfvars` |
| CMEK en Cloud SQL y Memorystore Redis | `usar_cmek_sql` / `usar_cmek_redis` | $0 adicional (usa la misma llave de KMS) | Cloud SQL exige recrear/migrar la instancia (`export/import` en ventana acordada); Redis se aplica en el próximo encendido |
| Workload Identity Federation (GitHub) + service account de despliegue + sus permisos | `crear_pipeline` | $0 | `cicd.tf`; sin llaves; limitada a este repo y al entorno `staging` |
| Un servicio de Cloud Run por entrada de `.github/servicios.json`, sus jobs de migración, una service account por servicio y los secretos | `crear_servicios` | ~$0 en reposo | `servicios.tf`; máx. `max_instancias` por servicio (`calentar_camino_critico = true` sube `min_instance_count = 1` solo durante sesiones de medición) |
| Cola de Cloud Tasks `reintento-cobros` + trabajos de Cloud Scheduler `sonda-acl` y `reconciliacion-cobros-policy` y `DISPARADOR=externo` en PAYMENTS y el ACL Worker | `crear_tareas` | $0 (capa gratuita: 1 M de operaciones de Cloud Tasks; 3 trabajos de Scheduler por cuenta) | `tareas.tf`. En Cloud Run los bucles de fondo no corren de forma confiable (`cpu_idle = true`, mínimo 0). Ver «Disparadores externos» |
| Bucket privado `<proyecto>-archivos` (CMEK con la llave `archivos`, CORS, ciclo de vida por tipo), aviso del bucket a Pub/Sub `archivos-gcs` → push OIDC a ALMACENAMIENTO, temas `archivo-disponible` y `archivo-rechazado`, base `almacenamiento` en Cloud SQL | `crear_almacenamiento` (requiere `crear_cloud_sql`; CMEK si `crear_kms`) | ~USD 0,02/GB-mes (5 GB gratis en EE. UU.); Pub/Sub en capa gratuita | `almacenamiento.tf`, [ADR-10](../docs/adr/ADR-10-almacenamiento-de-archivos.md). Fija `almacenamiento_origenes_cors` con los orígenes reales del frontend. Sin la bandera, el servicio responde 503 `ALMACEN_NO_CONFIGURADO` |
| Memorystore Redis (BASIC, 1GB) + conector VPC | `crear_redis` | **~USD 0,12/h juntos** (estimación estándar de GCP; no se verificó empíricamente en ningún experimento de `solventa-arquitectura` — ese repo no tiene Redis en Terraform, solo en docker-compose local) | **no escala a cero**: crear y destruir por sesión (`redis_encendida`) |
| Cloud SQL PostgreSQL (`db-f1-micro`, 1 instancia, bases `auth`, `payments` + `policy`) | `crear_cloud_sql` | ~USD 9-12/mes (instancia shared-core + 10GB disco; sin respaldos automáticos para no sumarles costo) | **no escala a cero**: cobra por hora aunque no haya tráfico (`cloud_sql_encendida`) |
| MongoDB Atlas M0 (RISK y RATING) | `crear_mongo_atlas` | $0 (nivel gratuito) | sin CMEK en M0 (CMEK exige M10 ~USD 57/mes): cubierto por el cifrado de campo AES-GCM con llave de KMS |
| Pub/Sub (`perfil-actualizado` + suscripción de `rating`) | `crear_pubsub` | $0 (capa gratuita hasta 10 GB/mes) | `pubsub.tf`; `risk` publica y `rating` invalida la caché del score |
| Batch nocturno (`risk-reproceso` Cloud Run Job + Cloud Scheduler `30 23 * * *`) | `crear_batch` | $0 Scheduler (capa gratuita) + minutos de Job | `batch.tf`; corre a las 23:30 America/Bogota, antes del apagado automático de las 2:00 a.m. |
| Métricas basadas en logs (ofertas por tipo, circuito abierto y cobros por estado) | `crear_metricas_logs` | ~$0 (volumen mínimo, capa gratuita de Cloud Monitoring) | `metricas-logs.tf`; las leen los paneles de `hu-w01.json` y `hu-w05.json`. Los filtros asumen el log como `textPayload`: verificar en Logs Explorer tras aplicar |
| API Gateway delante del BFF (`solventa-gateway`, deadline duro de 700 ms) + cuenta `gateway-bff` | `crear_gateway` | $0 hasta 2 M llamadas/mes | `gateway.tf` + `gateway/openapi.yaml`; el BFF deja de ser público y la salida `gateway_url` es la única puerta pública |
| Presupuesto + Pub/Sub + función `frenar-gasto` | `crear_presupuesto` | ~$0 (capa gratuita de Cloud Functions; solo paga las pocas invocaciones que dispara el presupuesto) | requiere `billing_account_id`; debe estar activo **antes** de cualquier despliegue real (regla 2 de `CLAUDE.md`) |

Cloud Run y Cloud Run Jobs escalan a cero. Secret Manager cobra ~USD 0,06/mes por versión activa (una marcadora por secreto).

No hay nada en este directorio que se aplique solo: cada fila de arriba necesita que alguien ponga
su variable en `true` en `terraform.tfvars` y corra `terraform apply` con confirmación explícita
del equipo (regla de costos de `CLAUDE.md`).

## Decisiones tomadas al construir esto (no estaban escritas antes)

- **Cloud Run vía `.github/servicios.json`, sin ciclos entre servicios.** Cada servicio necesita la
  URL de otro (igual que los nombres de contenedor en `docker-compose.yml`: `ACL_URL`, `AUTH_URL`,
  etc.). En vez de leer `.uri` de otro `google_cloud_run_v2_service` (lo que produce
  `Error: Cycle` en cuanto dos servicios se llaman mutuamente por el manifiesto), `servicios.tf`
  calcula la URL de forma determinista a partir del número de proyecto y la región
  (`https://<nombre>-<project_number>.<region>.run.app`, igual al patrón real de Cloud Run) y evita
  así cualquier referencia circular con un único `for_each` sobre los 10 servicios.
- **MongoDB: Atlas M0 (gratis), no un replica set autogestionado en Compute Engine.** Se consideraron
  ambas (la segunda replicaría `dev/mongo/init-replica-set.js` en 2 VMs reales). Se eligió Atlas
  porque es gratis y no obliga al equipo a operar Mongo (parches, failover, backups) con cero horas
  de holgura. Limitación conocida y documentada en `mongo-atlas.tf`: M0 no soporta VPC
  peering/Private Endpoint (eso es solo M10+, de pago), así que la lista de acceso de red queda
  abierta a `0.0.0.0/0` — la seguridad depende de usuario/contraseña (en Secret Manager) + TLS
  obligatorio de Atlas. Aceptable para staging sin datos reales del Sprint 1; revisar antes de
  manejar datos de riesgo reales.
- **Protección de gasto: diseño nuevo, no portado.** `CLAUDE.md` y una versión anterior de este README
  decían que había que activar "la protección de gasto de
  `../solventa-arquitectura/proteccion-costos/`". Se verificó (octubre 2026) que esa carpeta **no
  existe** en ningún lugar del repo de arquitectura — ni en la raíz, ni en ningún experimento. No
  había nada que copiar o adaptar. Lo que hay en `presupuesto.tf` + `funciones/frenar-gasto/` es un
  diseño propio con el patrón estándar de GCP (presupuesto -> Pub/Sub -> Cloud Function), construido
  desde cero para esta tarea. Ver los límites honestos documentados en los comentarios de
  `presupuesto.tf` y en el docstring de `frenar-gasto/main.py`: solo actúa sobre Cloud Run (no sobre
  Cloud SQL/Redis/Mongo, que seguirían cobrando), y no es instantáneo.
- **El presupuesto va en la moneda de la cuenta de facturación.** La del equipo está en COP y GCP rechaza un presupuesto en
  otra moneda, así que `presupuesto_moneda` y `presupuesto_monto` reemplazan al antiguo `presupuesto_monto_usd` (default
  300 USD, que sirve para una cuenta en USD). Con la cuenta en COP: `presupuesto_moneda = "COP"` y
  `presupuesto_monto = 958332` (300 USD a la TRM de 3.194,44 del 10-oct-2026). El tope es una protección, no una
  conversión exacta: si el peso se aprecia mucho, conviene recalcularlo. La función `frenar-gasto` decide por umbral
  relativo (50/80/100 %), así que no depende de la moneda.
- **Lo que dejó ver el primer `apply` real de la protección de gasto** (proyecto `solventa-511815`):
  - La API de presupuestos exige un proyecto de cuota explícito con credenciales de usuario, y tus credenciales locales
    pueden traer otro como cuota (`quota_project_id`). Por eso `google_billing_budget` usa el provider con alias
    `google.facturacion` (`user_project_override` + `billing_project`, ver `versions.tf`), y solo ese recurso. No se
    puso global: con `user_project_override` en todo, habilitar las APIs de un proyecto nuevo falla (403) hasta que
    Cloud Resource Manager esté habilitada, que es una de las APIs que se habilitan ahí.
  - En proyectos nuevos la cuenta de Compute por defecto ya no trae roles. La función `frenar-gasto` se construye con una
    cuenta propia (`frenar-gasto-build`, `roles/cloudbuild.builds.builder`) y recibe el evento con la cuenta de la función
    (`roles/eventarc.eventReceiver`); sin eso el build falla con "missing permission on the build service account".
    Además el build lee el código desde `gcf-v2-sources-<proyecto>-<región>`, un bucket que crea y administra GCP en el
    primer despliegue: la cuenta de build necesita `roles/storage.objectViewer` **sobre ese bucket** (no sobre el proyecto,
    donde leería también el estado de Terraform). En un proyecto nuevo el primer `apply` falla en ese punto y el siguiente pasa.
  - `main.py` necesita `@functions_framework.cloud_event`. Sin el decorador la función **se despliega y aparece `ACTIVE`**,
    pero cada alerta revienta con `TypeError` (el framework la llama con `(data, context)`) y nunca frena nada. Se descubrió
    publicando una alerta de prueba (`alertThresholdExceeded: 0.5`, que solo registra) en `alertas-presupuesto`.
  - `google_billing_account_iam_member` (cicd.tf) pasa por la Cloud Billing API del proyecto de cuota: hay que habilitar
    `cloudbilling.googleapis.com` (está en `presupuesto_apis`) y la propagación tarda uno o dos minutos.
  - La API guarda el filtro del presupuesto con el **número** de proyecto; con el ID, cada `plan` mostraba una diferencia.
- **Acceso entre servicios por IAM, no `allUsers` global.** `servicios.tf` crea una service account
  de runtime por servicio y solo otorga `roles/run.invoker` a quien el manifiesto declara en `llama`;
  `allUsers` solo se concede a los servicios marcados `publico: true` (hoy, `bff`). Más estricto que
  el patrón de `solventa-arquitectura/experimento-1-acl-kyc/infra` (que sí usa `allUsers` para todos).
- **`deploy-staging.yml` aplica Terraform primero, y luego migra y despliega** ([ADR-09](../docs/adr/ADR-09-terraform-desde-el-pipeline.md)).
  Si el merge cambió `infra/` o `.github/servicios.json`, la etapa `infra` hace plan, pasa el guardia
  (`scripts/revisar_plan.py`, que bloquea destrucciones) y aplica; solo entonces construye la imagen
  (tag = SHA), corre el Cloud Run Job de migración y actualiza la revisión del servicio. El estado ya vive en
  GCS, así que el pipeline y el equipo trabajan sobre el mismo.
- **Secretos:** Cloud SQL y Mongo Atlas generan su contraseña con `random_password` (nunca un valor
  fijo en el código) y la guardan en Secret Manager; Cloud Run los lee con `value_source.secret_key_ref`, nunca
  como texto plano en una variable de entorno de la consola de Cloud Run. Ojo: esos valores generados sí quedan
  en el estado de Terraform (ver "Estado de Terraform"); los que se cargan a mano, no.
- **Un solo dueño por secreto.** `servicios.tf` crea el contenedor de **todos** los secretos del manifiesto;
  `cloud-sql.tf` y `mongo-atlas.tf` solo le agregan la versión con el valor real (si `crear_servicios` está activo).
  Así `crear_cloud_sql`/`crear_mongo_atlas` se pueden activar antes o después de `crear_servicios` sin que dos
  recursos intenten crear el mismo `secret_id`.
- **Postgres desde Cloud Run.** Con `crear_cloud_sql`, los servicios con un secreto `database-url-*` (hoy `auth`,
  `payments` y `policy`) y sus jobs de migración montan el socket de Cloud SQL en `/cloudsql`, y solo sus cuentas
  `rt-<servicio>` reciben `roles/cloudsql.client`. Sin ese montaje la URL `...@/auth?host=/cloudsql/...` no
  conecta y la migración falla antes de aplicar nada.

## Disparadores externos (Cloud Tasks y Cloud Scheduler)

Decisión W05-7. Con `crear_tareas = true` (hoy encendida en `staging.tfvars`), Terraform crea la cola `reintento-cobros`
y el trabajo `sonda-acl` y pone `DISPARADOR=externo` (más `URL_PROPIA`, `GCP_PROYECTO`, `GCP_REGION` y `TAREAS_CUENTA`) en
los servicios con `"disparador_externo": true` en `.github/servicios.json` (PAYMENTS y ACL Worker). Sin la bandera, esos
servicios siguen con su bucle (`DISPARADOR=bucle`), que en Cloud Run no es fiable.

| Qué | Quién lo dispara | Endpoint (privado, IAM de Cloud Run) |
|---|---|---|
| Reintento de cobros `pendiente` y recuperación de los atascados en `cobrando` | PAYMENTS programa una tarea de Cloud Tasks cuando un cobro queda `pendiente`; cada pasada programa la siguiente si aún queda alguno | `POST /cobros/_reintentar` de PAYMENTS (token OIDC de su propia cuenta de runtime) |
| Sonda de recuperación del circuito (regla #5) | Cloud Scheduler, cada minuto | `POST /sonda/ejecutar` del ACL Worker |
| Reapertura de pólizas con el pago rechazado o pendiente que se recuperó (W03-14, CA-W03-06) | Cloud Scheduler `reconciliacion-cobros-policy`, cada `reconciliacion_cobros_cron` (15 min por defecto); además el BFF avisa a POLICY tras cada `reintentarCobro` del back-office | `POST /reconciliacion/cobros` de POLICY |
| Reconciliación de fuentes degradadas (W01) | El ACL Worker encola una tarea en la cola `reconciliacion-fuentes` en vez del job de `rq`; la cola reintenta (5 intentos, 10 a 300 s) mientras el consolidador responda 503 | `POST /reconciliar` del consolidador (token OIDC de la cuenta del ACL Worker) |

Con `crear_tareas = true` hay **tres** trabajos de Scheduler en la cuenta (`sonda-acl`, `risk-reproceso-nocturno` del batch y `reconciliacion-cobros-policy`), que es justo el tope de la capa gratuita que documenta `crear_tareas` (3 por cuenta de facturación; no se verificó contra la factura). Si esa cuenta tiene trabajos de otros proyectos, el cuarto ya cobra centavos al mes.

Límites que conviene saber: el estado de los circuitos vive en la memoria de cada instancia, así que la sonda revisa los de
la instancia que recibe la llamada; y una tarea de reconciliación que agota sus 5 intentos se descarta (no hay un registro de
fallidos como el `FailedJobRegistry` de `rq`). Si Cloud Tasks falla al programar, el cobro sigue `pendiente` (se registra una advertencia) y el botón del
back-office lo recupera: nunca se tumba un cobro por eso.

## Uso

```bash
cd infra
export TF_VAR_project_id=<proyecto> TF_VAR_billing_account_id=<cuenta-de-facturacion>   # no van en el repo
terraform init          # el lockfile trae hashes de linux_amd64 (CI) y windows_amd64: no debería modificarlo
terraform fmt -check -recursive && terraform validate
terraform plan -var-file=staging.tfvars    # solo lectura (necesita credenciales de un proyecto real)
```

`staging.tfvars` es la configuración que aplica el pipeline y la que se usa a mano (ver [ADR-09](../docs/adr/ADR-09-terraform-desde-el-pipeline.md)).
Desde el arranque, los cambios en `infra/` los aplica el merge a `main` (un release o un hotfix); **no** hagas `terraform apply` a mano salvo para
el arranque inicial o una emergencia, y siempre con el plan revisado y confirmado por el equipo.
`terraform.tfvars.example` sigue sirviendo para quien monte un entorno propio con un `terraform.tfvars` local (ignorado por git).

## Estado de Terraform

El estado vive en GCS (`gs://solventa-511815-tfstate`, prefijo `solventa-backend`), no en tu máquina: así no se pierde y
el equipo trabaja sobre el mismo. El bucket no lo crea Terraform (huevo y gallina); se creó una sola vez a mano, en
`us-central1`, con acceso uniforme, prevención de acceso público y versionado:

```bash
gcloud storage buckets create gs://solventa-511815-tfstate --project=solventa-511815 --location=us-central1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://solventa-511815-tfstate --versioning
```

- Quien corra `terraform init`/`plan`/`apply` necesita credenciales de aplicación (`gcloud auth application-default login`)
  con acceso al bucket (`roles/storage.objectUser` basta; el propietario del proyecto ya lo tiene). El CI usa
  `init -backend=false`: no toca el bucket.
- Terraform bloquea el estado durante un `apply`; si una corrida se corta y deja el bloqueo, se libera con
  `terraform force-unlock <ID>` (solo si nadie más está aplicando).
- Versionado = se puede volver a una versión anterior del estado (`gcloud storage ls --all-versions gs://solventa-511815-tfstate/**`).
- **El estado contiene secretos:** las contraseñas generadas por `random_password` y las URLs de conexión que Terraform
  escribe en Secret Manager (Cloud SQL, Mongo Atlas) quedan en el estado en claro. Dar acceso al bucket equivale a dar
  acceso a esos secretos: no se amplía más allá de quien aplica Terraform.

Secuencia recomendada para activar algo (nunca todo junto): `crear_kms` (si aplica) ->
`crear_presupuesto` (SIEMPRE antes de cualquier otra cosa facturable) -> `crear_pipeline` ->
`crear_servicios` (los servicios nacen con `imagen_inicial`, una imagen de ejemplo; el primer
despliegue real la reemplaza, ver abajo) -> `crear_redis` / `crear_cloud_sql` / `crear_mongo_atlas`
según qué historia lo necesite.

## Antes de desplegar (obligatorio, en este orden)

1. Definir el **proyecto GCP** del equipo y habilitar facturación (crédito de USD 300 / 90 días).
2. Activar la **protección de gasto** (`crear_presupuesto = true` en este mismo directorio — ver
   "Decisiones" arriba: es diseño nuevo, no hay nada previo en `../solventa-arquitectura/proteccion-costos/`
   que copiar, se verificó que esa carpeta no existe). Sin ella no se despliega nada más.
3. Estado en un bucket de GCS con versionado: ya hecho (ver "Estado de Terraform").
4. `crear_pipeline = true` → `terraform apply` → copiar los outputs a GitHub (ver "Conectar GitHub con GCP").
5. `crear_servicios = true` → `terraform apply` → **cargar el valor real de cada secreto** (ver "Cargar los secretos").
6. Primer despliegue: Actions > *Deploy staging* > *Run workflow* **eligiendo la rama `main`** (por defecto GitHub propone
   `develop`, que no despliega) con `servicios = todos` (los servicios nacen con la imagen de ejemplo `imagen_inicial`; este
   paso pone las reales). Desde ahí, cada release o hotfix que llega a `main` despliega solo lo que cambió. Un merge a
   `develop` no despliega nada.
7. Si se va a usar `crear_mongo_atlas`: crear primero la cuenta/organización de MongoDB Atlas a mano
   (no se puede automatizar sin que exista ya) y exportar `MONGODB_ATLAS_PUBLIC_KEY` /
   `MONGODB_ATLAS_PRIVATE_KEY` en el shell que corre terraform. **Nadie ha hecho esto todavía.**

## Conectar GitHub con GCP

Con `crear_pipeline = true`, `terraform output` entrega los valores. En GitHub: Settings > Environments > **New environment
`staging`** (aquí se pueden exigir revisores; ver más abajo por qué admite `main` y `develop`), y en Settings > Secrets and variables > Actions >
**Variables**:

| Variable | Valor |
|---|---|
| `GCP_PROJECT_ID` | tu `project_id` |
| `GCP_WIF_PROVIDER` | `terraform output -raw wif_provider` |
| `GCP_DEPLOY_SA` | `terraform output -raw deploy_sa` |
| `GCP_TERRAFORM_SA` | `terraform output -raw terraform_sa` (solo para la etapa `infra`) |
| `GCP_BILLING_ACCOUNT_ID` | ID de la cuenta de facturación (solo para la etapa `infra`; el presupuesto lo lee con `TF_VAR_billing_account_id`) |
| `GCP_PROTECCION_GASTO` | `activa` — solo después del paso 2 de arriba, y con la función `frenar-gasto` desplegada |
| `GCP_REGION`, `GCP_REGISTRY_REPO` | opcionales (`us-central1`, `solventa-backend`) |

Hasta que las tres primeras y `GCP_PROTECCION_GASTO` existan, `deploy-staging.yml` y `rollback-staging.yml` no hacen nada.

**Dos entornos de GitHub** (Settings > Environments): `staging` (despliegue de aplicaciones, identidad `GCP_DEPLOY_SA`) e
`infra-staging` (Terraform, identidad `GCP_TERRAFORM_SA`, mucho más potente: propietario del proyecto de staging). En
`infra-staging` se puede exigir revisores antes de cada apply. Los dos están limitados a las ramas `main` y `develop`: en
`workflow_run` GitHub toma como ref la rama por defecto (`develop`), así que restringirlos solo a `main` bloquearía el
despliegue. Que solo `main` despliegue lo garantiza `deploy-staging.yml` (`branches: [main]` y la ejecución manual solo desde
`main`); lo que el entorno impide es que una rama de trabajo o un PR asuma la identidad de Terraform. Si un entorno no existe,
GitHub lo crea solo en el primer uso **sin reglas de protección**: créalos antes (ya están creados desde el 2026-10-17).

**Arranque (una vez, a mano):** la cuenta de Terraform se crea con `crear_pipeline`, así que ese primer `apply` lo hace una
persona desde su máquina con `terraform plan -var-file=staging.tfvars` revisado. Después, los cambios en `infra/` los aplica
el merge. Localmente: `export TF_VAR_project_id=... TF_VAR_billing_account_id=...` y siempre `-var-file=staging.tfvars`.

## Cargar los secretos

`terraform output secretos_a_cargar` lista **todos** los secretos del manifiesto. Terraform crea cada contenedor con un
valor marcador (`pendiente-de-cargar`). Con `crear_cloud_sql` (y `crear_servicios`) Terraform ya escribe solo
`database-url-auth`, `database-url-payments` y `database-url-policy`; con `crear_redis`, `redis-url-*`; y con `crear_mongo_atlas`,
`mongo-uri-risk` y `mongo-uri-rating`: esos no hay que cargarlos. Lo que se agrega aparte es `clave-jwt`,
`clave-cliente-id`, `clave-cifrado` y, para M01, `didit-api-key` y `clave-cache-identidad` (ver «Didit»), y para el correo real `resend-api-key` (ver «Resend»), y ese valor nunca queda en el estado de Terraform. Ejemplo (solo si no usas `crear_cloud_sql`):

```bash
printf '%s' 'postgresql://USUARIO:CLAVE@/auth?host=/cloudsql/PROYECTO:REGION:INSTANCIA' \
  | gcloud secrets versions add database-url-auth --data-file=-
```

Una migración con el valor marcador falla fuerte y a propósito. `mongo-uri-rating` es un usuario de **solo lectura**
(RATING nunca escribe en Riesgo) y `mongo-uri-risk` el de lectura/escritura; Terraform crea los dos.

### Didit (verificación de identidad de HU-M01)

El ACL Worker usa `stubs/kyc` mientras `IDENTIDAD_URL` no esté definida, también en staging. Para pasar a Didit:

1. Crea la cuenta y una aplicación en `business.didit.me` y copia su **API key** (la de *sandbox* responde siempre
   aprobado con datos ficticios y no cobra; la real cobra cada llamada que responde 200).
2. Carga la llave y la de la caché cifrada del resultado (32 bytes al azar), desde tu terminal:

```bash
P=solventa-511815
read -rsp "Didit API key: " K; echo; printf '%s' "$K" | gcloud secrets versions add didit-api-key --project=$P --data-file=-; unset K
openssl rand -base64 32 | tr -d '\n' | gcloud secrets versions add clave-cache-identidad --project=$P --data-file=-
```

3. En el `entorno` de `acl-worker` (`.github/servicios.json`) agrega `"IDENTIDAD_URL": "https://verification.didit.me"`.
   Sin la llave cargada el ACL no arranca con esa URL, a propósito. Sin `clave-cache-identidad` funciona, pero cada
   reintento se vuelve a cobrar.

### Resend (correo real de los avisos de HU-W03)

El ACL manda los avisos (código de verificación, enlace de firma y póliza emitida con el PDF) por el simulador
(`stubs/notificaciones`) mientras `NOTIFICACIONES_PROVEEDOR` sea `simulado`. Para correo real:

1. Crea la cuenta en `resend.com`, verifica un dominio (registros DNS que da Resend) y crea una API key con permiso de
   **envío** («Sending access»). Sin dominio verificado, el remitente de prueba `onboarding@resend.dev` solo entrega al
   correo del dueño de la cuenta. El plan gratis permite 100 correos al día y 3.000 al mes.
2. Carga la llave:

```bash
P=solventa-511815
read -rsp "Resend API key: " K; echo; printf '%s' "$K" | gcloud secrets versions add resend-api-key --project=$P --data-file=-; unset K
```

3. En el `entorno` de `acl-worker` (`.github/servicios.json`) cambia `"NOTIFICACIONES_PROVEEDOR": "resend"` y
   `"CORREO_REMITENTE": "Solventa <avisos@TU-DOMINIO>"`. Con `resend` y sin la llave cargada el ACL no arranca, a
   propósito.
4. En POLICY, `URL_FRONT` debe ser la URL real del front: con ella se arma el enlace de firma del correo.

Cada aviso viaja con su id como `Idempotency-Key`: si POLICY lo reintenta (p. ej. tras un corte a los 700 ms), Resend
no manda otro correo. Un aviso sin correo, como el del asesor (AUTH no guarda su contacto), queda `rechazada` y POLICY
lo omite.

### MongoDB Atlas: lo único que hay que hacer a mano

Atlas necesita una cuenta que solo una persona puede crear (gratis, nivel M0). Una vez:

1. Crea la cuenta en `cloud.mongodb.com` (se crea una organización).
2. *Organization → Access Manager → API Keys → Create API Key*: nombre `terraform-solventa`, permiso **Organization
   Project Creator**. Copia la clave pública y la privada (la privada se muestra una sola vez).
3. Los servidores de GitHub no tienen IP fija: en la lista de acceso de esa clave agrega `0.0.0.0/0`, o desactiva *Require
   IP Access List for the Atlas Administration API* en los ajustes de la organización (concesión de seguridad: la clave
   sigue necesitando su secreto).
4. Carga las claves en el vault, desde tu terminal (nunca en el chat ni en GitHub):

```bash
P=solventa-511815
for s in mongodb-atlas-public-key mongodb-atlas-private-key; do
  gcloud secrets create "$s" --project=$P --replication-policy=automatic
done
read -rsp "Public key: " K; echo; printf '%s' "$K" | gcloud secrets versions add mongodb-atlas-public-key --project=$P --data-file=-; unset K
read -rsp "Private key: " K; echo; printf '%s' "$K" | gcloud secrets versions add mongodb-atlas-private-key --project=$P --data-file=-; unset K
```

5. Copia el **Organization ID** (*Organization Settings*; no es secreto) y guárdalo como variable de GitHub
   `MONGODB_ATLAS_ORG_ID` (*Settings → Secrets and variables → Actions → Variables*).
6. Pon `crear_mongo_atlas = true` en `infra/staging.tfvars` (PR) y haz el release. Los workflows `Deploy staging` y
   `Costos staging` leen las claves de Secret Manager, las enmascaran y se las pasan a Terraform.

Atlas M0 es gratis y **no se apaga** con `Costos staging`. Limitación conocida de M0: sin VPC peering ni Private Endpoint,
por eso la lista de acceso de red del clúster queda abierta (`0.0.0.0/0`); la seguridad es usuario y contraseña (en Secret
Manager) más TLS. Revisar antes de manejar datos reales.

`clave-cliente-id` es la clave HMAC con la que el BFF deriva el `cliente_id` opaco a partir del documento de
identidad (ver `services/bff/app/identificadores.py`). Debe ser aleatoria y **no cambiar** mientras haya datos en
RISK (cambiarla deja huérfanos los perfiles existentes). El BFF se niega a arrancar con el valor marcador:

```bash
openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add clave-cliente-id --data-file=-
```

`clave-jwt` es el secreto HS256 con el que AUTH **firma** los JWT y el BFF los **verifica** (los dos servicios leen el
mismo secreto; ver `docs/autenticacion-usuarios.md`). Debe tener al menos 32 bytes: AUTH y el BFF se niegan a arrancar
con el valor marcador, con el valor local o con uno más corto. Rotarlo invalida todas las sesiones abiertas (cada
usuario vuelve a iniciar sesión), no pierde datos:

```bash
openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add clave-jwt --data-file=-
```

`clave-cifrado` es la llave de datos AES-256-GCM (DEK de 32 bytes) con la que AUTH y RISK cifran los campos personales
(y RATING los descifra al leer de la réplica). Con `crear_kms = true` (`kms.tf`), solo las cuentas de servicio
`rt-auth`, `rt-risk` y `rt-rating` tienen `roles/cloudkms.cryptoKeyEncrypterDecrypter` sobre la llave `campos-personales`
(rotación automática cada 90 días). En Cloud Run se soporta **cifrado de sobre (envelope encryption)**: el secreto puede
guardar la llave envuelta por KMS (`kms:<ciphertext_b64>`) para que el servicio la desenvuelva al arrancar sin tener que
re-cifrar las bases cuando KMS rota la llave maestra:

```bash
# Opción 1 (sobre KMS): envolver una DEK de 32 bytes con la llave `campos-personales` y guardarla en Secret Manager
DEK=$(openssl rand 32 | base64 | tr -d '\n')
CIPHERTEXT=$(printf '%s' "$DEK" | base64 -d | gcloud kms encrypt \
  --project=solventa-511815 --location=us-central1 --keyring=solventa-backend --key=campos-personales \
  --plaintext-file=- --ciphertext-file=- | base64 | tr -d '\n')
printf 'kms:%s' "$CIPHERTEXT" | gcloud secrets versions add clave-cifrado --project=solventa-511815 --data-file=-
unset DEK CIPHERTEXT

# Opción 2 (DEK directa en base64 urlsafe):
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' | gcloud secrets versions add clave-cifrado --data-file=-
```

### CMEK en los almacenes de datos (HA-SEG-002, D-04 cerrada)

- **Cloud SQL (`usar_cmek_sql`):** PostgreSQL en Cloud SQL solo acepta `encryption_key_name` **al crear la instancia**.
  Activar `usar_cmek_sql = true` sobre una instancia existente fuerza su reemplazo (que el guardia `scripts/revisar_plan.py`
  bloquea por seguridad). Por eso la migración a CMEK se hace en un PR propio con ventana acordada con el equipo:
  1. Exportar las bases `auth`, `payments` y `policy` con `gcloud sql export sql`.
  2. Mergear `usar_cmek_sql = true` y correr *Deploy staging* con `permitir_destruir = true` para recrear la instancia con CMEK.
  3. Importar los volcados o correr las migraciones y `scripts/crear_usuarios.py`.
- **Memorystore Redis (`usar_cmek_redis`):** como Redis es caché efímera y el botón *Costos staging → apagar* la destruye
  a diario, basta con activar `usar_cmek_redis = true` cuando esté apagada: en el próximo *encender* nace con
  `customer_managed_key` apuntando a `campos-personales`.
- **MongoDB Atlas (`M0`):** se queda en M0 **sin CMEK de disco** (decisión 6 de `docs/plan-completar-hu-w01.md`). En Atlas,
  *Encryption at Rest using Customer Key Management* exige subir el clúster a **M10 (~USD 57/mes)**, casi una quinta parte
  del presupuesto total del equipo. En M0, Atlas cifra el disco por defecto con AES-256 y los campos personales/financieros
  de `risk` se guardan cifrados en la aplicación con AES-256-GCM bajo nuestra llave de KMS (`campos-personales`): sin esa
  llave los documentos de Mongo son ilegibles, cumpliendo HA-SEG-002 y EC022 sin pagar M10.

### API Gateway delante del BFF (`crear_gateway`, HA-LAT-004)

Con `crear_gateway = true` (`gateway.tf`), Terraform crea en `us-central1` el API Gateway `solventa-gateway` configurado con
`infra/gateway/openapi.yaml` (rutas `POST /graphql/web`, `POST /graphql/movil`, `POST /graphql/app`, `GET /health` y `GET /polizas/{poliza_id}/documento`, todas con
`deadline: 0.7` = 700 ms). El BFF **deja de ser público** (sin `allUsers`): solo la cuenta `gateway-bff` tiene
`roles/run.invoker` sobre `bff`. La URL pública sale en `terraform output -raw gateway_url` y en el informe de
`deploy-staging.yml`.

En local, `docker-compose.yml` levanta el servicio `gateway` (Envoy en `:8080`) con `infra/gateway/envoy.yaml`, y
`scripts/validar_gateway.py` verifica en CI que `openapi.yaml` y `envoy.yaml` tengan exactamente las mismas rutas y el
mismo corte de 700 ms. Durante sesiones de medición con k6 se puede activar `calentar_camino_critico = true` para fijar
`min_instance_count = 1` en los servicios del camino crítico de HU-W01 y evitar arranques en frío; al terminar la sesión
se vuelve a poner en `false`.

### API Gateway de los socios (`crear_gateway_socios`, HU-W07)

Con `crear_gateway = true` y `crear_gateway_socios = true` (`gateway.tf`), Terraform crea un segundo gateway,
`solventa-gateway-socios`, configurado con `infra/gateway/openapi-socios.yaml`: las 20 rutas de
`contracts/openapi/socios-v1.json`, con el corte de 700 ms y el token ES256 del socio validado en el borde
(`x-google-jwks_uri`). Usa la misma cuenta `gateway-bff` para invocar al BFF.

Pasos, en orden (nada se aplica a mano; va por el pipeline al llegar a `main`):

1. **Cargar `clave-firma-socios`** en Secret Manager antes de la liberación: AUTH no arranca en Cloud Run con el
   marcador ni con la clave local. Una clave EC P-256 en PKCS#8, como DER en base64 de una sola línea:
   `openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -outform DER | base64 -w0 | gcloud secrets versions add clave-firma-socios --data-file=-`.
2. Liberar con `crear_gateway_socios = true` y `jwks_socios_url = ""`.
3. Poner en `jwks_socios_url` la URL `<terraform output -raw gateway_socios_url>/v1/.well-known/jwks.json` (la sirve
   el BFF sin token, por el mismo gateway) y volver a liberar.
4. Dar de alta los socios con un Cloud Run Job de la imagen de AUTH: `python -m app.crear_socio ...` (no hay pantalla
   de alta, W07-21).

El BFF tiene `roles/monitoring.viewer` y `METRICAS_FUENTE=monitoring` para la salud del sistema (`servicios.tf`). En
local, Envoy replica este gateway en el mismo `:8080` (filtro `jwt_authn` con el JWKS de AUTH) y
`scripts/validar_gateway.py` verifica que el contrato, `openapi-socios.yaml` y `envoy.yaml` tengan las mismas rutas.

### Firma, pago y avisos de HU-W03 (F-B8)

- **Ingreso directo, fuera del gateway.** `avisos` (stream SSE: un stream largo no cabe en el corte de 700 ms) y `stub-pasarela`
  (la página de pago que abre el titular) llevan `ingreso_directo` en `.github/servicios.json`: Terraform les da `allUsers`
  como invocador, igual que haría con `publico`. Si la organización bloquea `allUsers`, esos dos recursos fallan (es
  esperable, como con el gateway). El resto de servicios sigue privado.
- **`url_front`** (variable, sin barra final) es el origen del front web (Vercel). De ella salen `ORIGENES_PERMITIDOS` de
  `avisos`, `URLS_RETORNO_PERMITIDAS` de la pasarela y `URL_FRONT` de POLICY y del BFF (a `<url_front>/pago/retorno/<id>`
  regresa el titular). **Sin fijarla en `staging.tfvars` vale `http://localhost:5173`, y el stream de avisos y el regreso
  de la pasarela no sirven en staging.** El mismo origen va en `almacenamiento_origenes_cors` al encender el almacenamiento.
- **Redis base 3 (`redis-url-w03`)**: tickets del BFF, avisos que publica POLICY y lectura de `avisos`. Con Memorystore apagado,
  la URL marcador no resuelve: los avisos se pierden (de mejor esfuerzo) y `ticketAvisos` responde error con reintento.
- **Cloud Scheduler `purgar-contactos-policy`** (`purgar_contactos_cron`, diario 03:00 Bogotá): `POST /_purgar-contactos` de
  POLICY. Con el almacenamiento encendido, `purga-almacenamiento` (F-B0) borra las subidas abandonadas.
- **Gateway:** `GET /propuestas/{id}/contrato.pdf` en `openapi.yaml` y `envoy.yaml` (`scripts/validar_gateway.py` las compara).
  La cabecera `X-Enlace-Firma` del token del enlace pasa tal cual hasta el BFF.

### Pub/Sub (`crear_pubsub`) y Batch nocturno (`crear_batch`)

- **`crear_pubsub = true` (`pubsub.tf`):** crea el tema `perfil-actualizado` y la suscripción `rating-perfil-actualizado`
  (capa gratuita de Pub/Sub: 10 GB/mes). Otorga `roles/pubsub.publisher` únicamente a `rt-risk` y `roles/pubsub.subscriber`
  únicamente a `rt-rating` para invalidar el score cacheado cuando cambia el perfil.
- **`crear_batch = true` (`batch.tf`, HA-ESC-004 / EC017):** crea el Cloud Run Job `risk-reproceso` (`python -m app.batch_reproceso`,
  con la imagen y cuenta de `risk`) y la tarea de Cloud Scheduler `risk-reproceso-nocturno` programada a las `30 23 * * *`
  (`America/Bogota`), para que termine antes del apagado automático de costos de las 2:00 a.m. `batch_tareas` (default `1`) fija `task_count`: cada tarea procesa una partición de los perfiles, así que para 10 M de perfiles en < 2 h se escala subiendo `batch_tareas` (la cifra sale de la medición de `docs/evidencias/EC017-2026-10-18.md`). **Ojo con Atlas M0:** según su documentación admite como máximo 100 operaciones por segundo para todo el clúster, así que el batch no llegaría a EC017 y competiría con el tráfico en línea de RISK y RATING; para medir EC017 en staging hace falta un nivel de pago (sin medir; ver la sección 5 del informe).

### Secretos de SonarCloud en GitHub Actions (Bloque 6)

El job `sonar` de `.github/workflows/ci.yml` escanea el código con SonarCloud (gratis para repos públicos) en cada push a
`main` o `develop`. Para activarlo, un administrador de la organización carga en *Settings → Secrets and variables → Actions*:

- `SONAR_TOKEN`: token del proyecto `solventa-backend` generado en SonarCloud.
- `SONAR_HOST_URL`: `https://sonarcloud.io`.

### Usuarios de staging: el primer BACKOFFICE y los de prueba

Los usuarios se crean por el BFF, que exige un BACKOFFICE. Mientras staging no tenga ninguno, el **primero** se crea
llamando directo a `POST /usuarios` de AUTH, que no pide sesión: lo protege Cloud Run (solo una identidad con
`roles/run.invoker` sobre `auth` entra; hoy `rt-bff`, `rt-consolidador-fuentes` y la cuenta propietaria del proyecto).
`scripts/crear_usuarios.py --auth` hace esa llamada con tu token de identidad de `gcloud` y crea **solo al operador**
(BACKOFFICE); el CLIENTE y el ASESOR se crean después por el BFF, con su control de rol. Cada clave se genera al azar y,
con `--vault`, va a Secret Manager (`usuario-<alias>`) en vez de la pantalla. El script es idempotente (lo que ya existe
no se toca) y solo usa la biblioteca estándar y `gcloud`. Con Cloud SQL encendida (*Costos staging → encender*):

```bash
PROYECTO=solventa-511815
AUTH_URL=$(gcloud run services describe auth --region us-central1 --project "$PROYECTO" --format='value(status.url)')
BFF_URL=$(gcloud run services describe bff --region us-central1 --project "$PROYECTO" --format='value(status.url)')

# 1) Una sola vez: el primer BACKOFFICE (operador.demo), directo a AUTH
python scripts/crear_usuarios.py --auth "$AUTH_URL" --vault "$PROYECTO"

# 2) El CLIENTE y el ASESOR, por el BFF, con ese operador
export USUARIO_BACKOFFICE=operador.demo
export CLAVE_BACKOFFICE=$(gcloud secrets versions access latest --secret usuario-operador-demo --project "$PROYECTO")
python scripts/crear_usuarios.py --bff "$BFF_URL" --vault "$PROYECTO"
```

En PowerShell cambian las variables: `$AUTH_URL = gcloud run services describe auth --region us-central1 --project solventa-511815 --format='value(status.url)'`
y `$env:CLAVE_BACKOFFICE = gcloud secrets versions access latest --secret usuario-operador-demo --project solventa-511815`.

Los alias por defecto son `cliente.demo`, `asesor.demo` y `operador.demo` (`--cliente`, `--asesor`, `--operador` los
cambian; un alias no puede ser un correo ni tener 6 o más dígitos seguidos). En local no hace falta el paso 1: el compose
ya crea un BACKOFFICE público (`admin.solventa` / `admin-local-solventa`), con el que sirve `python scripts/crear_usuarios.py`.

## Apagar y encender lo que cuesta (sin depender de nadie)

Staging es sintético y sin usuarios: lo que **cobra por hora aunque nadie lo use** no tiene por qué estar encendido todo
el tiempo. Cualquiera del equipo puede apagarlo y volver a subirlo con un botón de GitHub.

| Recurso | Cobra por hora | Al apagar | Al encender |
|---|---|---|---|
| **Cloud SQL** (`auth`, `payments`, `policy`) | Sí (~USD 9-12/mes con `db-f1-micro`, según este README) | Se **detiene**: conserva las bases, los usuarios y las migraciones; solo se paga el disco | Arranca (1-3 min); no hay que volver a migrar |
| **Redis + conector VPC** (`rating`, `acl-worker`, `consolidador-fuentes`) | Sí (~USD 0,12/h juntos, según este README) | Se **borran** la instancia y el conector (es una caché) | Se vuelven a crear (10-15 min) |
| **Instancias fijas** de Cloud Run (`bff`, `auth`, `risk`, `rating`, `payments`, `acl-worker` y los 3 stubs) | Sí (estimado, sin verificar: ~USD 10 al mes cada una; ~USD 3 al día las 9 juntas) | Vuelven a **mínimo 0** instancias y CPU solo en las peticiones | Mínimo 1 otra vez |
| Red, subred y APIs de Redis | No | Se quedan (ver abajo) | No se tocan |
| El resto de Cloud Run (`consolidador-fuentes`), Secret Manager, MongoDB Atlas M0 | No / casi cero / gratis | No se toca | No se toca |

### El botón

*GitHub → Actions → **Costos staging** → Run workflow → elige la acción:*

- **`estado`**: solo informa qué está encendido y qué apagado. No cambia nada.
- **`apagar`**: detiene Cloud SQL, borra Redis y su conector y devuelve las instancias fijas a mínimo 0.
- **`encender`**: arranca Cloud SQL, vuelve a crear Redis y el conector y restituye las instancias fijas.

El resumen de la ejecución muestra el plan antes de aplicarlo y el estado en que queda.

**Seguridad:** `apagar` solo puede **borrar** Redis, su conector y las versiones de los secretos `redis-url-*` (la guardia de
`scripts/revisar_plan.py` falla sin aplicar nada si el plan destruyera otra cosa, y un reemplazo nunca se permite). Cloud SQL
no se borra: se detiene.

**Por qué la red se queda:** el primer apagado automático (2026-10-18) falló al borrar la red `solventa-backend-vpc`: *«cannot
be deleted because it is being used by a Serverless resource»*, porque GCP tarda en liberar el conector. La red, la subred y
las APIs no cuestan, así que `apagar` ya no las toca (variable `redis_encendida`; `crear_redis` sigue siendo «este entorno
tiene Redis»). Ese fallo dejó la marca sin escribir y el siguiente despliegue lo encendió todo de nuevo.

**Qué recuerda el sistema:** el estado queda en `gs://<proyecto>-tfstate/solventa-backend/costos/estado` (`apagado` o
`encendido`). El despliegue automático (`deploy-staging.yml`) lo respeta: **un release no vuelve a encender lo que está
apagado**. Sin marca guardada se considera `encendido`.

**Después de encender**, los servicios toman las URLs nuevas al arrancar su próxima revisión (Terraform ya crea una). Si
alguno sigue fallando, lanza *Deploy staging* solo para ese servicio. Mientras está apagado, los servicios que dependen de
Cloud SQL o Redis no atienden: es lo esperado.

**Apagado automático:** por si se olvida apagarlo, el workflow **apaga solo todos los días a las 2:00 a.m. hora de
Colombia** (`cron: '0 7 * * *'`, 07:00 UTC; Colombia no tiene horario de verano). Así, lo que se encienda para una
prueba no sigue cobrando de un día para otro. Por eso cada mañana estará **apagado**: para usarlo, `encender`.
Como GitHub no garantiza la hora (el 18-oct la ejecución llegó casi 5 h tarde y el 19-oct no llegó), el bloque `schedule`
trae **tres reintentos** (08:30, 10:00 y 12:00 UTC). Apagar es idempotente: si la primera pasada funcionó, las otras no
tienen cambios que aplicar. **Los reintentos solo actúan si ningún apagado programado de ese día terminó bien** (job
`comprobar`, que mira el job `costos` de las ejecuciones `schedule` del día): el 1-nov el primero llegó a las 9:06 a.m.
y funcionó, pero dos reintentos llegaron a las 10:19 y 11:31 a.m. y volvieron a apagar staging justo después de
encenderlo a mano; uno se canceló a mitad del `apply` y dejó el candado del estado puesto. Para quitarlo, borra el bloque `schedule` de `.github/workflows/costos-staging.yml`. Tres cosas a saber:

- GitHub puede demorar o saltarse una ejecución programada, y en un repositorio público las desactiva tras
  60 días sin actividad. Si el 1.er intento falla, GitHub avisa por correo a quien editó el cron por última vez.
- Si alguien está trabajando pasadas las 2:00 a.m., se le apagará: basta con volver a ejecutar `encender`.
- Mientras `main` no traiga este cambio, la ejecución programada se omite con un aviso (no falla cada noche).

**Desplegar con los costos apagados:** un despliegue que tenga que migrar `auth`, `payments` o `policy` mientras Cloud SQL está
detenida **falla de inmediato con un mensaje claro** (*Cloud SQL está detenida… ejecuta Costos staging → encender*) en
vez de un error de conexión. Enciende primero y vuelve a lanzar el despliegue. Un release no vuelve a encender lo apagado.

**Primera vez:** el workflow aplica la infraestructura de `main`, así que solo funciona cuando `main` ya trae este cambio (lo
dice con un error claro si no).

### Plan B: a mano desde Cloud Shell (si el botón no sirve)

Hazlo desde **Cloud Shell o Linux/WSL**, no desde Windows: el zip de la función `frenar-gasto` lleva el MD5 de su contenido
y en Windows sale distinto; un `apply` desde ahí hace que el siguiente despliegue automático bloquee su guardia.

```bash
git clone https://github.com/solventa-app/solventa-backend && cd solventa-backend && git checkout main
cd infra
export TF_VAR_project_id=solventa-511815
export TF_VAR_billing_account_id=<ID de la cuenta de facturación (variable GCP_BILLING_ACCOUNT_ID de GitHub)>
terraform init

# APAGAR: Terraform muestra el plan y pide escribir "yes"; revisa que solo toque Cloud SQL y Redis/conector
terraform apply -var-file=staging.tfvars -var=cloud_sql_encendida=false -var=redis_encendida=false
printf apagado | gcloud storage cp - gs://solventa-511815-tfstate/solventa-backend/costos/estado

# ENCENDER
terraform apply -var-file=staging.tfvars
printf encendido | gcloud storage cp - gs://solventa-511815-tfstate/solventa-backend/costos/estado
```

**No** apagues Cloud SQL con `gcloud sql instances patch` ni borres Redis desde la consola: Terraform lo vería como deriva y
lo deshacería en el próximo despliegue.

## Arranque en frío de Cloud Run

Los servicios tienen **mínimo 0 instancias** (`min_instance_count = 0`): se apagan al no recibir tráfico y la primera
petición los arranca. En staging eso toma de 2 a 8 s por servicio (hasta ~18 s en una petición de AUTH), y el primer login
tras un rato sin uso sumaba el BFF en frío más AUTH en frío.

- **El BFF espera a los demás hasta 15 s** (`SERVICIOS_TIMEOUT_SEGUNDOS` y `ACL_TIMEOUT_SEGUNDOS`, en el manifiesto). Antes
  eran 2 s y 1 s (los valores por defecto del código, que siguen valiendo en local, donde no hay arranque en frío), y el
  primer login fallaba con "servicio no disponible". Ahora tarda, pero entra.
- **`startup_cpu_boost` apagado** (2026-10-19): con el boost cada instancia que arranca pide 2 vCPU y el deploy pasó la cuota de 20 vCPU de la región.
- **No se toca el presupuesto de 700 ms del ACL con los proveedores externos** (`TIMEOUT_MS`, EC009/EC010): es otra
  frontera, la del ACL hacia Open Finance, Open Data y la pasarela.
- **PAYMENTS hacia el ACL** sigue en 3 s: con el ACL frío, un cobro queda `pendiente` y se reintenta (degradación prevista).
- **Instancias fijas (`instancias_fijas_encendidas`, true en `staging.tfvars`):** los servicios que atiende el usuario llevan
  `"instancia_fija": true` en `.github/servicios.json` y corren con **mínimo 1 instancia**: BFF, AUTH, RISK, RATING,
  PAYMENTS, el ACL y los 3 stubs de proveedores (un stub frío tarda más de los 700 ms del ACL y degradaría la oferta). Solo
  `consolidador-fuentes` queda en 0: el BFF no lo llama. La CPU sigue limitada a las peticiones: el reintento de cobros y
  la sonda del circuito los disparan Cloud Tasks y Cloud Scheduler desde fuera (ver «Disparadores externos»), así que no
  hace falta CPU siempre asignada. Agregar un servicio a la lista es una línea en el manifiesto.
- **Cobran por hora y se apagan con el botón** («Costos staging → apagar», y se apaga solo a las 2 a.m. de Colombia): apagadas,
  los servicios vuelven a mínimo 0 y el primer uso tras un rato sin tráfico tarda de nuevo (los 15 s de arriba lo cubren).
  Por la mañana hay que **encender** (unos 10-15 minutos por Redis); las instancias fijas vuelven con ese mismo botón.
- **Cuota de CPU de Cloud Run: 20 vCPU por región** (`cpu_allocation`). Las instancias fijas ocupan 9, así que los despliegues
  en paralelo se limitan: `terraform apply -parallelism=3` y `max-parallel: 4` en el job `desplegar`. Sin eso, un `apply` de
  infra que crea una revisión nueva en los 10 servicios a la vez supera la cuota («Quota exceeded for total allowable CPU per
  project per region», 2026-10-18). Si hiciera falta más, se pide un aumento de la cuota en la consola de GCP.

## Costos y cómo destruir

| Cuándo | Qué | Costo a vigilar |
|---|---|---|
| Servicios del Sprint 1 | Cloud Run por servicio (escala a cero) | casi $0 en reposo; `max_instancias` acota ráfagas |
| RATING / ACL / Consolidador (Redis) | Memorystore + conector VPC (no escalan a cero) | **~USD 0,12/h juntos**: crear y destruir por sesión |
| AUTH / PAYMENTS | Cloud SQL PostgreSQL (o instancia compartida pequeña) | cobra aunque esté detenido el uso |
| RISK | MongoDB Atlas M0 (gratis) | límites del nivel gratuito |

Para apagar los servicios de la sesión: `terraform destroy -target=...` sobre `google_cloud_run_v2_service.servicio` y
`google_cloud_run_v2_job.migracion` (los servicios y jobs no tienen protección contra borrado en staging). **No** destruyas el
key ring de KMS (no se puede borrar) ni los secretos si ya cargaste valores reales.

## Pendiente de confirmación del usuario/equipo antes de que nada de esto se pueda aplicar

- Resuelto el 2026-10-17: el proyecto de staging es `solventa-511815` y su cuenta de facturación ya está vinculada y en uso
  (`billing_account_id` llega por `TF_VAR_billing_account_id`, no por el repositorio).
- La **cuenta/organización de MongoDB Atlas** y sus API keys, si se activa `crear_mongo_atlas` — hoy
  no existen.
- Confirmar que `repositorio_github` (`solventa-app/solventa-backend`) sigue siendo el `origin`
  correcto cuando se active `crear_pipeline`.
- Configurar manualmente en GitHub (Settings > Environments) un revisor obligatorio en el
  environment `staging` que usa `deploy-staging.yml`, si se quiere una aprobación humana además del
  `workflow_dispatch`.

## Pipeline

- `ci.yml` valida el Terraform (`fmt`, `init -backend=false`, `validate`) cuando cambia `infra/` o `.github/servicios.json`. No aplica nada.
- `deploy-staging.yml`, tras el CI del merge a `main` (o a mano desde `main` con `workflow_dispatch`; un merge a `develop` no dispara nada): **`infra`** (si cambió `infra/` o el
  manifiesto: plan → guardia → apply) → construir la imagen (tag = SHA) → migrar → desplegar sin tráfico → humo → promover. Si
  `infra` falla o el guardia bloquea, no se despliega nada. Para permitir una destrucción revisada: *Run workflow* con
  `permitir_destruir`.
- **Falso positivo del guardia al agregar una API.** Si el cambio suma una API a `google_project_service.apis` (por ejemplo
  `telemetry` y `cloudtrace` en el release #167), el plan marca como **reemplazar**
  `google_storage_bucket_iam_member.frenar_gasto_build_fuente[0]` y el guardia bloquea el `apply` ("el plan destruye o
  reemplaza recursos"). No es una destrucción real: `data.google_project.actual` depende de esas APIs, no se lee hasta
  aplicarlas, y el nombre del bucket (que lleva el número del proyecto) queda "desconocido" en el plan. El permiso que se
  recrea es el mismo, el de lectura que la cuenta de build de la función `frenar-gasto` tiene sobre su bucket de código, y
  solo importaría si esa función se recompilara en esos segundos. `google_billing_budget.presupuesto` aparece como
  *modificar* por la misma causa. **Qué hacer:** revisa que el plan solo tenga eso como reemplazo y vuelve a correr el
  workflow a mano desde `main`: *Run workflow* con `servicios=todos`, `infra` y `permitir_destruir` activados. Cualquier otro
  reemplazo o destrucción en el plan sí es real: no lo permitas sin revisarlo. No se quitó el `depends_on` del dato porque en un
  proyecto nuevo la lectura del proyecto debe esperar a que las APIs estén habilitadas.
- **Informe de despliegue** (último job de `deploy-staging.yml`, `informe`): corre siempre que el despliegue arrancó, también si
  una etapa falló, y deja un Markdown con el resultado de cada etapa, lo que quedó corriendo en Cloud Run (servicio, commit,
  acceso y URL, leídos del estado real y no de lo previsto), cómo llamar al BFF y a los servicios privados, y **qué debes hacer**
  (logs y rollback si algo falló, almacenes de datos que faltan, secretos por cargar, reintegrar `main` en `develop`, tag del
  sprint). Está en el resumen de la ejecución (Actions) y como artefacto `informe-despliegue-<n>` durante 90 días. Lo genera
  `scripts/informe_despliegue.py`; si el informe falla no tumba el despliegue.
- `rollback-staging.yml` vuelve una revisión de aplicación a la anterior. **No** revierte infraestructura: para eso se revierte el PR.

## Patrón de referencia

`../solventa-arquitectura/experimento-1-acl-kyc/infra/` (Cloud Run + Artifact Registry, desplegado y destruido el 2026-09-12,
verificado contra ese repo). Ese experimento **no incluye Memorystore/Redis en Terraform** (solo en su `docker-compose.yml`
local) — la estimación de costo de Redis + conector VPC de este README es una cifra de referencia estándar de GCP, no algo
medido en ese experimento; dejarlo así de explícito para no sugerir una verificación que no ocurrió.

## Estado de verificación

Validado: `terraform fmt -check`, `terraform validate`, `terraform plan` contra `solventa-511815` (con las banderas
apagadas: 9 recursos sin costo) y planes de las combinaciones `crear_servicios` / `crear_cloud_sql` / `crear_pipeline` /
`crear_mongo_atlas` (sin secretos repetidos, socket de Cloud SQL solo donde corresponde).

**Aplicado el 2026-10-17** (el arranque manual de ADR-09, desde una máquina con `-var-file=staging.tfvars`): 114 recursos en
el estado y un `plan` final sin cambios. Existen los 10 servicios de Cloud Run (con la imagen de ejemplo `hello`) y los 3
jobs de migración, la cuenta y la federación del pipeline, y la protección de gasto, que se probó de verdad: una alerta de
prueba por debajo del 100 % llega a la función y responde 200. El camino de bloqueo (≥ 100 %) se probó con CloudEvents
simulados por HTTP, no en vivo, porque bloquearía el ingreso de todos los servicios.

**Verificado después, desde GitHub (2026-10-17):** la etapa `infra` y el entorno `infra-staging` corrieron: login por
federación, estado en GCS, `plan`, guardia y `apply` (5 creados, 12 modificados, 1 destruido). Siete servicios (`bff`, los
tres stubs, `consolidador-fuentes`, `payments`, `risk`) ya sirven su imagen real. Ese `apply` destapó una deriva: los
despliegues dejan `template.labels` (la etiqueta `commit-sha`) y `template.revision` (el nombre que `gcloud` le pone a
la revisión) en la plantilla de cada servicio. `servicios.tf` ignora `template.labels`. **`template.revision` NO se puede
ignorar:** Terraform reenviaría el nombre viejo y, en cuanto la plantilla cambie (p. ej. una variable nueva en
`servicios.json`), Cloud Run responde 409 *«Revision named … with different configuration already exists»* y bloquea el
`apply` (pasó con `payments` el 2026-10-18). Costo asumido: un `apply` de infra puede crear una revisión nueva, con la misma
configuración, en los servicios que despliega el pipeline.

**Sin verificar todavía:** el primer arranque de los servicios con base de datos y caché (`auth`, `payments`, `risk`,
`rating`, `acl-worker`), que solo se puede comprobar con Cloud SQL, MongoDB Atlas y Redis creados (`crear_cloud_sql`,
`crear_mongo_atlas` y `crear_redis` están en `false` hoy).

**Redis:** los servicios que declaran un secreto `redis-url-*` (`rating`, `acl-worker`, `consolidador-fuentes`) llevan
`vpc_access` hacia el conector de `redis.tf` cuando `crear_redis = true` (Memorystore solo tiene IP privada), y Terraform
escribe las tres URLs `redis-url-*` en Secret Manager con la contraseña (AUTH) que genera Memorystore: no hay que cargar
nada a mano. **Sin probar en ejecución:** conectividad real servicio → Memorystore, que solo se comprueba con Redis creado
(`crear_redis = false` hoy; cuesta ~USD 0,12/h según este README).

## Paso del BFF al API Gateway sin caída (2026-10-20)

El primer release con `crear_gateway = true` deja el BFF **todavía público** (`publico` + `publico_temporal` en
`.github/servicios.json`; `scripts/validar_servicios.py` solo lo acepta así para el BFF). Orden:

1. Release del backend: se crea el gateway y la web sigue entrando directo al BFF.
2. Con la `gateway_url` del informe del despliegue, el front cambia `apps/web/vercel.json` para apuntar al gateway.
3. Otro release quita `publico` y `publico_temporal` del BFF: desde ahí solo el gateway lo invoca.

Hecho el 2026-10-20: gateway `https://solventa-gateway-btxoyawl.uc.gateway.dev` (pasos 1 y 2 en el front, #67).
El paso 3 destruye `google_cloud_run_v2_service_iam_member.invocador_publico["bff"]`, así que la guardia de
`scripts/revisar_plan.py` frena el apply automático del release: se revisa el plan y se vuelve a correr
*Deploy staging* a mano con `infra` y `permitir_destruir` marcados. `validar_servicios.py` ya no admite la excepción.

