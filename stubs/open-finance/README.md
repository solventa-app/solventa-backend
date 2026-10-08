# stub-open-finance

Stub de Open Finance (2 fuentes financieras, circular 004 de 2024 de la SFC).

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`). Es andamiaje de prueba: sin hexagonal, sin persistencia. A diferencia de `stub-kyc` (Truora/KYC, asíncrono crear→pollear), este contrato es síncrono: una consulta, una respuesta — ese ciclo asíncrono era específico de KYC y queda fuera de alcance del Sprint 1.

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`, sin importar el modo (liveness del contenedor, no la sonda de recuperación del circuito).
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global (persiste entre llamadas).
- `POST /v1/fuentes/{fuente}/consulta` `{"cliente_id": "..."}` → `{"fuente", "datos", "capturado_en"}`. `fuente` ∈ `cuentas-bancarias`, `historial-crediticio`; 404 si es otra.
  - Override de un solo request, sin persistir: query param `?modo=...` o header `X-Modo-Simulado`.

## Modos de falla

| Modo | Comportamiento |
|---|---|
| `sano` | Responde 200 en 20–120 ms. |
| `lento` | Responde 200, pero en 750–1100 ms (supera el timeout duro de 700 ms del ACL Worker). |
| `error` | Responde 503 de inmediato. |
| `caido` | No responde (socket colgado 60 s): el cliente debe cancelar por su propio timeout. |

## Operación

- Puerto local: `8101`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-open-finance .`
