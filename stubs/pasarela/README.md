# stub-pasarela

Stub de la pasarela de pagos tokenizada.

> **El stub no es parte de Solventa.** Simula a una pasarela como PayU o Stripe para mostrar cómo sería integrarla.
> Tiene **su propia página de pago**, en su origen (`:8103`): ahí captura, tokeniza y cobra la tarjeta. Solventa nunca
> ve un dato de tarjeta; recibe un token con la marca y el tipo, y confirma el pago de servidor a servidor (W03-28,
> regla 6, CA-W03-09).

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
- **Página de pago alojada (HU-W03, W03-28):**
  - `POST /v1/sesiones-pago` `{referencia_comercio, monto, moneda, descripcion, url_retorno, idioma}` → `201` con
    `{sesion_id, estado: ABIERTA, url_pago, vence_en, ...}`. **Idempotente por `referencia_comercio`** y **vence a los
    10 min** (`SESION_VIGENCIA_SEGUNDOS`, 600). `url_retorno` debe ser de un origen de `URLS_RETORNO_PERMITIDAS`; si no,
    `422 url_retorno_no_permitida` (la página no sirve de redirección abierta). Una sesión vencida no se reabre: el
    comercio pide otra con otra referencia.
  - `GET /v1/sesiones-pago/{id}` → `ABIERTA`, `PROCESANDO`, `COBRADA`, `RECHAZADA` (con `motivo`), `FALLIDA` o
    `VENCIDA`. Con `COBRADA` trae `medio` (`token` `tok_…`, `marca`, `tipo` `CREDITO`|`DEBITO`) y la `referencia` del
    cobro. **Nunca el número ni los últimos dígitos.** El cobro queda además en `GET /v1/cobros/{referencia_comercio}`
    para la conciliación.
  - `GET /checkout/{id}?idioma=es|en` → la página (HTML y JS propios en `app/estaticos/`, sin CDN, con CSP estricta):
    resumen, crédito o débito, formulario de la tarjeta, cuenta regresiva, «Pagando…» y el resultado con **«Regresar
    al comercio»**, que lleva a `url_retorno?sesion=<id>`. El estado del pago no viaja en esa URL: el comercio lo
    pregunta.
  - `POST /checkout/{id}/pagar` → lo envía la página. Valida Luhn, vencimiento futuro y CVV de 3 o 4 dígitos (`422`
    con el `campo` a corregir, sin repetir la tarjeta), tokeniza y cobra. Una sesión se paga una sola vez (`409`).
  - `/alterna/v2/checkout-sessions` (`POST` y `GET /{id}`): lo mismo con los nombres del segundo proveedor (EC031).
- `GET /v1/estado` → **sí obedece el modo simulado** (a diferencia de `/health`): es el endpoint que usa la sonda de recuperación del circuito del ACL Worker, para no tener que ejecutar un cobro real solo para probar si el proveedor ya respondió.
  - Override de un solo request (en `/v1/cobros` y en `/v1/estado`): query param `?modo=...` o header `X-Modo-Simulado`.

## Tarjetas de prueba

Al estilo de Stripe; la página las muestra. Cualquier otra tarjeta válida por Luhn se aprueba. Vencimiento futuro y
cualquier CVV de 3 o 4 dígitos.

| Número | Marca | Resultado |
|---|---|---|
| `4242 4242 4242 4242` | Visa | Aprobado |
| `5555 5555 5555 4444` | Mastercard | Aprobado |
| `4000 0000 0000 0002` | Visa | Rechazado: `fondos_insuficientes` |
| `4000 0000 0000 0069` | Visa | Rechazado: `tarjeta_vencida` |
| `4000 0000 0000 0341` | Visa | Aprobado en la página; sus cobros con token se rechazan con `fondos_insuficientes` (W05-A7) |
| `4000 0000 0000 0119` | Visa | Falla: `error_procesamiento` (sesión `FALLIDA`) |

El tipo (crédito o débito) es el que elige el titular en la página. **Ningún dato de tarjeta se guarda ni se escribe en
un log**: en memoria quedan solo el token, la marca, el tipo y los últimos 4 dígitos (estos, solo para el stub).

## Modos de falla

| Modo | Efecto | ¿Registra el cobro? |
|---|---|---|
| `sano` | Responde en 20–120 ms con `cobrado` | Sí |
| `lento` | Responde en 750–1100 ms (supera los 700 ms del ACL Worker) | **Sí, antes de la espera**: si el ACL corta, el cobro igual quedó hecho |
| `error` | 503 inmediato | No |
| `caido` | Socket colgado 60 s | Solo si la petición llega a terminar |
| `rechaza` | **200** con `estado: rechazado`, `motivo: fondos_insuficientes` (un rechazo no es una falla: no abre el circuito) | Sí (el rechazo también se recuerda) |

Los modos se aplican también al crear y consultar una sesión y al pagar en la página: con `error`, la sesión queda
`FALLIDA` (`pasarela_no_disponible`); con `rechaza`, `RECHAZADA`; con `lento`, el cobro se hace antes de esperar.

## Operación

- Variables: `PASARELA_URL_PUBLICA` (`http://localhost:8103`; en staging, la URL pública del stub),
  `URLS_RETORNO_PERMITIDAS` (`http://localhost:5173,http://localhost:8080`) y `SESION_VIGENCIA_SEGUNDOS` (600). En
  Cloud Run con mínimo 0 instancias, un reciclaje pierde las sesiones: Solventa lo ve como inexistente y abre otra.
- Puerto local: `8103`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-stub-pasarela .`
