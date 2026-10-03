# bff

BFF GraphQL de Solventa (web y móvil).

## Responsabilidad

BFF único de GraphQL para web y móvil (ADR-01): un solo Cloud Run con esquema y resolutores distintos según el
discriminador de canal (`/graphql/web` y `/graphql/app`). **No tiene lógica de negocio**: orquesta AUTH, RISK,
RATING y PAYMENTS y traduce al contrato de `contracts/graphql/schema.graphql`.

- Historias: HU-W01 y HU-W05 (Sprint 1). La API REST B2B `/v1` llega con HU-W07 (Sprint 2).
- Regla: nunca devuelve un 5xx crudo al cliente; traduce la degradación a oferta preliminar o error con reintento (CA-W01-04/05).
- Pendiente de construir: Strawberry GraphQL (propuesta, ver `docs/adr/`) y los resolutores del Sprint 1.

## Operación

- Puerto local: `8000`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-bff .`
