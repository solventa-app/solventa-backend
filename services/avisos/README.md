# avisos

Avisos en vivo por SSE (HU-W03, W03-37, F-B7): la pantalla se entera sola de que una propuesta se emitió, sin recargar.

## Responsabilidad

Reenvía a cada pestaña abierta lo que POLICY publica en Redis, sin datos personales. **No tiene base de datos ni lógica de
negocio**: solo valida el ticket, revisa el `Origin` y empuja mensajes.

## Flujo

1. El front pide al BFF `ticketAvisos` (con sesión). El BFF guarda en Redis `avisos:ticket:<token>` =
   `{"rol", "usuarioId", "titularId"}` durante 60 s y devuelve el ticket y la URL de este servicio.
2. El front abre `new EventSource("<url>?ticket=<ticket>")`. `EventSource` no manda cabeceras: por eso el ticket va en la
   URL y nunca el JWT.
3. `GET /avisos/stream?ticket=…`:
   - `Origin` ausente o fuera de `ORIGENES_PERMITIDOS` → **403** (sin gastar el ticket).
   - ticket inexistente, vencido o ya usado → **401** (`GETDEL`: sirve una sola vez, dos pestañas no comparten uno).
   - Redis caído → **503** con `Retry-After: 5`.
   - si no, `200 text/event-stream` suscrito a los canales del rol.
4. Cada evento llega como `event: <tipo>` y `data: <json>`; cada 25 s sin mensajes va un comentario `: latido` para que los
   proxies no cierren la conexión. Si Redis falla a mitad, el stream **termina**; el front pide un ticket nuevo y reconecta
   (el viejo ya se gastó). El front debe consultar el estado real al abrirse y al reconectar: los avisos no se guardan.

## Canales (Redis pub/sub)

| Rol del ticket | Canales | Quién publica |
|---|---|---|
| ASESOR | `avisos:asesor:<usuario>` | POLICY (propuestas que aceptó) |
| CLIENTE (con titular verificado) | `avisos:titular:<titular>` | POLICY |
| BACKOFFICE | `avisos:todos` y `avisos:cobros` | POLICY (`todos`); PAYMENTS (`cobros`, W05-A6, aún no publica) |

Un CLIENTE sin identidad verificada no se suscribe a nada (recibe solo latidos). Eventos:
`propuesta` → `{"propuestaId", "estado", "estadoVisible", "polizaId", "numero"}`; `cobro` → `{"cobroId", "polizaId",
"estado"}`. `avisos:cobros` queda reservado para W05-01 (W05-A6): quien publique allí debe incluir `"tipo": "cobro"` (si
falta, el canal lo da).

## Variables de entorno

| Variable | Default local | Nota |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/3` | La **misma base** que usa el BFF para los tickets y POLICY para publicar. En Cloud Run, secreto `redis-url-w03` |
| `ORIGENES_PERMITIDOS` | `http://localhost:5173,http://localhost:8080` | Orígenes del front, separados por coma |
| `LATIDO_SEGUNDOS` | `25` | Cada cuánto va el latido |

## Operación

- Puerto local: `8011`. Salud: `GET /health`. Métricas: `GET /metrics`.
- **Ingreso público, fuera del API Gateway** (un stream largo no cabe en el gateway). No es invocado por otro servicio: en
  `.github/servicios.json` lleva `ingreso_directo`. Pendiente en F-B8 (infra): el ingreso público en Cloud Run, el CORS del
  front en `ORIGENES_PERMITIDOS` y subir el timeout de la solicitud.
- Sin registro de accesos (`--no-access-log`): el ticket va en la URL y no debe quedar en los logs.
- Cloud Run limita la CPU entre peticiones, pero un stream abierto cuenta como petición activa: mientras haya pestañas
  conectadas la instancia sigue despierta (y cobra). Cada conexión mantiene una suscripción en Redis.

## Pruebas

`python -m pytest` desde esta carpeta. El stream se prueba contra un uvicorn real en un hilo y un Redis en memoria
(`tests/fakes.py`): `httpx.ASGITransport` no sirve porque espera a que la respuesta termine.
