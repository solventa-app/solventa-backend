# payments

PAYMENTS: cobros con pasarela tokenizada.

## Responsabilidad

PAYMENTS procesa el cobro de primas e indemnizaciones con **pasarela tokenizada** (HU-W05). Ningún dato de tarjeta toca servidores propios (EC031): `token_medio_pago` es el token opaco del proveedor PCI-DSS, no un dato de tarjeta real.

- `POST /cobros`: recibe `clave_idempotencia`, `poliza_id`, `monto` (texto decimal, nunca float), `moneda`, `token_medio_pago` — el mismo shape que ya acepta `acl-worker` en `POST /pagos/cobrar` (`OrdenCobro`); no se inventa un contrato nuevo entre PAYMENTS y el ACL Worker.
- **Idempotencia real a nivel de base de datos (CA-W05-04):** un mismo intento recibido dos veces (incluso en paralelo) se ejecuta una sola vez. `clave_idempotencia` es `UNIQUE` en PostgreSQL; el `INSERT ... ON CONFLICT (clave_idempotencia) DO NOTHING RETURNING ...` es correcto bajo concurrencia real (ver "Diseño" abajo), no solo un `SELECT` seguido de un `INSERT`.
- Estados de cobro: `cobrando` → `cobrado` / `rechazado` / `pendiente` (PostgreSQL, base `payments`, única que escribe esta tabla — regla de escritor único).
- `GET /polizas/{poliza_id}/balance`: suma los `monto` de los cobros en estado `cobrado` de esa póliza. No cubre balances de indemnización (Sprint 3, HU-W04/HU-M04).
- **Reintento del cobro pendiente (T-W05-4, CA-W05-05):** un bucle local reintenta los cobros `pendiente` reutilizando la MISMA `clave_idempotencia`, acotado a un número máximo de intentos. Ver huecos abajo (no es Cloud Tasks real).
- La pasarela se alcanza **solo** a través del `acl-worker` (Circuit Breaker + Retry, 700 ms). PAYMENTS nunca llama a la pasarela directamente ni guarda estado del circuito — eso es responsabilidad exclusiva del ACL Worker.
- Emite `cobro.estado-cambiado` por un bus de eventos **placeholder** (ver huecos) en cada cambio de estado real: al crear (`cobrando`) y al actualizar (`cobrado`/`rechazado`/`pendiente`).

## Diseño (decisiones no escritas en el plan)

### Esquema de la tabla `cobros`

Un único script SQL (`app/sql/001_cobros.sql`, sin framework de migraciones — regla de "capas simples", nada de Alembic/SQLAlchemy para un único CRUD), ejecutado al arrancar la app (`RepositorioCobrosPostgres.preparar_esquema`, `CREATE TABLE IF NOT EXISTS`):

