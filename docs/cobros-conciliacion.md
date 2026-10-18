# Cobros en el back-office: conciliar y reintentar (HU-W05, KAN-28)

Cómo el back-office ve los cobros de una póliza y reintenta los que no entraron, **sin cobrar dos veces**. Cubre
CA-W05-01 (tabla de cobros por póliza con su estado) y CA-W05-02 (reintentar con el mismo `id_intento_cobro`), y la
meta del Sprint 1: *"el back-office cobra y **concilia** pagos"*.

Documentos relacionados: [README de PAYMENTS](../services/payments/README.md) · [README del ACL Worker](../services/acl-worker/README.md) ·
[mock de la pasarela](../stubs/pasarela/README.md) · [contrato GraphQL](../contracts/graphql/schema.graphql) ·
[autenticación](autenticacion-usuarios.md) · [pruebas Newman](../tests/newman/README.md).

## Glosario: los tres identificadores de un cobro

| Nombre | Quién lo genera | Para qué |
|---|---|---|
| `cliente_id` (`clienteId`) | AUTH (id del usuario CLIENTE) | A quién se le cobra; PAYMENTS guarda el id y AUTH el nombre |
| `id` (cobro) | PAYMENTS (UUID) | Identifica el registro en Solventa: es el que usan `reintentarCobro` y `GET /cobros/{id}` |
| `id_intento_cobro` (`idIntentoCobro` en GraphQL y en el evento) | Quien ordena el cobro (el frontend genera un UUID por intento) | Idempotencia: si la misma orden llega dos veces, se cobra una sola vez. Con otros datos, PAYMENTS responde 409 |
| `referencia` (`pg-…`) | La pasarela | El comprobante del cobro en el proveedor |

En la pasarela, el `id_intento_cobro` se llama `referencia_comercio`: el adaptador del ACL Worker traduce el nombre en
la frontera. Antes se llamaba `clave_idempotencia`; se renombró en todo el producto (KAN-28, migraciones `0003` y `0004`
de PAYMENTS y evento `cobro.estado-cambiado` v2).

### El nombre del cliente

El back-office ve **quién es el cliente** de cada cobro. El nombre es dato personal, así que:

- **Vive en AUTH**, cifrado por campo (AES-256-GCM, `services/auth/app/cifrado.py`), en `usuarios.nombre_cifrado`. Cualquier
  rol puede tener nombre; en los cobros se muestra el de los CLIENTE. PAYMENTS guarda únicamente el `cliente_id` (escritor único: cada servicio escribe lo suyo).
- **Sale solo por dos caminos:**
  1. **El nombre del cliente en un cobro:** `cobros`, `crearCobro` y `reintentarCobro`, que son solo BACKOFFICE y siempre
     con token. El ASESOR y el CLIENTE no entran a pagos.
  2. **El nombre propio:** `Usuario.nombre` en `iniciarSesion` y `yo`, y cada usuario recibe únicamente el suyo. En
     `usuarios` (la lista) y en `crearUsuario` va en `null`.

  El nombre **no viaja en el JWT**: va en base64, sin cifrar, y valdría 60 minutos aunque cambiara. El front lo pide por
  GraphQL tras iniciar sesión y, si llega `null`, muestra el alias.
- **El BFF compone la respuesta:** pide a AUTH los nombres de **todos** los clientes de la lista en una sola llamada (de
  a 200, el máximo de AUTH), no uno por cobro. Si AUTH no responde, los cobros llegan igual con `cliente.nombre` en
  `null`: ver el cobro importa más que ver el nombre.
- **Auditoría:** cada consulta deja un log con quién la hizo (id opaco) y cuántos clientes pidió; nunca los nombres ni los ids.
- **Limitación:** `clienteId` lo declara quien ordena el cobro. Mientras no exista el servicio de pólizas, nada valida que la
  póliza sea de ese cliente.
