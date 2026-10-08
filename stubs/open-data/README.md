# stub-open-data

Stub de Open Data (3 fuentes abiertas).

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`). Es andamiaje de prueba: sin hexagonal, sin persistencia. Mismo contrato síncrono (una consulta, una respuesta) que `stub-open-finance` — ver ese README para el detalle de los modos.

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`, sin importar el modo.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/fuentes/{fuente}/consulta` `{"cliente_id": "..."}` → `{"fuente", "datos", "capturado_en"}`. `fuente` ∈ `afiliacion-pila`, `camara-comercio`, `antecedentes-judiciales`; 404 si es otra.
  - Override de un solo request: query param `?modo=...` o header `X-Modo-Simulado`.

## Modos de falla

`sano` (20–120 ms), `lento` (750–1100 ms, supera los 700 ms del ACL Worker), `error` (503 inmediato), `caido` (socket colgado 60 s).

## Operación

- Puerto local: `8102`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-open-data .`
