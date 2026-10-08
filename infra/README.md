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
- **Acceso entre servicios por IAM, no `allUsers` global.** `servicios.tf` crea una service account
  de runtime por servicio y solo otorga `roles/run.invoker` a quien el manifiesto declara en `llama`;
  `allUsers` solo se concede a los servicios marcados `publico: true` (hoy, `bff`). Más estricto que
  el patrón de `solventa-arquitectura/experimento-1-acl-kyc/infra` (que sí usa `allUsers` para todos).
- **`deploy-staging.yml` migra antes de desplegar y no corre `terraform apply`.** El estado de
  Terraform es local por ahora (no se ha movido a GCS, ver `versions.tf`), así que aplicarlo desde
  GitHub Actions crearía un estado desconectado del que usa el equipo localmente. El workflow
  construye la imagen (tag = SHA), corre el Cloud Run Job de migración y solo entonces actualiza la
  revisión del servicio — Terraform sigue siendo la única fuente de verdad para la infraestructura en
  sí (Redis, Cloud SQL, IAM, etc., que siguen fuera de este pipeline).
- **Secretos:** Cloud SQL y Mongo Atlas generan su contraseña con `random_password` (nunca un valor
  fijo en el código) y la guardan solo en Secret Manager; Cloud Run los lee con
  `value_source.secret_key_ref`, nunca como texto plano en una variable de entorno visible en el
  estado o en la consola de Cloud Run.

## Uso

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # completar project_id; todo lo demás queda en false
terraform init
terraform fmt -check -recursive && terraform validate
terraform plan          # solo lectura (necesita credenciales de un proyecto real)
terraform apply         # CREA recursos: confirmar con el equipo antes, uno por uno
```

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
3. Mover el estado a un bucket de GCS con versionado (ver `versions.tf`).
4. `crear_pipeline = true` → `terraform apply` → copiar los outputs a GitHub (ver "Conectar GitHub con GCP").
5. `crear_servicios = true` → `terraform apply` → **cargar el valor real de cada secreto** (ver "Cargar los secretos").
6. Primer despliegue: Actions > *Deploy staging* > *Run workflow* con `servicios = todos` (los servicios nacen con la
   imagen de ejemplo `imagen_inicial`; este paso pone las reales). Desde ahí, cada merge a `main` despliega solo lo que cambió.
7. Si se va a usar `crear_mongo_atlas`: crear primero la cuenta/organización de MongoDB Atlas a mano
   (no se puede automatizar sin que exista ya) y exportar `MONGODB_ATLAS_PUBLIC_KEY` /
   `MONGODB_ATLAS_PRIVATE_KEY` en el shell que corre terraform. **Nadie ha hecho esto todavía.**

## Conectar GitHub con GCP

Con `crear_pipeline = true`, `terraform output` entrega los valores. En GitHub: Settings > Environments > **New environment
`staging`** (aquí se pueden exigir revisores y limitar a la rama `main`), y en Settings > Secrets and variables > Actions >
**Variables**:

| Variable | Valor |
|---|---|
| `GCP_PROJECT_ID` | tu `project_id` |
| `GCP_WIF_PROVIDER` | `terraform output -raw wif_provider` |
| `GCP_DEPLOY_SA` | `terraform output -raw deploy_sa` |
| `GCP_PROTECCION_GASTO` | `activa` — solo después del paso 2 de arriba |
| `GCP_REGION`, `GCP_REGISTRY_REPO` | opcionales (`us-central1`, `solventa-backend`) |

Hasta que las tres primeras y `GCP_PROTECCION_GASTO` existan, `deploy-staging.yml` y `rollback-staging.yml` no hacen nada.

## Cargar los secretos

`terraform output secretos_a_cargar` lista los secretos. Terraform crea el contenedor con un valor marcador
(`pendiente-de-cargar`); el real se agrega aparte, y nunca queda en el estado de Terraform:

```bash
printf '%s' 'postgresql://USUARIO:CLAVE@/auth?host=/cloudsql/PROYECTO:REGION:INSTANCIA' \
  | gcloud secrets versions add database-url-auth --data-file=-
```

Una migración con el valor marcador falla fuerte y a propósito. `mongo-uri-rating` debe ser un usuario de **solo lectura**
(RATING nunca escribe en Riesgo) y `mongo-uri-risk` el de lectura/escritura.

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

- El **`project_id` real** del proyecto GCP de staging (hoy no existe ninguno, según `CLAUDE.md`).
- El **`billing_account_id`** real, si se activa `crear_presupuesto`.
- La **cuenta/organización de MongoDB Atlas** y sus API keys, si se activa `crear_mongo_atlas` — hoy
  no existen.
- Confirmar que `repositorio_github` (`solventa-app/solventa-backend`) sigue siendo el `origin`
  correcto cuando se active `crear_pipeline`.
- Configurar manualmente en GitHub (Settings > Environments) un revisor obligatorio en el
  environment `staging` que usa `deploy-staging.yml`, si se quiere una aprobación humana además del
  `workflow_dispatch`.

## Pipeline

- `ci.yml` valida el Terraform (`fmt`, `init -backend=false`, `validate`) cuando cambia `infra/` o `.github/servicios.json`. No aplica nada.
- `deploy-staging.yml` y `rollback-staging.yml` despliegan aplicaciones (no infraestructura): construyen la imagen (tag = SHA),
  migran y solo entonces actualizan la revisión del servicio. Se disparan **solo manualmente** (`workflow_dispatch`) o desde
  el flujo que define `ci.yml` tras el merge a `main` — nunca aplican Terraform.

## Patrón de referencia

`../solventa-arquitectura/experimento-1-acl-kyc/infra/` (Cloud Run + Artifact Registry, desplegado y destruido el 2026-09-12,
verificado contra ese repo). Ese experimento **no incluye Memorystore/Redis en Terraform** (solo en su `docker-compose.yml`
local) — la estimación de costo de Redis + conector VPC de este README es una cifra de referencia estándar de GCP, no algo
medido en ese experimento; dejarlo así de explícito para no sugerir una verificación que no ocurrió.

## Estado de verificación

Validado: `terraform fmt -check`, `terraform validate` y la evaluación offline de los `for_each`/manifiesto con `terraform console`.
**No** se ha corrido `plan` ni `apply` (no hay proyecto GCP): espere ajustes en la primera ejecución real, en particular la
deriva de atributos que el pipeline modifica (por eso `servicios.tf` ignora imagen, etiquetas, cliente y tráfico).
