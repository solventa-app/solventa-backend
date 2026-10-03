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
| `infra/` | Terraform (GCP): APIs, Artifact Registry, KMS opcional |
| `tests/k6/` | Pruebas de carga y regresión de escenarios de calidad |
| `observabilidad/` | Prometheus + Grafana locales |
| `dev/` | Soporte del entorno local (init de Mongo y Postgres) |
| `scripts/` | Utilidades (validación de contratos) |
| `docs/` | ADR, convenciones, resumen de arquitectura y plan del Sprint 1 |
| `.claude/` | Agentes y permisos de Claude Code para este repo |

## Arranque rápido

```bash
# 1. Solo dependencias (Postgres, Redis, MongoDB con replica set)
docker compose up -d

# 2. Servicios y stubs
docker compose --profile servicios up -d --build
curl localhost:8000/health

# 3. Pruebas de un servicio
cd services/risk
pip install -r requirements.txt -r ../../requirements-dev.txt
python -m pytest

# 4. Contratos
python scripts/validar_contratos.py
```

## Documentación

- [`CLAUDE.md`](CLAUDE.md) — contexto de trabajo, decisiones cerradas y reglas (léelo antes de tocar código).
- [`docs/arquitectura-backend.md`](docs/arquitectura-backend.md) — servicios, almacenes y flujo del caso insignia.
- [`docs/sprint-1.md`](docs/sprint-1.md) — alcance, habilitadoras, escenarios y decisiones abiertas del Sprint 1.
- [`docs/convenciones.md`](docs/convenciones.md) — ramas, commits, PR y definición de hecho.
- [`infra/README.md`](infra/README.md) — infraestructura y control de costos.

## Configuración pendiente en GitHub (una vez creado el primer push)

| Dónde | Qué | Para qué |
|---|---|---|
| Settings → Secrets → Actions | `SONAR_TOKEN` y `SONAR_HOST_URL` | Activa el análisis de SonarQube del CI |
| Settings → Branches | Proteger `main`: PR + 1 revisión + CI en verde | ADR-06 y `docs/convenciones.md` |
| `.github/CODEOWNERS` | Reemplazar los roles por usuarios reales | Revisores automáticos |
| Más adelante | Workload Identity Federation hacia GCP | Deploy a staging sin llaves de service account |
