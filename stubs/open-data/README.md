# stub-open-data

Stub de Open Data (3 fuentes abiertas).

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`healthy`, `lento`, `error-429`, `down`). Es andamiaje de prueba: sin hexagonal, sin persistencia.

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Operación

- Puerto local: `8102`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-open-data .`
