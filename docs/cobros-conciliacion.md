# Cobros en el back-office: conciliar y reintentar (HU-W05, KAN-28)

Cómo el back-office ve los cobros de una póliza y reintenta los que no entraron, **sin cobrar dos veces**. Cubre
CA-W05-01 (tabla de cobros por póliza con su estado) y CA-W05-02 (reintentar con la misma clave de idempotencia), y la
meta del Sprint 1: *"el back-office cobra y **concilia** pagos"*.

Documentos relacionados: [README de PAYMENTS](../services/payments/README.md) · [README del ACL Worker](../services/acl-worker/README.md) ·
[mock de la pasarela](../stubs/pasarela/README.md) · [contrato GraphQL](../contracts/graphql/schema.graphql) ·
[autenticación](autenticacion-usuarios.md) · [pruebas Newman](../tests/newman/README.md).

## 1. El problema

Una demo en vivo (2026-10-16) dejó a la vista tres huecos en lo que ya existía:

1. **Doble cobro posible.** Si la pasarela tarda más de 700 ms, el ACL Worker corta y PAYMENTS registra el cobro
   `pendiente`, pero la pasarela **sí cobró**. El reintento volvía a cobrar y generaba otra referencia.
2. **Cobros atascados.** Con la pasarela caída, el bucle de reintento gastaba sus 10 intentos (uno cada 5 s) aunque
   el circuito estuviera abierto. Una caída de más de ~50 s dejaba el cobro `pendiente` para siempre.
3. **Nada que mostrar.** El back-office no podía ver los cobros ni reintentarlos, y el mock nunca rechazaba un cobro.

## 2. Decisiones

| Decisión | Resultado | Por qué |
|---|---|---|
| **Conciliar antes de cobrar** | Cada reintento pregunta primero a la pasarela si ya tiene el cobro | Cierra el doble cobro del hueco 1 |
| **Un solo camino** | El bucle automático y el botón del back-office usan la misma función (`services/payments/app/application/conciliacion.py`) | Los dos quedan protegidos igual |
| **Consulta por referencia del comercio** | Se busca el cobro por la `clave_idempotencia` que Solventa envía | Así consultan las pasarelas reales (Wompi `reference`, PayU `referenceCode`): cambiar de pasarela sigue siendo solo un adaptador (CA-W05-06) |
| **Intentos que no cuentan** | Si el circuito está abierto o la pasarela no responde a la consulta, no se gasta intento | Cierra el hueco 2 |
| **Qué se reintenta** | Solo `pendiente`. `cobrado` o `cobrando` se devuelven sin tocarlos; `rechazado` da error | Un doble clic no cobra dos veces; un rechazo no se arregla insistiendo |
| **Mock realista, sin base de datos** | La pasarela simulada recuerda cada cobro **en memoria** por clave, puede rechazar y se puede consultar | Imita a un proveedor real sin sobre-construir |
| **Permisos** | `cobros` y `reintentarCobro`: solo `BACKOFFICE`, **siempre con token** (aun con la bandera en `opcional`) | Son datos de pagos |

## 3. Flujo del reintento

```
Back-office ─► BFF  reintentarCobro(cobroId)         solo BACKOFFICE, con token
                │
                ▼
            PAYMENTS  POST /cobros/{id}/reintentar
                │  ¿estado?  cobrado/cobrando → devolver tal cual
                │            rechazado        → error "requiere otro medio de pago"
                │            pendiente        ↓
                ▼
            ACL Worker  GET /pagos/cobros/{clave}     conciliar (mismo Circuit Breaker, 700 ms)
                │
     ┌──────────┼─────────────────────┬───────────────────────────┐
 encontrado   no_encontrado        desconocido
     │          │                     │
     ▼          ▼                     ▼
 tomar el    ACL Worker POST          no cobrar; sigue pendiente
 resultado   /pagos/cobrar con la     (sin gastar intento)
 SIN cobrar  MISMA clave
 (misma
 referencia)
```

El bucle automático de PAYMENTS (cada 5 s, hasta 10 intentos) hace exactamente lo mismo con cada cobro `pendiente`.

## 4. Estados de un cobro

