# stub-pasarela

Stub de la pasarela de pagos tokenizada.

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`). Es andamiaje de prueba: sin hexagonal, sin persistencia. Nunca recibe ni almacena datos de tarjeta: solo un `token_medio_pago` opaco, ya tokenizado por el proveedor PCI-DSS (CA-W05-03, regla de arquitectura #6).

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`, sin importar el modo.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/cobros` `{clave_idempotencia, poliza_id, monto, moneda, token_medio_pago}` → `{"estado": "cobrado", "referencia", "detalle"}`.
- `GET /v1/estado` → **sí obedece el modo simulado** (a diferencia de `/health`): es el endpoint que usa la sonda de recuperación del circuito del ACL Worker, para no tener que ejecutar un cobro real solo para probar si el proveedor ya respondió.
  - Override de un solo request (en `/v1/cobros` y en `/v1/estado`): query param `?modo=...` o header `X-Modo-Simulado`.

## Modos de falla

`sano` (20–120 ms), `lento` (750–1100 ms, supera los 700 ms del ACL Worker), `error` (503 inmediato), `caido` (socket colgado 60 s).

## Operación

- Puerto local: `8103`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-pasarela .`