- **Llave:** `CIFRADO_CLAVE` (local, no secreta; en la nube, el secreto `clave-cifrado`). Pendiente pasar a Cloud KMS (D-04).

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
| **Consulta por referencia del comercio** | Se busca el cobro por el `id_intento_cobro` que Solventa envía (en la pasarela, `referencia_comercio`) | Así consultan las pasarelas reales (Wompi `reference`, PayU `referenceCode`): cambiar de pasarela sigue siendo solo un adaptador (CA-W05-06) |
| **Intentos que no cuentan** | Si el circuito está abierto o la pasarela no responde a la consulta, no se gasta intento | Cierra el hueco 2 |
| **Qué se reintenta** | `pendiente` (conciliar y cobrar con la misma clave) y, desde el 2026-10-17, `rechazado` **hasta 3 veces** a mano (prototipo W05-01). `cobrado` o `cobrando` se devuelven sin tocarlos | Un doble clic no cobra dos veces; agotados los 3, "requiere otro medio de pago" |
| **Referencia por reintento de un rechazado** | Cada reintento manual de un rechazado va a la pasarela con su propia referencia: `{idIntentoCobro}-r1`, `-r2`, `-r3` | La pasarela es idempotente por referencia: con la original devolvería el mismo rechazo |
| **Mock realista, sin base de datos** | La pasarela simulada recuerda cada cobro **en memoria** por clave, puede rechazar y se puede consultar | Imita a un proveedor real sin sobre-construir |
| **Crear cobros desde el BFF** | `crearCobro(entrada)`: el cliente envía `idIntentoCobro` y lo reenvía igual si repite; un conflicto llega como `ErrorCobro` | Un doble clic no crea otro cobro |
| **Permisos** | `cobros`, `crearCobro` y `reintentarCobro`: solo `BACKOFFICE`, **siempre con token** (aun con la bandera en `opcional`) | Son datos de pagos |

## 3. Flujo del reintento

```
Back-office ─► BFF  reintentarCobro(cobroId)         solo BACKOFFICE, con token
                │
                ▼
            PAYMENTS  POST /cobros/{id}/reintentar
                │  ¿estado?  cobrado/cobrando → devolver tal cual
                │            rechazado        → intento nuevo con referencia …-rN (hasta 3);
                │                               agotados → error "requiere otro medio de pago"
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
| `rechazado` | La pasarela | Respondió, pero rechazó el cobro (p. ej. fondos insuficientes) | **Sí, a mano, hasta 3 veces** (`reintentosRestantes`); después requiere otro medio de pago |
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
| **Nombre propio e `idTransaccion` (2026-10-18)** | Contrato v0.6: `Usuario.nombre` (propio) y `Cobro.idTransaccion`; AUTH admite nombre en cualquier rol y lo descifra solo para el propio usuario |
| **Nombre del cliente (2026-10-17)** | Contrato v0.5; AUTH guarda `nombre_cifrado` (migración `0003`) y expone `POST /usuarios/nombres`; PAYMENTS guarda `cliente_id` (migración `0005`); el BFF compone `cobros.cliente { id nombre }` en lotes |
| **Ajustes (2026-10-17)** | Contrato v0.3/v0.4: `crearCobro` en el BFF y evento v2 con `idIntentoCobro`; renombre a `id_intento_cobro` (mock: `referencia_comercio`); PAYMENTS responde 409 si el mismo intento llega con otros datos y 422 si falta un campo |

## 6. Pruebas

| Nivel | Resultado |
|---|---|
| Unitarias | Mock 15, ACL Worker 65, PAYMENTS 39, BFF 90 (incluida la equivalencia con el contrato y que la póliza no viaje en la URL) |
| Nombre propio e `idTransaccion` (2026-10-18) | AUTH 81, PAYMENTS 47, BFF 133. Newman: 58 peticiones y 135 aserciones, 0 fallas en 2 corridas seguidas. Humo en verde. `Cobro.idTransaccion` es la referencia de la pasarela (la misma que PAYMENTS guarda en `referencia`) |
| Nombre del cliente (2026-10-17) | AUTH 74, PAYMENTS 47, BFF 109. Newman: 55 peticiones y 126 aserciones, 0 fallas en 2 corridas seguidas. Humo en verde. Nombre ilegible en la base y 0 apariciones en logs |
| Ajustes (2026-10-17) | Mock 15, ACL Worker 66, PAYMENTS 43, BFF 99. Newman: 54 peticiones y 122 aserciones, 0 fallas en 2 corridas seguidas. Humo en verde. Este paso a paso, verificado tal como está escrito |
| Newman | 51 peticiones y 116 aserciones (carpeta nueva 9). En 6 corridas, 5 sin fallos y 1 con 4 fallas **en la oferta de HU-W01**, no en cobros (ver limitaciones) |
| Humo | Paso 6: `crearCobro` con la pasarela lenta → reintento conciliado con una sola referencia |
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
ALIAS="cliente.demo.$(python3 -c "import random,string;print(''.join(random.choices(string.ascii_lowercase,k=8)))")"
ID_CL=$(gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"'"$ALIAS"'\", clave:\"clave-demo-0001\", rol:CLIENTE, nombre:\"Ana Pérez Demo\"}) { ... on Usuario { id } } }"}' "$TOKEN_BO" | campo data.crearUsuario.id)
echo "cliente: $ID_CL"
```

