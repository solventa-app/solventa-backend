# stub-pasarela

Stub de la pasarela de pagos tokenizada.

## Responsabilidad

Stub **por contrato** (decisión D-05) de un proveedor externo, en el estilo del `stub-kyc` del Experimento 1: imita el contrato real y expone un endpoint de control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`, `rechaza`). Es andamiaje de prueba: sin hexagonal y sin base de datos; **recuerda los cobros en memoria** (se reinicia limpio con el contenedor) para comportarse como una pasarela real. Nunca recibe ni almacena datos de tarjeta: solo un `token_medio_pago` opaco, ya tokenizado por el proveedor PCI-DSS (CA-W05-03, regla de arquitectura #6).

La suite de pruebas de contrato debe poder correr contra este stub y contra el sandbox real sin cambios (EC031).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`, sin importar el modo.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/cobros` `{referencia_comercio, poliza_id, monto, moneda, token_medio_pago}` → `{"estado": "cobrado" | "rechazado", "referencia", "motivo", "detalle"}`.
  **Idempotente por `referencia_comercio`**: repetir la misma referencia devuelve el resultado
  original sin cobrar de nuevo, como las pasarelas reales. "Comercio" es el punto de vista de la pasarela (el comercio
  es Solventa): en su propio dominio, Solventa la llama `id_intento_cobro`, y el adaptador del ACL Worker traduce
  entre los dos nombres.
- `GET /v1/cobros/{referencia_comercio}` → el cobro registrado con esa clave, o `404`. Es la consulta que usa la
  **conciliación** (KAN-28) para saber si un cobro ya se hizo antes de reintentarlo. Obedece el modo simulado.
- `GET /v1/estado` → **sí obedece el modo simulado** (a diferencia de `/health`): es el endpoint que usa la sonda de recuperación del circuito del ACL Worker, para no tener que ejecutar un cobro real solo para probar si el proveedor ya respondió.
  - Override de un solo request (en `/v1/cobros` y en `/v1/estado`): query param `?modo=...` o header `X-Modo-Simulado`.

- `POST /v1/tokens` `{numero, vencimiento_mes, vencimiento_anio, cvv, titular?}` → `{token, marca, ultimos4, vencimiento_mes, vencimiento_anio, estado}`
  (**KAN-208**). Tokeniza una tarjeta **de prueba** (Luhn, CVV de 3 o 4 dígitos, vencimiento vigente; `422` con
  `numero_invalido`, `cvv_invalido`, `vencimiento_invalido` o `tarjeta_vencida`). **Es idempotente**: la misma tarjeta da
  siempre el mismo token (derivado de la tarjeta, solo en el simulador) y conserva su estado. El número y el CVV **no se
  guardan, no se devuelven y no se loguean**, y los errores de validación tampoco los repiten.
- `GET /v1/tokens/{token}` → marca, últimos 4, vencimiento y estado, o `404`.
- Un `token_medio_pago` que el simulador **no conoce** se sigue aceptando al cobrar (compatibilidad con pruebas y datos
  anteriores).

## Controles de la demo

Todos bajo `/control`, solo para simular. Se pierden al reiniciar el contenedor (la memoria vive en el proceso).

| Endpoint | Para qué |
|---|---|
| `GET /control/modo` · `POST /control/modo {"modo"}` | Modo global de falla (ver la tabla de abajo) |
| `GET /control/tokens` | Las tarjetas tokenizadas y su estado (nunca el número) |
| `POST /control/tokens/{token}/estado {"estado"}` | Cambia a mano el estado de **una tarjeta**: `activa`, `fondos_insuficientes`, `bloqueada` o `vencida`. Un cobro con una tarjeta que no está `activa` se **rechaza** (200, `estado: rechazado`) con el motivo `fondos_insuficientes`, `tarjeta_bloqueada` o `tarjeta_vencida`, sin importar el modo global. `404` si el token no existe |
| `GET /control/latencia` · `POST /control/latencia {"minimo_ms","maximo_ms"}` · `DELETE /control/latencia` | Fija cuánto tarda en responder la pasarela (0 a 120 000 ms, aleatorio entre el mínimo y el máximo) en `/v1/cobros`, `/v1/tokens` y `/v1/estado`; `DELETE` vuelve a la latencia propia de cada modo |

Ejemplos (con el stub en `localhost:8103`):

```bash
# Tokenizar una tarjeta de prueba y tomar el token
TOKEN=$(curl -s -X POST localhost:8103/v1/tokens -H 'Content-Type: application/json' \
  -d '{"numero":"4242 4242 4242 4242","vencimiento_mes":12,"vencimiento_anio":2030,"cvv":"123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Dejarla sin fondos: los cobros con ese token se rechazan; volver a activa los permite de nuevo
curl -s -X POST localhost:8103/control/tokens/$TOKEN/estado -H 'Content-Type: application/json' -d '{"estado":"fondos_insuficientes"}'
curl -s -X POST localhost:8103/control/tokens/$TOKEN/estado -H 'Content-Type: application/json' -d '{"estado":"activa"}'

# Pasarela lenta: 900 ms supera los 700 ms del ACL Worker (equivale a una falla por timeout; el cobro igual queda hecho)
curl -s -X POST localhost:8103/control/latencia -H 'Content-Type: application/json' -d '{"minimo_ms":900,"maximo_ms":900}'
curl -s -X DELETE localhost:8103/control/latencia
```

**Prioridad al cobrar:** primero las fallas del modo (`error` y `caido`), luego el estado de la tarjeta, luego `rechaza`;
la latencia fijada reemplaza la espera de todos los modos menos `caido`.

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
