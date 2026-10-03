# infra/

Terraform de GCP para el backend (staging, `us-central1`).

## Qué crea hoy

| Recurso | Costo | Nota |
|---|---|---|
| APIs: Cloud Run, Artifact Registry, Secret Manager, Pub/Sub | $0 | `disable_on_destroy = false` |
| Repositorio de Artifact Registry | centavos por GB | imágenes de los servicios |
| Cloud KMS (key ring + llave de cifrado de campo) | ~USD 0,06/mes por versión de llave | **opcional** (`crear_kms`); un key ring **no se puede borrar** |

No hay todavía Cloud Run, Redis ni bases de datos: se agregan por historia, no antes.

## Uso

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # completar project_id
terraform init
terraform fmt -check -recursive && terraform validate
terraform plan          # solo lectura
terraform apply         # CREA recursos: confirmar con el equipo antes
```

## Antes de desplegar (obligatorio)

1. Definir el **proyecto GCP** del equipo y habilitar facturación (crédito de USD 300 / 90 días).
2. Activar la **protección de gasto** de `../solventa-arquitectura/proteccion-costos/` en ese proyecto (presupuesto + función `frenar-gasto`). Sin ella no se despliega.
3. Mover el estado a un bucket de GCS con versionado (ver `versions.tf`).
4. Configurar Workload Identity Federation para el deploy desde GitHub Actions (sin llaves de service account).

## Qué se agrega después, por historia

| Cuándo | Qué | Costo a vigilar |
|---|---|---|
| Servicios del Sprint 1 | Cloud Run por servicio (escala a cero) | casi $0 en reposo |
| RATING / ACL (Redis) | Memorystore + conector VPC (no escalan a cero) | **~USD 0,12/h juntos**: crear y destruir por sesión |
| AUTH / PAYMENTS | Cloud SQL PostgreSQL (o instancia compartida pequeña) | cobra aunque esté detenido el uso |
| RISK | MongoDB Atlas M0 (gratis) | límites del nivel gratuito |
| Pipeline de deploy | `deploy-staging.yml` (aún no existe) | minutos de Actions |

Patrón de referencia: `../solventa-arquitectura/experimento-1-acl-kyc/infra/` (Cloud Run + Memorystore + conector VPC + Artifact Registry, desplegado y destruido el 2026-09-19).

## Pipeline de CI

`.github/workflows/ci.yml` valida el Terraform (`fmt`, `init -backend=false`, `validate`) solo cuando cambia algo en `infra/`. No aplica nada.