**Esperado:** el inicio de un JWT y el id del cliente que se crea con nombre (el alias lleva letras al azar: un alias con 6 o más
dígitos seguidos se rechaza por parecer un documento). Si el `up` falla porque Postgres queda `unhealthy` en el primer arranque, repítelo.

### Paso 2. Un cobro con la pasarela lenta

```bash
CLAVE="demo-$(date +%s)"
CREAR='mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) { __typename ... on Cobro { id estado } ... on ErrorCobro { mensaje } } }'
entrada() { echo '{"query":"'"$CREAR"'","variables":{"e":{"idIntentoCobro":"'"$1"'","polizaId":"'"$2"'","monto":{"valor":"'"$3"'","moneda":"COP"},"tokenMedioPago":"tok_demo","clienteId":"'"$4"'"}}}'; }
modo lento
ID=$(gql "$(entrada "$CLAVE" POL-DEMO-1 90000.00 "$ID_CL")" "$TOKEN_BO" | campo data.crearCobro.id)
modo sano
curl -s "$PAY/cobros/$ID" | campo estado
curl -s "$PASARELA/v1/cobros/$CLAVE" | campo referencia
```

**Esperado:** PAYMENTS dice `pendiente`, pero la pasarela devuelve una referencia `pg-…`: **la pasarela sí cobró**.
**Demuestra:** el caso que antes terminaba en doble cobro, con el cobro ordenado desde el BFF (`crearCobro`).

### Paso 2b. Reenviar la misma orden

```bash
gql "$(entrada "$CLAVE" POL-DEMO-1 90000.00 "$ID_CL")" "$TOKEN_BO"
gql "$(entrada "$CLAVE" POL-DEMO-1 1.00 "$ID_CL")" "$TOKEN_BO"
```

**Esperado:** la primera devuelve el **mismo** `id` (no se crea otro cobro); la segunda, con otro monto, un
`ErrorCobro` con "Ese id_intento_cobro ya se usó con otros datos…".
**Demuestra:** la idempotencia por `idIntentoCobro` y que un conflicto nunca se ignora en silencio.

### Paso 3. El back-office ve el cobro

```bash
gql '{"query":"query($p: ID) { cobros(polizaId: $p) { id estado monto { valor moneda } motivo intentos cliente { id nombre } } }","variables":{"p":"POL-DEMO-1"}}' "$TOKEN_BO"
```

**Esperado:** el cobro con `"estado":"PENDIENTE"` y `"motivo":"pasarela_no_disponible"`, o ya `COBRADO` si el bucle
automático (cada 5 s) se te adelantó.
Además, `"cliente":{"id":"…","nombre":"Ana Pérez Demo"}`.
**Demuestra:** CA-W05-01, la tabla de cobros por póliza con su estado y el cliente al que se le cobra.

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
ID_R=$(gql "$(entrada "rech-$(date +%s)" POL-DEMO-1 90000.00 "$ID_CL")" "$TOKEN_BO" | campo data.crearCobro.id)
modo sano
gql '{"query":"query { cobros(polizaId: \"POL-DEMO-1\", estado: RECHAZADO) { estado motivo } }"}' "$TOKEN_BO"
gql '{"query":"mutation($id: ID!) { reintentarCobro(cobroId: $id) { estado reintentosRestantes } }","variables":{"id":"'"$ID_R"'"}}' "$TOKEN_BO"
```

**Esperado:** el cobro `RECHAZADO` con `"motivo":"fondos_insuficientes"`, y el reintento (con la pasarela ya sana)
`COBRADO` con `reintentosRestantes: 2`: fue un intento nuevo, con la referencia `…-r1`. Con la pasarela en `rechaza`,
el cuarto reintento responde "Se agotaron los 3 reintentos: el cobro requiere otro medio de pago." (HTTP 200, nunca un 5xx).

### Datos de ejemplo para la pantalla W05-01

`python3 scripts/sembrar_cobros.py` crea por el BFF los 5 clientes del prototipo y un cobro para cada uno: 2 `COBRADO`,
1 `RECHAZADO` (para probar el reintento) y 2 `PENDIENTE` que el reintento automático deja `COBRADO` a los pocos segundos.
Es idempotente (alias e `idIntentoCobro` fijos) y solo sirve en local (usa el stub de la pasarela).

### Paso 6. Una caída larga ya no agota el cobro

```bash
modo error
ID_C=$(curl -s -X POST "$PAY/cobros" -H 'Content-Type: application/json' \
  -d "{\"id_intento_cobro\":\"caida-$(date +%s)\",\"poliza_id\":\"POL-DEMO-2\",\"monto\":\"70000.00\",\"moneda\":\"COP\",\"token_medio_pago\":\"tok_demo\"}" | campo id)
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