| Estado | Quién lo pone | Cuándo | ¿Se puede reintentar? |
|---|---|---|---|
| `cobrando` | PAYMENTS | Recién creado, mientras se llama a la pasarela | Se devuelve tal cual |
| `cobrado` | La pasarela | Cobró (lo informa al cobrar o se detecta al conciliar) | Se devuelve tal cual |
| `rechazado` | La pasarela | Respondió, pero rechazó el cobro (p. ej. fondos insuficientes) | No: requiere otro medio de pago |
| `pendiente` | El ACL Worker | La pasarela no respondió: HTTP 503, más de 700 ms o circuito abierto | **Sí** |

**La pasarela solo responde `cobrado` o `rechazado`**, siempre con HTTP 200. Cuando tiene problemas no devuelve un
estado, sino una falla HTTP (503 o una conexión colgada); el ACL Worker la traduce en `pendiente` para que nunca llegue
un 5xx al cliente (regla #4). `cobrando` es un estado interno de PAYMENTS. Así, el recorrido completo es: PAYMENTS crea
el cobro en `cobrando` → la pasarela responde `cobrado` o `rechazado` → si no responde, queda `pendiente`.

El `motivo` explica el estado: `pasarela_no_disponible` o `circuito_abierto` (los pone el ACL Worker en un
`pendiente`) y `fondos_insuficientes` (lo pone la pasarela en un `rechazado`).

Cómo produce cada estado el mock de la pasarela, por modo:

| Modo del mock | Respuesta | Estado final en PAYMENTS |
|---|---|---|
| `sano` | 200 con `cobrado` | `cobrado` |
| `lento` | 200 con `cobrado`, pero después de 750–1.100 ms (el cobro sí queda hecho) | `pendiente` (el ACL corta a los 700 ms); al reintentar, la conciliación lo deja `cobrado` sin cobrar de nuevo |
| `error` | HTTP 503, sin registrar el cobro | `pendiente` |
| `caido` | Conexión colgada 60 s | `pendiente` |
| `rechaza` | 200 con `rechazado` y `motivo: fondos_insuficientes` | `rechazado` |

## 5. Qué se construyó

| Pieza | Cambio |
|---|---|
| **Contrato** (v0.2) | `cobros(polizaId, estado)`; `Cobro` suma `motivo` e `intentos` (opcionales) |
| **Mock** (`stubs/pasarela`) | Recuerda cobros por clave; modo `lento` registra antes de tardar; modo `rechaza`; `GET /v1/cobros/{referencia_comercio}` |
| **ACL Worker** | `PuertoPasarelaPagos.consultar`; `GET /pagos/cobros/{referencia_comercio}` (siempre 200); `motivo=circuito_abierto`; póliza enmascarada en logs |
| **PAYMENTS** | `POST /cobros/buscar` (filtros en el cuerpo), `GET /cobros/{id}`, `POST /cobros/{id}/reintentar`; `procesar_reintento` concilia en el bucle y en el botón |
| **BFF** | `cobros` y `reintentarCobro` reales, solo BACKOFFICE con token |

## 6. Pruebas

| Nivel | Resultado |
|---|---|
| Unitarias | Mock 15, ACL Worker 65, PAYMENTS 39, BFF 90 (incluida la equivalencia con el contrato y que la póliza no viaje en la URL) |
| Newman | 51 peticiones y 116 aserciones (carpeta nueva 9). En 6 corridas, 5 sin fallos y 1 con 4 fallas **en la oferta de HU-W01**, no en cobros (ver limitaciones) |
| Humo | Paso 6: pasarela lenta → reintento conciliado con una sola referencia |
| En vivo | Pasarela lenta: **1 solo cobro** enviado y la misma referencia. Caída de 60 s: `intentos` siguió en 1 y el cobro quedó `cobrado` a los 3 s de volver la pasarela |

## 7. Cómo probarlo paso a paso

Desde la raíz del repo, en **una sola terminal** (los pasos reutilizan variables). Datos ficticios.

### Paso 0. Atajos

```bash
BFF=http://localhost:8000/graphql/web
PAY=http://localhost:8004
PASARELA=http://localhost:8103

# gql '<cuerpo JSON>' [token]
gql() {
  if [ -n "$2" ]; then
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -H "Authorization: Bearer $2" -d "$1"
  else
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -d "$1"
  fi
  echo
}
# campo <ruta>: extrae un campo de la respuesta JSON (sin jq)
campo() { python3 -c "import sys,json; d=json.load(sys.stdin); [d:=d[k] for k in sys.argv[1].split('.')]; print(d)" "$1"; }
modo() { curl -s -X POST "$PASARELA/control/modo" -H 'Content-Type: application/json' -d "{\"modo\":\"$1\"}"; echo; }
```

### Paso 1. Levantar el stack y entrar como BACKOFFICE

```bash
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios up -d --build --wait
TOKEN_BO=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"admin.solventa\", clave:\"admin-local-solventa\"}) { ... on Sesion { token } } }"}' | campo data.iniciarSesion.token)
echo "${TOKEN_BO:0:20}..."
```

**Esperado:** el inicio de un JWT. Si el `up` falla porque Postgres queda `unhealthy` en el primer arranque, repítelo.

### Paso 2. Un cobro con la pasarela lenta

```bash
CLAVE="demo-$(date +%s)"
modo lento
ID=$(curl -s -X POST "$PAY/cobros" -H 'Content-Type: application/json' \
  -d "{\"clave_idempotencia\":\"$CLAVE\",\"poliza_id\":\"POL-DEMO-1\",\"monto\":\"90000.00\",\"moneda\":\"COP\",\"token_medio_pago\":\"tok_demo\"}" | campo id)
modo sano
curl -s "$PAY/cobros/$ID" | campo estado
curl -s "$PASARELA/v1/cobros/$CLAVE" | campo referencia
```

**Esperado:** PAYMENTS dice `pendiente`, pero la pasarela devuelve una referencia `pg-…`: **la pasarela sí cobró**.
**Demuestra:** el caso que antes terminaba en doble cobro. (La creación de cobros se hace directo en PAYMENTS porque
el BFF no crea cobros en este alcance.)

### Paso 3. El back-office ve el cobro

```bash
gql '{"query":"query($p: ID) { cobros(polizaId: $p) { id estado monto { valor moneda } motivo intentos } }","variables":{"p":"POL-DEMO-1"}}' "$TOKEN_BO"
```

**Esperado:** el cobro con `"estado":"PENDIENTE"` y `"motivo":"pasarela_no_disponible"`, o ya `COBRADO` si el bucle
automático (cada 5 s) se te adelantó.
**Demuestra:** CA-W05-01, la tabla de cobros por póliza con su estado.

### Paso 4. Reintentar: concilia sin cobrar de nuevo

```bash
gql '{"query":"mutation($id: ID!) { reintentarCobro(cobroId: $id) { estado intentos } }","variables":{"id":"'"$ID"'"}}' "$TOKEN_BO"
echo "PAYMENTS: $(curl -s "$PAY/cobros/$ID" | campo referencia)"
echo "Pasarela: $(curl -s "$PASARELA/v1/cobros/$CLAVE" | campo referencia)"
```

**Esperado:** `"estado":"COBRADO"` y **la misma referencia** en PAYMENTS y en la pasarela.
**Demuestra:** CA-W05-02 con conciliación: no hubo un segundo cobro. Repetir el `reintentarCobro` (doble clic) devuelve
el mismo cobro sin cambios.

### Paso 5. Un rechazo

```bash
modo rechaza
ID_R=$(curl -s -X POST "$PAY/cobros" -H 'Content-Type: application/json' \
  -d "{\"clave_idempotencia\":\"rech-$(date +%s)\",\"poliza_id\":\"POL-DEMO-1\",\"monto\":\"90000.00\",\"moneda\":\"COP\",\"token_medio_pago\":\"tok_demo\"}" | campo id)
modo sano
gql '{"query":"query { cobros(polizaId: \"POL-DEMO-1\", estado: RECHAZADO) { estado motivo } }"}' "$TOKEN_BO"
gql '{"query":"mutation($id: ID!) { reintentarCobro(cobroId: $id) { estado } }","variables":{"id":"'"$ID_R"'"}}' "$TOKEN_BO"
```

**Esperado:** el cobro `RECHAZADO` con `"motivo":"fondos_insuficientes"`, y el reintento con el error
"El cobro fue rechazado por la pasarela: requiere otro medio de pago." (HTTP 200, nunca un 5xx).

### Paso 6. Una caída larga ya no agota el cobro

```bash
modo error
ID_C=$(curl -s -X POST "$PAY/cobros" -H 'Content-Type: application/json' \
  -d "{\"clave_idempotencia\":\"caida-$(date +%s)\",\"poliza_id\":\"POL-DEMO-2\",\"monto\":\"70000.00\",\"moneda\":\"COP\",\"token_medio_pago\":\"tok_demo\"}" | campo id)
sleep 60
echo "tras 60 s de caída: intentos=$(curl -s "$PAY/cobros/$ID_C" | campo intentos), circuito=$(curl -s localhost:8005/circuitos | campo pasarela)"
modo sano
sleep 10
curl -s "$PAY/cobros/$ID_C" | campo estado
```

**Esperado:** tras 60 s, `intentos=1` y el circuito `opened`; diez segundos después de restaurar, `cobrado`.
**Demuestra:** los intentos que no llegan a la pasarela no cuentan (antes, ~50 s de caída dejaban el cobro atascado).

### Paso 7. Permisos

```bash
gql '{"query":"{ cobros { id } }"}'
```

**Esperado:** `"message":"Debes iniciar sesión."`: los cobros exigen token aun con la bandera en `opcional`.

### Paso 8. Logs sin datos personales

```bash
docker compose logs acl-worker payments bff --no-color | grep -c "POL-DEMO" || true
```

**Esperado:** `0`: el ACL Worker, PAYMENTS y el BFF no dejan la póliza en los logs (regla #6). Por eso la búsqueda
de cobros manda sus filtros en el **cuerpo** (`POST /cobros/buscar`): una póliza en la URL quedaría escrita en el log
de acceso de PAYMENTS y en el log de `httpx` del BFF. Lo detectó este mismo paso al verificar el documento.

### Paso 9. Pruebas automáticas y apagar

```bash
python3 scripts/humo_bff.py
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios down -v
```

**Esperado:** `OK: humo del BFF en verde` y Newman con 116 aserciones, 0 fallidas.

## 8. Limitaciones conocidas

| Limitación | Efecto | Cómo se cierra |
|---|---|---|
| **Bucle y botón a la vez** | Si el bucle y el back-office reintentan el mismo cobro en el mismo instante, los dos concilian; la clave idempotente evita el doble cobro, pero se pueden registrar dos intentos | Bloqueo de fila o Cloud Tasks con deduplicación |
| **Cobro atascado en `cobrando`** | Si el proceso se cae a mitad de un cobro, queda `cobrando` y el reintento lo devuelve tal cual | Un barrido que concilie los `cobrando` viejos |
| **El mock vive en memoria** | Reiniciar el contenedor del mock olvida los cobros; la conciliación diría `no_encontrado` | Es un simulador; una pasarela real persiste sus cobros |
| **Bucle local** | El reintento automático es un bucle en el proceso, no Cloud Tasks | Cloud Tasks cuando exista el proyecto GCP |
| **PAYMENTS → ACL sin token** | En Cloud Run esas llamadas recibirían 403 | Pendiente ya documentado en `docs/arquitectura-backend.md` |
| **Falla intermitente en Newman (oferta de HU-W01)** | En 1 de 6 corridas, justo después del humo, la oferta de la carpeta 3 salió `PRELIMINAR` en vez de `COMPLETA` (4 aserciones). No se reprodujo en 4 repeticiones. Esta rama no toca las fuentes de la oferta | Sin causa confirmada. Hipótesis: la carrera de la sonda del circuito que `docs/arquitectura-backend.md` ya reconoce ("no es 100 % infalible") tras el escenario degradado del humo. Vigilarlo en el CI; si se repite, guardar el `newman ... -r json` para ver qué fuente vino degradada |

## 9. Fuera de alcance

Crear cobros desde el BFF y la captura de tarjeta (CA-W05-03, frontend), Cloud Tasks real, eventos a Pub/Sub, la orden
de indemnización (CA-W05-07, Sprint 3) y el arranque frágil de Postgres en el primer `up` (un `fix(infra)` aparte).