```sql
cobros (
    id TEXT PRIMARY KEY,                 -- UUID generado en Python, no en la BD
    clave_idempotencia TEXT UNIQUE NOT NULL,
    poliza_id TEXT NOT NULL,
    monto TEXT NOT NULL,                 -- decimal como texto, nunca float
    moneda TEXT NOT NULL,
    token_medio_pago TEXT NOT NULL,      -- token PCI-DSS, no cifrado (ver huecos)
    estado TEXT NOT NULL,                -- cobrando | cobrado | rechazado | pendiente
    referencia TEXT,
    motivo TEXT,
    intentos INTEGER NOT NULL DEFAULT 1, -- usado por el reintento para acotarse
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

### Idempotencia correcta bajo concurrencia real

`RepositorioCobrosPostgres.crear_o_obtener` (`app/application/repositorio_cobros.py`):

1. `INSERT ... ON CONFLICT (clave_idempotencia) DO NOTHING RETURNING ...` en una conexión con `autocommit=True` (una sentencia = una transacción, vía `psycopg_pool.ConnectionPool`).
2. Si `RETURNING` devuelve una fila → es la primera vez con esa clave → se llama al ACL Worker.
3. Si no devuelve nada → ya existía → **no se llama al ACL Worker otra vez**, se hace un `SELECT` de la fila existente y se devuelve tal cual (sea cual sea su estado actual: `cobrando`, `cobrado`, `rechazado` o `pendiente`).

Por qué es correcto bajo concurrencia (dos requests simultáneas con la misma clave, no solo secuenciales): Postgres serializa el conflicto con el lock del índice único — la segunda conexión en llegar se bloquea hasta que la primera transacción termine (commit o rollback) y solo entonces evalúa el `ON CONFLICT`. Si el `INSERT` de la primera committeó, la segunda ve `RETURNING` vacío y el `SELECT` que sigue YA ve esa fila committeada (sin ventana de carrera). Esto se prueba con PostgreSQL real en la verificación en vivo (abajo) y, a nivel de caso de uso, con un fake thread-safe (`tests/fakes.py::RepositorioCobrosFalso`, con `threading.Lock`) en `tests/test_servicio_cobros.py::test_llamadas_concurrentes_con_la_misma_clave_cobran_una_sola_vez` (20 hilos concurrentes, 1 sola llamada al ACL, 1 solo cobro persistido).

### Reintento de pendientes — placeholder local de Cloud Tasks (T-W05-4)

No hay Cloud Tasks real disponible (sin proyecto GCP, ver `CLAUDE.md`). `app/application/reintento.py` implementa:

- `ejecutar_una_pasada(repositorio, cliente_acl, bus, max_intentos)`: la lógica de reintento en sí — pequeña, aislada, sin saber qué la dispara. Busca cobros `pendiente` con `intentos < max_intentos`, llama de nuevo a `POST {ACL_URL}/pagos/cobrar` con la **misma** `clave_idempotencia` (CA-W05-05) y actualiza el estado.
- `ReintentoCobrosPendientes`: HOY lo que dispara `ejecutar_una_pasada` — un bucle `asyncio` arrancado desde el `lifespan` de FastAPI (mismo patrón que `SondaRecuperacion` de `acl-worker/app/application/sonda.py`), cada `REINTENTO_COBRO_INTERVALO_SEGUNDOS` (default `5`).
- **Acotado:** `listar_pendientes` solo devuelve cobros con `intentos < REINTENTO_COBRO_MAX_INTENTOS` (default `10`). Agotados los intentos, el cobro deja de aparecer y el bucle ya no lo toca — queda `pendiente` para seguimiento manual, consultable vía la propia tabla o `GET /polizas/{poliza_id}/balance` (no se construye una DLQ formal aparte).
- **Cuando `infra-engineer` tenga un proyecto GCP real:** lo que cambia es QUÉ dispara el reintento (una tarea de Cloud Tasks llamando a un endpoint interno que invoque `ejecutar_una_pasada`, en vez de este bucle `asyncio`) — la lógica de PAYMENTS no se toca. Deliberadamente **no** se construyó una abstracción de puertos y adaptadores para esto (regla de arquitectura #3: hexagonal es solo para `acl-worker`); aislar la decisión en una función pequeña (`ejecutar_una_pasada`) es suficiente.

### Bus de eventos

Mismo patrón placeholder que `services/risk/app/application/bus_eventos.py` (sin Pub/Sub real, sin proyecto GCP): `BusEventosLog` solo loguea el evento ya serializado. El evento (`cobro.estado-cambiado`) valida contra `contracts/events/cobro.estado-cambiado.schema.json` (ver `tests/test_bus_eventos.py`).

A diferencia de RISK, aquí **no se hashea `cobroId` ni `claveIdempotencia`** en el log: son identificadores internos/de correlación, no datos personales. Sí se hashea `polizaId` (identifica, indirectamente, a la persona asegurada) con el mismo patrón `id_opaco` de `acl-worker`/`risk` (duplicado deliberadamente, sin código compartido entre servicios) — más estricto que el logging actual de `acl-worker/app/application/servicio_pagos.py`, que loguea `poliza_id` en claro; no se tocó ese servicio, solo se documenta la diferencia.

### Dinero y tarjeta

- `monto` es `TEXT` en la tabla, en el `OrdenCobro`/`ResultadoCobro` y en el payload del evento — nunca `float`, ni en la app ni en SQL (el `SUM` del balance castea a `NUMERIC` solo para la agregación y vuelve a `TEXT`).
- `token_medio_pago` se persiste tal cual: es el token opaco del proveedor PCI-DSS (ya tokenizado antes de llegar aquí), no un dato de tarjeta real. No se cifra como campo adicional — ver huecos.

## Pendiente, fuera de alcance de esta tarea (huecos documentados)

- **Cloud Tasks real:** el reintento (T-W05-4) es un bucle `asyncio` local, no una tarea programada de GCP. Ver "Diseño" arriba para qué cambia cuando exista un proyecto GCP.
- **Cifrado de campo de `token_medio_pago`:** no aplica (regla de arquitectura #6: cero datos de tarjeta, solo token PCI-DSS) — no es el mismo caso que los datos personales de RISK, que sigue sin cifrado por falta de Cloud KMS (ver `services/risk/README.md`).
- **Bus de eventos real:** `BusEventosLog` solo loguea; cuando `infra-engineer` levante Pub/Sub real, se sustituye sin tocar `ServicioCobros` ni `app.application.reintento`.
- **Balance de órdenes de indemnización:** fuera de alcance (Sprint 3, HU-W04/HU-M04). `GET /polizas/{poliza_id}/balance` solo suma cobros `cobrado`.
- **Reintentos de red hacia `acl-worker` desde el propio `ClienteAcl`:** si la llamada HTTP de PAYMENTS al ACL Worker falla (no la pasarela, sino el ACL Worker mismo inalcanzable), se trata igual que `pasarela_no_disponible` (`motivo=acl_no_disponible`, estado `pendiente`) — no hay un retry inmediato dentro de la misma petición, se apoya en el mismo bucle de reintento de pendientes.

## Operación

- Puerto local: `8004`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- `POST /cobros` `{clave_idempotencia, poliza_id, monto, moneda, token_medio_pago}` → `{id, clave_idempotencia, poliza_id, monto, moneda, estado, referencia, motivo, intentos}`.
- `GET /polizas/{poliza_id}/balance` → `{poliza_id, balance}`.
- Variables de entorno: `DATABASE_URL` (ya fijada en `docker-compose.yml`), `ACL_URL` (ídem), `PAYMENTS_ACL_TIMEOUT_SEGUNDOS` (default `3.0`, timeout de PAYMENTS llamando al ACL Worker — defensa en profundidad, el presupuesto de 700 ms proveedor-abajo ya lo aplica el ACL Worker), `REINTENTO_COBRO_INTERVALO_SEGUNDOS` (default `5`), `REINTENTO_COBRO_MAX_INTENTOS` (default `10`).
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta). Usan dobles de prueba (`tests/fakes.py`), no PostgreSQL ni ACL Worker reales — ver "Verificación en vivo" abajo para la prueba con PostgreSQL y el ACL Worker reales.
- Imagen: `docker build -t solventa-payments .`

### Verificación en vivo (idempotencia y reintento reales, T-W05-3/4)

```bash
docker compose --profile servicios up -d --build postgres redis acl-worker stub-pasarela payments
```

**(a) Cobro normal con la pasarela sana termina `cobrado`:**

```bash
curl -s -X POST localhost:8004/cobros -H 'content-type: application/json' -d '{
  "clave_idempotencia": "demo-001", "poliza_id": "poliza-demo-1",
  "monto": "150000.00", "moneda": "COP", "token_medio_pago": "tok_demo_1"
}'
# -> {"estado":"cobrado", ...}
```

**(b) La MISMA `clave_idempotencia` enviada dos veces no duplica el cobro ni llama dos veces a la pasarela** (verificado con las métricas del stub, no solo con la respuesta — ver evidencia abajo con `curl -s localhost:8103/metrics | grep 'v1_cobros.*POST'`).

**(c) Pasarela caída → `pendiente` sin 5xx → vuelve sana → el bucle de reintento la resuelve solo:**

```bash
curl -s -X POST localhost:8103/control/modo -d '{"modo":"caido"}' -H 'content-type: application/json'
curl -s -X POST localhost:8004/cobros -d '{"clave_idempotencia":"demo-002", ...}' # -> estado: pendiente
curl -s -X POST localhost:8103/control/modo -d '{"modo":"sano"}' -H 'content-type: application/json'
# esperar <= REINTENTO_COBRO_INTERVALO_SEGUNDOS (default 5s) x unos pocos ciclos
curl -s localhost:8004/polizas/poliza-demo-2/balance # -> ya refleja el cobro resuelto
```

```bash
docker compose --profile servicios down
```

Ver el reporte de la tarea (o el historial de este repo) para la evidencia con números reales de esta verificación.
