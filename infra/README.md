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
| Cloud KMS (key ring + llave de cifrado de campo) | `crear_kms` | ~USD 0,06/mes por versión de llave | un key ring **no se puede borrar** |
| Workload Identity Federation (GitHub) + service account de despliegue + sus permisos | `crear_pipeline` | $0 | `cicd.tf`; sin llaves; limitada a este repo y al entorno `staging` |
| Un servicio de Cloud Run por entrada de `.github/servicios.json`, sus jobs de migración, una service account por servicio y los secretos | `crear_servicios` | ~$0 en reposo | `servicios.tf`; máx. `max_instancias` por servicio |
| Memorystore Redis (BASIC, 1GB) + conector VPC | `crear_redis` | **~USD 0,12/h juntos** (estimación estándar de GCP; no se verificó empíricamente en ningún experimento de `solventa-arquitectura` — ese repo no tiene Redis en Terraform, solo en docker-compose local) | **no escala a cero**: crear y destruir por sesión |
| Cloud SQL PostgreSQL (`db-f1-micro`, 1 instancia, bases `auth` + `payments`) | `crear_cloud_sql` | ~USD 9-12/mes (instancia shared-core + 10GB disco; sin respaldos automáticos para no sumarles costo) | **no escala a cero**: cobra por hora aunque no haya tráfico |
| MongoDB Atlas M0 (RISK) | `crear_mongo_atlas` | $0 (nivel gratuito) | requiere cuenta/organización de Atlas y API keys que **no existen todavía** (ver más abajo) |
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
- **Postgres desde Cloud Run.** Con `crear_cloud_sql`, los servicios con un secreto `database-url-*` (hoy `auth` y
  `payments`) y sus jobs de migración montan el socket de Cloud SQL en `/cloudsql`, y solo sus cuentas
  `rt-<servicio>` reciben `roles/cloudsql.client`. Sin ese montaje la URL `...@/auth?host=/cloudsql/...` no
  conecta y la migración falla antes de aplicar nada.

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
`database-url-auth` y `database-url-payments`, y con `crear_mongo_atlas` escribe `mongo-uri-risk`: esos no hay que
cargarlos. El resto (`mongo-uri-rating`, `redis-url-*`, `clave-jwt`, `clave-cliente-id`) se agrega aparte, y ese valor
nunca queda en el estado de Terraform. Ejemplo (solo si no usas `crear_cloud_sql`):

```bash
printf '%s' 'postgresql://USUARIO:CLAVE@/auth?host=/cloudsql/PROYECTO:REGION:INSTANCIA' \
  | gcloud secrets versions add database-url-auth --data-file=-
```

Una migración con el valor marcador falla fuerte y a propósito. `mongo-uri-rating` debe ser un usuario de **solo lectura**
(RATING nunca escribe en Riesgo) y `mongo-uri-risk` el de lectura/escritura.

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

`clave-cifrado` es la llave AES-256 (32 bytes en base64) con la que AUTH cifra el nombre de los clientes (KAN-28).
AUTH se niega a arrancar en Cloud Run con el valor marcador o con la llave local. **Rotarla sin migrar los datos deja
los nombres ilegibles** (los cobros saldrían sin nombre): antes de rotar hay que re-cifrar. Pendiente: pasar a Cloud KMS
(decisión D-04) detrás de la misma interfaz:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' | gcloud secrets versions add clave-cifrado --data-file=-
```

**Primer usuario BACKOFFICE en staging (pendiente, manual, NO probado):** la API exige un BACKOFFICE para crear
usuarios, así que el primero se crea con la misma imagen de AUTH, en un Cloud Run Job temporal que se borra al terminar
(así la clave no queda guardada en la configuración del job):

```bash
IMAGEN=$(gcloud run services describe auth --region us-central1 --format='value(spec.template.spec.containers[0].image)')
gcloud run jobs create crear-backoffice --region us-central1 --image "$IMAGEN" \
  --service-account "rt-auth@${PROYECTO}.iam.gserviceaccount.com" \
  --set-cloudsql-instances "${PROYECTO}:us-central1:<instancia>" \
  --set-secrets DATABASE_URL=database-url-auth:latest \
  --set-env-vars CLAVE_INICIAL="$CLAVE_BACKOFFICE" \
  --command python --args=-m,app.crear_usuario,--usuario,admin.solventa,--rol,BACKOFFICE
