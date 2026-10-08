# Solventa — Backend

Backend de **Solventa**, aseguradora digital sobre Open Finance y Open Data (MISW4501 · Proyecto Final 2).
Microservicios por dominio en Python/FastAPI sobre GCP (Cloud Run), con BFF GraphQL, persistencia políglota
(PostgreSQL, MongoDB, Redis) y capa anticorrupción para los proveedores externos.

> Remoto: https://github.com/solventa-app/solventa-backend
>
> Este repo es parte de una estrategia de 3 repos (ver [ADR-06](docs/adr/ADR-06-estrategia-de-repositorios.md)):
> **`solventa-backend`** (este), **`solventa-frontend`** (web Vue 3 + móvil Flutter) y
> **`solventa-arquitectura`** (experimentos de arquitectura, congelado como evidencia).

## Mapa del repo

| Ruta | Contenido |
|---|---|
| `services/` | Un directorio por servicio desplegable: `bff`, `auth`, `risk`, `rating`, `payments`, `acl-worker` (Sprint 1). `under`, `policy`, `claims` y `consolidador` llegan en los Sprints 2 y 3 |
| `stubs/` | Proveedores simulados por contrato: Open Finance, Open Data y pasarela (D-05) |
| `contracts/` | **Contratos compartidos**: esquema GraphQL y eventos. Es lo único que otro repo debe leer |
| `infra/` | Terraform (GCP): APIs, Artifact Registry, KMS opcional, identidad del pipeline y servicios de Cloud Run (opcionales) |
| `.github/` | CI y despliegue (`workflows/`), **`servicios.json`** (fuente única de servicios), Dependabot, plantilla de PR |
| `tests/k6/` | Pruebas de carga y regresión de escenarios de calidad |
| `observabilidad/` | Prometheus + Grafana locales |
| `dev/` | Soporte del entorno local (init de Mongo y Postgres) |
| `scripts/` | Validación de contratos y de servicios, selector de despliegue, verificación de migraciones |
| `docs/` | ADR, convenciones, resumen de arquitectura y plan del Sprint 1 |
| `.claude/` | Agentes y permisos de Claude Code para este repo |

## Arranque rápido

```bash
# 1. Solo dependencias (Postgres, Redis, MongoDB con replica set)
docker compose up -d

# 2. Servicios y stubs: migra los esquemas, arranca y espera a que todo esté sano (igual que el CI y staging)
docker compose --profile servicios up -d --build --wait
curl localhost:8000/health

# 3. Pruebas de un servicio
cd services/risk
pip install -r requirements.txt -r ../../requirements-dev.txt
python -m pytest

# 4. Contratos y consistencia de servicios
python scripts/validar_contratos.py
python scripts/validar_servicios.py

# 5. Migraciones de un servicio contra su base real (lo mismo que corre el CI)
bash scripts/verificar-migraciones.sh auth      # auth | payments | risk
```

Si 5432 o 6379 ya están ocupados en tu máquina: `POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose up -d`.

## De la base de datos al despliegue

Un solo camino, el mismo en local, CI y staging ([ADR-07](docs/adr/ADR-07-cicd-y-migraciones.md)):

```
PR ─► CI ─► merge a main ─► construir (imagen = SHA) ─► migrar (job) ─► revisión sin tráfico ─► humo ─► promover
```

- **Migraciones:** `auth` y `payments` con Alembic (`services/<x>/migrations/`), `risk` con un runner propio para MongoDB.
  Cada servicio migra solo su almacén. Compatibles hacia atrás: expandir primero, contraer en un despliegue posterior.
- **Agregar un servicio:** una entrada en [`.github/servicios.json`](.github/servicios.json) + su carpeta, filtro en `ci.yml`
  y Dependabot; `python scripts/validar_servicios.py` dice qué falta.
- **Nueva migración de Postgres:** `cd services/auth && alembic revision -m "texto" --rev-id 0002` (con `DATABASE_URL`),
  escribir `upgrade`/`downgrade` en SQL y correr `bash scripts/verificar-migraciones.sh auth`.
- **Rollback:** Actions > *Rollback staging* (devuelve el tráfico a la revisión anterior; no toca la base).

## Documentación

- [`CLAUDE.md`](CLAUDE.md) — contexto de trabajo, decisiones cerradas y reglas (léelo antes de tocar código).
- [`docs/arquitectura-backend.md`](docs/arquitectura-backend.md) — servicios, almacenes y flujo del caso insignia.
- [`docs/sprint-1.md`](docs/sprint-1.md) — alcance, habilitadoras, escenarios y decisiones abiertas del Sprint 1.
- [`docs/convenciones.md`](docs/convenciones.md) — ramas, commits, PR y definición de hecho.
- [`docs/adr/ADR-07-cicd-y-migraciones.md`](docs/adr/ADR-07-cicd-y-migraciones.md) — CI/CD y migraciones: principios y límites de lo probado.
- [`infra/README.md`](infra/README.md) — infraestructura y control de costos.

## Configuración pendiente en GitHub (una vez creado el primer push)

| Dónde | Qué | Para qué |
|---|---|---|
| Settings → Secrets → Actions | `SONAR_TOKEN` y `SONAR_HOST_URL` | Activa el análisis de SonarQube del CI |
| Settings → Branches | Proteger `main`: PR + 1 revisión + checks requeridos **`CI OK`** y **`Título del PR`** (solo esos dos) | ADR-06, ADR-07 y `docs/convenciones.md` |
| Settings → Environments | Crear el entorno `staging` (revisores y rama `main` si se quiere) | Lo exigen los jobs de despliegue y la identidad de GCP |
| `.github/CODEOWNERS` | Reemplazar los roles por usuarios reales | Revisores automáticos |
| Settings → Variables | `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`, `GCP_PROTECCION_GASTO=activa` | Encienden el despliegue (apagado hasta entonces); ver `infra/README.md` |
