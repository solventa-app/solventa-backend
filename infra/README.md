# infra/

Terraform de GCP para el backend (staging, `us-central1`).

**Todo lo de este directorio es código, no infraestructura desplegada.** Nadie ha corrido
`terraform apply` todavía: no hay proyecto GCP confirmado, no hay protección de gasto activa en
ningún proyecto real, y las cuentas externas que algunas piezas necesitan (MongoDB Atlas) no
existen. Ver la sección "Antes de desplegar" antes de intentarlo.

## Qué crea (todo detrás de variables opt-in con default `false`, salvo las APIs base y Artifact
## Registry, que son gratis o centavos)

| Recurso | Variable | Costo aproximado | Nota |
|---|---|---|---|
| APIs: Cloud Run, Artifact Registry, Secret Manager, Pub/Sub | (siempre) | $0 | `disable_on_destroy = false` |
| Repositorio de Artifact Registry | (siempre) | centavos por GB | imágenes de los 10 servicios/stubs |
| Cloud KMS (key ring + llave de cifrado de campo) | `crear_kms` | ~USD 0,06/mes por versión de llave | un key ring **no se puede borrar** |
| 10 servicios de Cloud Run (bff, auth, risk, rating, payments, acl-worker, consolidador-fuentes, 3 stubs) | `crear_servicios` | casi $0 en reposo (escalan a cero, `min_instance_count = 0`) | requiere que las imágenes ya existan en Artifact Registry con el tag `image_tag` |
| Memorystore Redis (BASIC, 1GB) + conector VPC | `crear_redis` | **~USD 0,12/h juntos** (estimación trasladada del README anterior; no se verificó empíricamente en ningún experimento de `solventa-arquitectura` — ese repo no tiene Redis en Terraform, solo en docker-compose local) | **no escala a cero**: crear y destruir por sesión |
| Cloud SQL PostgreSQL (`db-f1-micro`, 1 instancia, bases `auth` + `payments`) | `crear_cloud_sql` | ~USD 9-12/mes (instancia shared-core + 10GB disco; sin respaldos automáticos para no sumarles costo) | **no escala a cero**: cobra por hora aunque no haya tráfico |
| MongoDB Atlas M0 (RISK) | `crear_mongo_atlas` | $0 (nivel gratuito) | requiere cuenta/organización de Atlas y API keys que **no existen todavía** (ver más abajo) |
| Presupuesto + Pub/Sub + función `frenar-gasto` | `crear_presupuesto` | ~$0 (capa gratuita de Cloud Functions; solo paga las pocas invocaciones que dispara el presupuesto) | requiere `billing_account_id` |
| Workload Identity Federation (pool + provider + service account) | `crear_wif` | $0 | para que GitHub Actions despliegue sin llaves de service account |

No hay nada en este directorio que se aplique solo: cada fila de arriba necesita que alguien ponga
su variable en `true` en `terraform.tfvars` y corra `terraform apply` con confirmación explícita
del equipo (regla de costos de `CLAUDE.md`).

## Decisiones tomadas al construir esto (no estaban escritas antes)

- **Cloud Run en 4 "niveles" de recursos, no un solo `for_each` con los 10 servicios.** Cada
  servicio necesita la URL de otro (igual que los nombres de contenedor en `docker-compose.yml`:
  `ACL_URL`, `AUTH_URL`, etc.). Un único `for_each` donde las 10 instancias leen un mismo `local`
  compartido, y ese `local` referencia varias de esas mismas instancias, produce
  `Error: Cycle` en `terraform validate` (se comprobó al construir esto, no es una suposición). La
  solución fue agrupar en 4 niveles de dependencia, cada uno su propio recurso con `for_each`:
  `nivel_0` (stubs, auth, rating — sin dependencias de otro Cloud Run), `nivel_1` (acl-worker),
  `nivel_2` (risk, payments, consolidador-fuentes), `nivel_3` (bff). Ver el comentario al inicio de
  `cloud-run.tf`.
- **MongoDB: Atlas M0 (gratis), no un replica set autogestionado en Compute Engine.** Se consideraron
  ambas (la segunda replicaría `dev/mongo/init-replica-set.js` en 2 VMs reales). Se eligió Atlas
  porque es gratis y no obliga al equipo a operar Mongo (parches, failover, backups) con cero horas
  de holgura. Limitación conocida y documentada en `mongo-atlas.tf`: M0 no soporta VPC
  peering/Private Endpoint (eso es solo M10+, de pago), así que la lista de acceso de red queda
  abierta a `0.0.0.0/0` — la seguridad depende de usuario/contraseña (en Secret Manager) + TLS
  obligatorio de Atlas. Aceptable para staging sin datos reales del Sprint 1; revisar antes de
  manejar datos de riesgo reales.
- **Protección de gasto: diseño nuevo, no portado.** `CLAUDE.md` y la versión anterior de este README
  decían que había que activar "la protección de gasto de
  `../solventa-arquitectura/proteccion-costos/`". Se verificó (octubre 2026) que esa carpeta **no
  existe** en ningún lugar del repo de arquitectura — ni en la raíz, ni en ningún experimento. No
  había nada que copiar o adaptar. Lo que hay en `presupuesto.tf` + `funciones/frenar-gasto/` es un
  diseño propio con el patrón estándar de GCP (presupuesto -> Pub/Sub -> Cloud Function), construido
  desde cero para esta tarea. Ver los límites honestos documentados en los comentarios de
  `presupuesto.tf` y en el docstring de `frenar-gasto/main.py`: solo actúa sobre Cloud Run (no sobre
  Cloud SQL/Redis/Mongo, que seguirían cobrando), y no es instantáneo.