gcloud run jobs execute crear-backoffice --region us-central1 --wait
gcloud run jobs delete crear-backoffice --region us-central1 --quiet
```

Nunca se ha ejecutado (no hay proyecto GCP): revisa los nombres reales con `terraform output` antes de usarlo.

## Apagar y encender lo que cuesta (sin depender de nadie)

Staging es sintético y sin usuarios: lo que **cobra por hora aunque nadie lo use** no tiene por qué estar encendido todo
el tiempo. Cualquiera del equipo puede apagarlo y volver a subirlo con un botón de GitHub.

| Recurso | Cobra por hora | Al apagar | Al encender |
|---|---|---|---|
| **Cloud SQL** (`auth`, `payments`) | Sí (~USD 9-12/mes con `db-f1-micro`, según este README) | Se **detiene**: conserva las bases, los usuarios y las migraciones; solo se paga el disco | Arranca (1-3 min); no hay que volver a migrar |
| **Redis + conector VPC** (`rating`, `acl-worker`, `consolidador-fuentes`) | Sí (~USD 0,12/h juntos, según este README) | Se **borra** (es una caché) | Se vuelve a crear (10-15 min) |
| Cloud Run, Secret Manager, MongoDB Atlas M0 | No / casi cero / gratis | No se toca | No se toca |

### El botón

*GitHub → Actions → **Costos staging** → Run workflow → elige la acción:*

- **`estado`**: solo informa qué está encendido y qué apagado. No cambia nada.
- **`apagar`**: detiene Cloud SQL y borra Redis y su conector.
- **`encender`**: arranca Cloud SQL y vuelve a crear Redis y el conector.

El resumen de la ejecución muestra el plan antes de aplicarlo y el estado en que queda.

**Seguridad:** `apagar` solo puede **borrar** Redis y su conector (la guardia de `scripts/revisar_plan.py` falla sin aplicar
nada si el plan destruyera otra cosa, y un reemplazo nunca se permite). Cloud SQL no se borra: se detiene.

**Qué recuerda el sistema:** el estado queda en `gs://<proyecto>-tfstate/solventa-backend/costos/estado` (`apagado` o
`encendido`). El despliegue automático (`deploy-staging.yml`) lo respeta: **un release no vuelve a encender lo que está
apagado**. Sin marca guardada se considera `encendido`.

**Después de encender**, los servicios toman las URLs nuevas al arrancar su próxima revisión (Terraform ya crea una). Si
alguno sigue fallando, lanza *Deploy staging* solo para ese servicio. Mientras está apagado, los servicios que dependen de
Cloud SQL o Redis no atienden: es lo esperado.

**Apagado automático (opcional):** el workflow no se ejecuta solo. Para apagar cada noche, descomenta el bloque `schedule`
de `.github/workflows/costos-staging.yml`.

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
terraform apply -var-file=staging.tfvars -var=cloud_sql_encendida=false -var=crear_redis=false
printf apagado | gcloud storage cp - gs://solventa-511815-tfstate/solventa-backend/costos/estado

# ENCENDER
terraform apply -var-file=staging.tfvars
printf encendido | gcloud storage cp - gs://solventa-511815-tfstate/solventa-backend/costos/estado
```

**No** apagues Cloud SQL con `gcloud sql instances patch` ni borres Redis desde la consola: Terraform lo vería como deriva y
lo deshacería en el próximo despliegue.

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
despliegues dejan `template.labels` (la etiqueta `commit-sha`) y `template.revision` en la plantilla de cada servicio, y sin
ignorarlos cada `apply` de infra recreaba una revisión de TODOS los servicios. Ahora `servicios.tf` también ignora esos dos
atributos; comprobado con un `plan` real contra el estado con deriva (sin la corrección aparecía el servicio como
«modificar», con ella no).

**Sin verificar todavía:** el primer arranque de los servicios con base de datos y caché (`auth`, `payments`, `risk`,
`rating`, `acl-worker`), que solo se puede comprobar con Cloud SQL, MongoDB Atlas y Redis creados (`crear_cloud_sql`,
`crear_mongo_atlas` y `crear_redis` están en `false` hoy).

**Redis:** los servicios que declaran un secreto `redis-url-*` (`rating`, `acl-worker`, `consolidador-fuentes`) llevan
`vpc_access` hacia el conector de `redis.tf` cuando `crear_redis = true` (Memorystore solo tiene IP privada), y Terraform
escribe las tres URLs `redis-url-*` en Secret Manager con la contraseña (AUTH) que genera Memorystore: no hay que cargar
nada a mano. **Sin probar en ejecución:** conectividad real servicio → Memorystore, que solo se comprueba con Redis creado
(`crear_redis = false` hoy; cuesta ~USD 0,12/h según este README).