### Paso 8b. El nombre está cifrado en la base

```bash
docker compose exec -T postgres psql -U postgres -d auth -tc "SELECT nombre_cifrado FROM usuarios WHERE id='$ID_CL'"
docker compose exec -T postgres psql -U postgres -d payments -tc "SELECT count(*) FROM information_schema.columns WHERE table_name='cobros' AND column_name ILIKE '%nombre%'"
docker compose logs auth bff payments acl-worker --no-color | grep -c "Ana Pérez Demo" || true
```

**Esperado:** un texto ilegible (no "Ana Pérez Demo"); `0` columnas de nombre en `cobros`; y `0` apariciones del nombre en los logs.
**Demuestra:** regla #6: el nombre está cifrado en reposo, solo vive en AUTH y no se loguea.

### Paso 9. Pruebas automáticas y apagar

```bash
python3 scripts/humo_bff.py
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios down -v
```

**Esperado:** `OK: humo del BFF en verde` y Newman con 135 aserciones, 0 fallidas.

### Prueba automática de punta a punta de HU-W05 (local y staging)

`scripts/probar_cobros.py` recorre este mismo flujo por GraphQL, como el back-office, con la pasarela simulada en
cada modo, y dice cuántas comprobaciones pasan (27). Solo biblioteca estándar. A diferencia del humo y de Newman, que
solo corren en local, **también corre contra staging**, donde PAYMENTS, el ACL y la pasarela son privados.

```bash
# Local (stack arriba; credenciales solo locales)
python scripts/probar_cobros.py

# Staging: token de identidad de gcloud y clave de operador.demo leída del vault (no se muestra)
python scripts/probar_cobros.py --staging solventa-511815 --evidencia docs/evidencias/HU-W05-cobros-staging.md
```

| # | Escenario | Qué comprueba |
|---|---|---|
| 1 | Pasarela sana | `COBRADO` con la referencia `pg-…` de la pasarela, la misma en PAYMENTS y con el nombre del cliente |
| 2 | Idempotencia | La misma orden devuelve el mismo cobro; la misma clave con otro monto da `ErrorCobro`; sin segundo cobro |
| 3 | Pasarela lenta | `PENDIENTE` aunque la pasarela cobró; `reintentarCobro` **concilia** (misma referencia) y el doble clic no cambia nada |
| 4 | Pasarela caída (503) | `PENDIENTE` sin cobro en la pasarela; al volver se cobra una sola vez |
| 5 | Pasarela rechaza | `RECHAZADO` por `fondos_insuficientes`; quedan 2, 1 y 0 reintentos; luego "otro medio de pago"; y un rechazado se cobra si la pasarela acepta |
| 6 | Tabla y permisos | `cobros(polizaId)` y el filtro `RECHAZADO` muestran cada cobro con su estado; sin sesión no se ve nada |

- **Códigos de salida:** `0` todo en verde, `1` alguna comprobación falla, `2` no se pudo preparar (sin conexión, sesión o cliente).
- **Repetible:** cada corrida usa una póliza (`POL-E2E-…`) y claves nuevas, y deja la pasarela en `sano`. Los cobros de la prueba
  quedan en la base (datos ficticios). El CLIENTE es `cliente.demo` en staging y `cliente.cobros` en local (se crea si falta).
- **Verificada con un fallo real:** al romper a propósito la conciliación de PAYMENTS (el reintento cobraba con otra referencia),
  fallan 3 comprobaciones y sale con código `1`.
- **Evidencia:** `docs/evidencias/HU-W05-cobros-2026-10-18.md`.

## 8. Limitaciones conocidas