- **`allow_unauthenticated = true` por defecto para los 10 servicios**, igual que en
  `solventa-arquitectura/experimento-1-acl-kyc/infra`. Los stubs y servicios de este sprint no
  implementan minting de ID tokens de GCP, así que exigir autenticación entre servicios los dejaría
  sin poder llamarse entre sí. Aceptable mientras no haya lógica de negocio ni datos reales;
  replantear antes de manejar datos personales/financieros (regla 6 de `CLAUDE.md`).
- **`deploy-staging.yml` no corre `terraform apply`.** El estado de Terraform es local por ahora (no
  se ha movido a GCS, ver `versions.tf`), así que aplicarlo desde GitHub Actions crearía un estado
  desconectado del que usa el equipo localmente. El workflow solo hace `gcloud run deploy` (sube una
  imagen nueva a un servicio que Terraform ya creó a mano) — Terraform sigue siendo la única fuente
  de verdad para la infraestructura en sí (Redis, Cloud SQL, IAM, etc.).
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
`crear_presupuesto` (SIEMPRE antes de cualquier otra cosa facturable) -> `crear_wif` -> construir y
publicar imágenes (ver más abajo) -> `crear_servicios` -> `crear_redis` / `crear_cloud_sql` /
`crear_mongo_atlas` según qué historia lo necesite.

### Publicar las imágenes antes de `crear_servicios = true`

Igual que en `solventa-arquitectura/experimento-1-acl-kyc/infra`: Cloud Run necesita que la imagen
ya exista en Artifact Registry. Con el repositorio ya creado (`terraform apply` con todo lo demás en
`false` crea igual el repositorio base):

```bash
REGISTRY=$(terraform output -raw registry_url)
for servicio in bff auth risk rating payments acl-worker consolidador-fuentes; do
  docker build -t "$REGISTRY/$servicio:latest" "../services/$servicio"
  docker push "$REGISTRY/$servicio:latest"
done
for stub in open-finance open-data pasarela; do
  docker build -t "$REGISTRY/stub-$stub:latest" "../stubs/$stub"
  docker push "$REGISTRY/stub-$stub:latest"
done
```

## Antes de desplegar (obligatorio)

1. Definir el **proyecto GCP** del equipo y habilitar facturación (crédito de USD 300 / 90 días).
2. Activar la **protección de gasto** (`crear_presupuesto = true` en este mismo directorio — ver
   "Decisiones" arriba: es diseño nuevo, no hay nada previo que activar). Sin ella no se despliega
   nada más.
3. Mover el estado a un bucket de GCS con versionado (ver `versions.tf`).
4. Configurar Workload Identity Federation (`crear_wif = true`) y los secretos `WIF_PROVIDER` /
   `WIF_SERVICE_ACCOUNT` del repo antes de usar `.github/workflows/deploy-staging.yml`.
5. Si se va a usar `crear_mongo_atlas`: crear primero la cuenta/organización de MongoDB Atlas a mano
   (no se puede automatizar sin que exista ya) y exportar `MONGODB_ATLAS_PUBLIC_KEY` /
   `MONGODB_ATLAS_PRIVATE_KEY` en el shell que corre terraform. **Nadie ha hecho esto todavía.**

## Pendiente de confirmación del usuario/equipo antes de que nada de esto se pueda aplicar

- El **`project_id` real** del proyecto GCP de staging (hoy no existe ninguno, según `CLAUDE.md`).
- El **`billing_account_id`** real, si se activa `crear_presupuesto`.
- La **cuenta/organización de MongoDB Atlas** y sus API keys, si se activa `crear_mongo_atlas` — hoy
  no existen.
- Confirmar que `github_repositorio` (`solventa-app/solventa-backend`) sigue siendo el `origin`
  correcto cuando se active `crear_wif`.
- Configurar manualmente en GitHub (Settings > Environments) un revisor obligatorio en el
  environment `staging` que usa `deploy-staging.yml`, si se quiere una aprobación humana además del
  `workflow_dispatch`.

## Pipeline de CI

`.github/workflows/ci.yml` valida el Terraform (`fmt`, `init -backend=false`, `validate`) solo
cuando cambia algo en `infra/`. No aplica nada.

`.github/workflows/deploy-staging.yml` (nuevo) construye y publica imágenes, y corre
`gcloud run deploy` por servicio. Se dispara **solo manualmente** (`workflow_dispatch`) o por un tag
`deploy-staging-*` — nunca en cada push a `main`. Ver los prerrequisitos documentados al inicio de
ese archivo antes de usarlo.

## Patrón de referencia

`../solventa-arquitectura/experimento-1-acl-kyc/infra/` (Cloud Run + Artifact Registry, desplegado y
destruido el 2026-09-12). Ese experimento **no incluye Memorystore/Redis en Terraform** (solo en su
`docker-compose.yml` local) — la estimación de costo de Redis + conector VPC de este README es una
cifra de referencia estándar de GCP, no algo medido en ese experimento; dejarlo así de explícito para
no sugerir una verificación que no ocurrió.
