# infra/

Terraform de GCP para el backend (staging, `us-central1`). El flujo completo de CI/CD está en
[ADR-07](../docs/adr/ADR-07-cicd-y-migraciones.md).

## Qué crea

Lo nuevo va detrás de banderas **apagadas por defecto** (como `crear_kms`): un `terraform plan` sobre el estado actual no
agrega nada hasta que alguien las enciende.

| Recurso | Bandera | Costo | Nota |
|---|---|---|---|
| APIs (Cloud Run, Artifact Registry, Secret Manager, Pub/Sub, IAM, IAM Credentials, STS, Resource Manager) | siempre | $0 | `disable_on_destroy = false` |
| Repositorio de Artifact Registry | siempre | centavos por GB | imágenes de los servicios |
| Cloud KMS (key ring + llave de cifrado de campo) | `crear_kms` | ~USD 0,06/mes por versión de llave | un key ring **no se puede borrar** |
| Workload Identity Federation (GitHub) + service account de despliegue + sus permisos | `crear_pipeline` | $0 | `cicd.tf`; sin llaves; limitada a este repo y al entorno `staging` |
| 9 servicios de Cloud Run, 3 jobs de migración, una service account por servicio, secretos | `crear_servicios` | ~$0 en reposo | `servicios.tf`; generados desde `.github/servicios.json`; máx. 2 instancias por servicio |

Cloud Run y Cloud Run Jobs escalan a cero. Secret Manager cobra ~USD 0,06/mes por versión activa (una marcadora por secreto).

**Sigue fuera de Terraform** (se agrega cuando haya decisión, porque cobra aunque no se use): PostgreSQL de staging (Cloud SQL),
Redis (Memorystore + conector VPC, ~USD 0,12/h juntos: crear y destruir por sesión) y MongoDB (Atlas M0, gratis). El pipeline
solo necesita que existan los secretos `database-url-*`, `mongo-uri-*` y `redis-url-*` con su valor real.

## Uso

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # completar project_id
terraform init
terraform fmt -check -recursive && terraform validate
terraform plan          # solo lectura (necesita credenciales)
terraform apply         # CREA recursos: confirmar con el equipo antes
```

## Antes de desplegar (obligatorio, en este orden)

1. Definir el **proyecto GCP** del equipo y habilitar facturación (crédito de USD 300 / 90 días).
2. Activar la **protección de gasto** de `../solventa-arquitectura/proteccion-costos/` en ese proyecto (presupuesto + función
   `frenar-gasto`). Sin ella no se despliega.
3. Mover el estado a un bucket de GCS con versionado (ver `versions.tf`).
4. `crear_pipeline = true` → `terraform apply` → copiar los outputs a GitHub (ver abajo).
5. `crear_servicios = true` → `terraform apply` → **cargar el valor real de cada secreto** (ver abajo).
6. Primer despliegue: Actions > *Deploy staging* > *Run workflow* con `servicios = todos` (los servicios nacen con una
   imagen de ejemplo; este paso pone las reales). Desde ahí, cada merge a `main` despliega solo lo que cambió.

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
| Servicios del Sprint 1 | Cloud Run por servicio (escala a cero) | casi $0 en reposo; `max_instancias = 2` acota ráfagas |
| RATING / ACL (Redis) | Memorystore + conector VPC (no escalan a cero) | **~USD 0,12/h juntos**: crear y destruir por sesión |
| AUTH / PAYMENTS | Cloud SQL PostgreSQL (o instancia compartida pequeña) | cobra aunque esté detenido el uso |
| RISK | MongoDB Atlas M0 (gratis) | límites del nivel gratuito |

Para apagar los servicios de la sesión: `terraform destroy -target=...` sobre `google_cloud_run_v2_service.servicio` y
`google_cloud_run_v2_job.migracion` (los servicios y jobs no tienen protección contra borrado en staging). **No** destruyas el
key ring de KMS (no se puede borrar) ni los secretos si ya cargaste valores reales.

## Pipeline

- `ci.yml` valida el Terraform (`fmt`, `init -backend=false`, `validate`) cuando cambia `infra/` o `.github/servicios.json`. No aplica nada.
- `deploy-staging.yml` y `rollback-staging.yml` despliegan aplicaciones (no infraestructura). **Terraform nunca se aplica
  desde el pipeline.**
- Patrón de referencia anterior: `../solventa-arquitectura/experimento-1-acl-kyc/infra/` (Cloud Run + Memorystore + conector VPC + Artifact Registry, desplegado y destruido el 2026-09-19).

## Estado de verificación

Validado: `terraform fmt -check`, `terraform validate` y la evaluación offline de los `for_each` con `terraform console`.
**No** se ha corrido `plan` ni `apply` (no hay proyecto GCP): espere ajustes en la primera ejecución real, en particular la
deriva de atributos que `gcloud run deploy` modifica (por eso `servicios.tf` ignora imagen, etiquetas, cliente y tráfico).
