# stub-pasarela

Stub de la pasarela de pagos tokenizada.

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`, `rechaza`). Es andamiaje de prueba: sin hexagonal y sin base de datos; **recuerda los cobros en memoria** (se reinicia limpio con el contenedor) para comportarse como una pasarela real. Nunca recibe ni almacena datos de tarjeta: solo un `token_medio_pago` opaco, ya tokenizado por el proveedor PCI-DSS (CA-W05-03, regla de arquitectura #6).

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`, sin importar el modo.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/cobros` `{clave_idempotencia, poliza_id, monto, moneda, token_medio_pago}` → `{"estado": "cobrado" | "rechazado", "referencia", "motivo", "detalle"}`.
  **Idempotente por `clave_idempotencia`** (la referencia del comercio): repetir la misma clave devuelve el resultado
  original sin cobrar de nuevo, como las pasarelas reales.
- `GET /v1/cobros/{referencia_comercio}` → el cobro registrado con esa clave, o `404`. Es la consulta que usa la
  **conciliación** (KAN-28) para saber si un cobro ya se hizo antes de reintentarlo. Obedece el modo simulado.
- `GET /v1/estado` → **sí obedece el modo simulado** (a diferencia de `/health`): es el endpoint que usa la sonda de recuperación del circuito del ACL Worker, para no tener que ejecutar un cobro real solo para probar si el proveedor ya respondió.
  - Override de un solo request (en `/v1/cobros` y en `/v1/estado`): query param `?modo=...` o header `X-Modo-Simulado`.

## Modos de falla

| Modo | Efecto | ¿Registra el cobro? |
|---|---|---|
| `sano` | Responde en 20–120 ms con `cobrado` | Sí |
| `lento` | Responde en 750–1100 ms (supera los 700 ms del ACL Worker) | **Sí, antes de la espera**: si el ACL corta, el cobro igual quedó hecho |
| `error` | 503 inmediato | No |
| `caido` | Socket colgado 60 s | Solo si la petición llega a terminar |
| `rechaza` | **200** con `estado: rechazado`, `motivo: fondos_insuficientes` (un rechazo no es una falla: no abre el circuito) | Sí (el rechazo también se recuerda) |

## Operación

- Puerto local: `8103`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-pasarela .`