| Limitación | Efecto | Cómo se cierra |
|---|---|---|
| **Bucle y botón a la vez** | Si el bucle y el back-office reintentan el mismo cobro en el mismo instante, los dos concilian; la clave idempotente evita el doble cobro, pero se pueden registrar dos intentos | Bloqueo de fila o Cloud Tasks con deduplicación |
| **Cobro atascado en `cobrando`** | Si el proceso se cae a mitad de un cobro, queda `cobrando` y el reintento lo devuelve tal cual | Un barrido que concilie los `cobrando` viejos |
| **El mock vive en memoria** | Reiniciar el contenedor del mock olvida los cobros; la conciliación diría `no_encontrado` | Es un simulador; una pasarela real persiste sus cobros |
| **La pasarela de staging también** | En Cloud Run (mínimo 0 instancias), si se recicla la instancia a mitad de `probar_cobros.py`, el modo vuelve a `sano` y se olvidan los cobros; la corrida falla en esa comprobación | Repetir la corrida; con una pasarela real no aplica |
| **Bucle local** | El reintento automático es un bucle en el proceso, no Cloud Tasks | Cloud Tasks cuando exista el proyecto GCP |
| **Falla intermitente en Newman (oferta de HU-W01)** | En 1 de 6 corridas, justo después del humo, la oferta de la carpeta 3 salió `PRELIMINAR` en vez de `COMPLETA` (4 aserciones). No se reprodujo en 4 repeticiones. Esta rama no toca las fuentes de la oferta | Sin causa confirmada. Hipótesis: la carrera de la sonda del circuito que `docs/arquitectura-backend.md` ya reconoce ("no es 100 % infalible") tras el escenario degradado del humo. Vigilarlo en el CI; si se repite, guardar el `newman ... -r json` para ver qué fuente vino degradada |

## 9. Fuera de alcance

Que el CLIENTE pague su prima (ver la sección 10, Sprint 2) y la captura de tarjeta (CA-W05-03: el prototipo no la tiene), Cloud Tasks real, eventos a Pub/Sub, la orden
de indemnización (CA-W05-07, Sprint 3) y el arranque frágil de Postgres en el primer `up` (un `fix(infra)` aparte).

## 10. Pendiente para el Sprint 2: el primer cobro al firmar (HU-W03, decisión D-03)

Revisado el 2026-10-17 contra el prototipo (Figma `Solventa-web-Final`, sección HU-W03):

- **W03-02b "Confirmar firma y autorización de cobro"**: "Al firmar autorizas un cobro recurrente". La prima mensual "se
  facturará junto con la cuota de tu crédito hipotecario durante 180 meses". Al confirmar se registra la firma, se
  emite la póliza y "se activa el cobro de la prima".
- Siguen **W03-03** Firma confirmada, **W03-04** Póliza emitida y **W03-05** Documento de póliza. **No hay pantalla del
  resultado del cobro** (cobrado, pendiente o rechazado) ni de error de pago.
- **No se pide tarjeta**: la prima va con la cuota del crédito, así que el medio de pago lo pone el banco del crédito.
  Por eso no se construye la captura tokenizada de CA-W05-03; hay que ajustar su redacción en Jira.

Lo que ya existe y sirve tal cual: `crearCobro` (recibe un `tokenMedioPago` opaco, sea de tarjeta o del banco),
idempotencia por `idIntentoCobro`, estados `cobrando/cobrado/rechazado/pendiente`, reintento automático de pendientes y
reintento manual de rechazados (hasta 3) desde W05-01.

**Decisiones para el Planning del Sprint 2:**

1. **D-03:** ¿la primera prima se cobra **al firmar, antes de emitir** (recomendación del plan) o la póliza se emite y el
   cobro va por su lado?
2. Si el primer cobro queda **rechazado** o **pendiente**: ¿se emite la póliza igual, queda en espera o se cancela?
3. Qué ve el cliente después de firmar: diseñar en Figma la pantalla de resultado del pago (cobrado, en proceso o
   rechazado con qué hacer). El plan del frontend ya lo marca como faltante (`solventa-frontend-web/docs/plan-frontend.md`).
4. De dónde sale el `tokenMedioPago` cuando la prima va con la cuota del crédito (¿el banco del crédito vía Open
   Finance, un convenio de recaudo?), y quién llama a `crearCobro`: hoy solo lo puede llamar un BACKOFFICE.

