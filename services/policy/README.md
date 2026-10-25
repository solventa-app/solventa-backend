# policy

POLICY: propuestas y pólizas (HU-W03, KAN-26). Servicio nuevo del Sprint 2.

## Responsabilidad

Único escritor de las tablas de suscripción (regla de arquitectura #1). Hoy, los **bloques 1, 2, 4 y 5** del plan
([`docs/plan-completar-hu-w03.md`](../../docs/plan-completar-hu-w03.md)): aceptar una oferta COMPLETA y guardarla como
propuesta, firmarla con evidencia de no repudio y emitir la póliza tras la decisión de UNDER y el primer cobro, generar su PDF y avisar al
cliente.

- `POST /propuestas` `{oferta_id, actor_usuario_id?}`: lee la oferta de RATING (`GET /ofertas/{id}`) y crea la
  propuesta en estado `ACEPTADA`. **El precio sale de RATING, nunca del cliente.** Idempotente por `oferta_id`: aceptar
  dos veces (incluso en paralelo) devuelve la misma propuesta, aunque RATING ya haya dejado vencer la oferta.
  - `404 OFERTA_NO_ENCONTRADA`: la oferta venció o nunca existió.
  - `422 OFERTA_PRELIMINAR` / `OFERTA_VENCIDA`: no se puede aceptar.
  - `503 RATING_NO_DISPONIBLE`: RATING no responde; el BFF lo convierte en un error con reintento (nunca un 5xx al cliente).
- `GET /propuestas/{id}`: la propuesta, o 404.
- `GET /propuestas/{id}/documento-firma`: lo que el cliente lee antes de firmar (CA-W03-02): resumen, `monto_mensual`,
  `meses`, `forma_cobro` y `version`. Se arma siempre desde la propuesta guardada.
- `POST /propuestas/{id}/firma` `{autoriza_cobro, version_documento, firmante_id}` (CA-W03-03): firma con el proveedor **vía
  el ACL Worker** (solo el hash del documento) y guarda la evidencia; la propuesta pasa a `EN_EMISION`. Idempotente: firmar
  dos veces devuelve la misma evidencia. `422 COBRO_NO_AUTORIZADO`; `409 VERSION_DOCUMENTO_DESACTUALIZADA` /
  `PROPUESTA_NO_FIRMABLE`; `503 SERVICIO_FIRMA_NO_DISPONIBLE` (el proveedor no responde: reintentar; no queda evidencia).
  Al firmar se encola la emisión (ver abajo).

## Emisión de la póliza (bloque 4)

`POST /propuestas/{id}/_emitir` (privado: lo invoca Cloud Tasks; en local, un hilo) avanza la propuesta. Idempotente.
`POST /propuestas/{id}/_reconciliar-cobro` y `POST /reconciliacion/cobros` (privados) reabren una propuesta cuyo pago se recuperó (ver más abajo).

```
EN_EMISION ──UNDER──► RECHAZADA        ─► NO_EMITIDA
                   ├─ REVISION_MANUAL  ─► EN_REVISION
                   └─ APROBADA ──cobro──► cobrado    ─► EMITIDA  (póliza + evento `poliza.emitida`)
                                      ├─ rechazado  ─► PAGO_RECHAZADO
                                      └─ pendiente  ─► PENDIENTE_PAGO (se vuelve a consultar cada ~10 s, hasta 12 veces)
```

- **D-03:** se cobra antes de emitir, y UNDER decide antes del cobro para no cobrar a quien luego se rechaza. Un cobro
  pendiente o rechazado **nunca** deja una póliza emitida (CA-W03-06).
- **Idempotencia:** la orden de cobro lleva un `id_intento_cobro` derivado de la propuesta (`uuid5`), así que PAYMENTS
  devuelve el cobro que ya existe sin cobrar de nuevo; así se vuelve a consultar un cobro pendiente. La póliza es `UNIQUE`
  por propuesta y el cambio de estado es condicional. Verificado: 8 emisiones simultáneas dejan 1 póliza y 1 cobro.
- **Reabrir un pago que se recuperó (CA-W03-06, W03-14):** `PAGO_RECHAZADO` no se emite por la emisión normal, pero tampoco es
  final. Si el back-office reintenta el cobro (el BFF avisa a `_reconciliar-cobro`) o corre el barrido (`POST /reconciliacion/cobros`),
  POLICY vuelve a enviar la misma orden de cobro —que PAYMENTS contesta con el cobro que ya existe, **sin cobrar de nuevo**— y,
  si ya está `cobrado`, emite la póliza (idempotente, con un solo aviso). Si sigue rechazado no cambia nada; si el cobro se está
  reintentando vuelve a `PENDIENTE_PAGO` con otra tanda de consultas. **Solo el back-office y la reconciliación lo disparan: el
  cliente no.** El barrido revisa hasta `RECONCILIACION_LOTE_MAX` propuestas de los últimos `RECONCILIACION_DIAS` días y devuelve un
  resumen (`revisadas`, `emitidas`, `rechazadas`, `pendientes`, `fallidas`); una que falle no detiene a las demás. En la nube lo
  dispara Cloud Scheduler (`reconciliacion-cobros-policy`, `infra/tareas.tf`, cada `reconciliacion_cobros_cron`: 15 min por defecto; se crea al aplicar la
  infraestructura desde `main`); en local se llama a mano.
- **Fallas transitorias:** si UNDER o PAYMENTS no responden, `_emitir` contesta `503 EMISION_PENDIENTE`, la propuesta no
  cambia y Cloud Tasks reintenta (o el hilo local). Nunca se decide a ciegas ni se da el cobro por hecho.
- **Quién dispara:** `DISPARADOR=bucle` (local y CI) usa un hilo; `externo` (Cloud Run) usa Cloud Tasks (cola
  `emision-polizas`). Firmar encola la emisión, y volver a firmar una propuesta que quedó `EN_EMISION` la reencola.
- `GET /polizas/{id}`: número (`SOL-AAAA-NNNNNN`), vigencia, prima y cobertura. `poliza.emitida` va al bus de **log**
  (Pub/Sub real solo está aprobado para `perfil.actualizado`, W05-8; ver W03-7 en el plan).
- La póliza no guarda dinero: la prima y la cobertura viven cifradas en la propuesta.

### El documento y el aviso (bloque 5)

- `POST /polizas/{id}/documento` `{asegurado?}` → el PDF (`application/pdf`, `Cache-Control: no-store`). Se genera **al pedirlo**,
  a partir de los datos guardados (no se almacena, así nunca queda desactualizado): membrete, asegurado, plazo y valor asegurado,
  prima mensual, vigencia y el sello de la firma (referencia, fecha y huella SHA-256 del documento firmado). Es un `POST` para que
  el nombre del asegurado viaje en el **cuerpo** y no en la URL (regla #6); no se guarda ni se loguea. Sin él, el documento
  identifica al titular por la referencia opaca de su cliente. Quién puede pedirlo lo decide el BFF.
- Con `DOCUMENTO_AMBIENTE_PRUEBAS=true` (por defecto) el PDF avisa que la firma y el cobro son simulados y que no tiene validez
  legal. Apagarlo solo con un proveedor real.
- **Aviso al cliente, una sola vez por póliza:** al emitir, POLICY avisa al titular que firmó por el ACL Worker con el id
  `poliza-emitida-{id}` (idempotente en el proveedor) y registra `polizas.notificada_en`. Si el aviso no sale, **la póliza queda
  emitida** (no se deshace) y `_emitir` contesta 503 para que Cloud Tasks (o el hilo local) reintente solo el aviso; ya notificada,
  repetir la emisión no avisa de nuevo.
- **El aviso lleva el PDF (bloque 8, decisiones W03-16 a W03-18).** Para avisar, POLICY le pide a AUTH el contacto del cliente
  (`GET /usuarios/{id}/contacto`) y su nombre (`POST /usuarios/nombres`), y los descarta: **no guarda ni el contacto ni el nombre,
  ni los escribe en logs** (regla #6). Según lo que tenga el cliente:
  - **Correo:** el aviso sale por correo con la póliza en PDF adjunta, generada con el nombre que dio AUTH (el nombre vive solo en
    memoria mientras se genera el PDF; el PDF tampoco se guarda, W03-9).
  - **Solo teléfono:** sale por SMS con el enlace, sin PDF (un SMS no lleva archivos).
  - **Ninguno (hoy son todos los clientes ya creados):** la póliza se emite igual y el aviso se **omite** con
    `polizas.aviso_omitido_motivo = SIN_CONTACTO`, sin reintentar para siempre; el cliente la descarga desde la web. Si el ACL
    rechaza la solicitud (422), el motivo es `AVISO_RECHAZADO`.
  - **Si AUTH no responde NO se asume que no hay contacto:** `_emitir` contesta 503, el aviso se reintenta y la póliza ya emitida no
    se toca.
  Sin `AUTH_URL` configurada el aviso sale como antes (solo con el enlace).

## Diseño

- **Capas simples** (sin puertos y adaptadores): hexagonal es solo para `acl-worker` (regla #3). POLICY no llama a
  proveedores externos; la firma, el KYC y las notificaciones pasarán por el ACL Worker (regla #2).
- **Idempotencia en la base:** `oferta_id` es `UNIQUE` y el alta es `INSERT ... ON CONFLICT (oferta_id) DO NOTHING
  RETURNING` con el pool en `autocommit`, igual que PAYMENTS. Verificado en vivo con 8 aceptaciones simultáneas: una sola
  fila.
- **Cifrado en reposo (HA-SEG-002):** la prima, la cobertura y el plazo se guardan cifrados por campo (AES-GCM, el id de la
  propuesta como dato asociado). El `cliente_id` es el identificador opaco (HMAC) que calcula el BFF, nunca un documento.
- **Sin datos personales en logs** (regla #6): solo `id_opaco(...)` del cliente, la oferta, la propuesta y el firmante.
- **Verificado en vivo** (Postgres real + ACL Worker real + stub de firma): con el proveedor `lento` el ACL corta a ~719 ms,
  POLICY responde 503 reintentable y el reintento devuelve la misma referencia que el proveedor ya tenía; con el proveedor
  caído el circuito abre al 3.er intento y las siguientes llamadas fallan en ~10 ms; la sonda lo cierra sola.

## Esquema

`propuestas` (migración `0001_propuestas.py`): `id`, `oferta_id` (UNIQUE), `cliente_id`, `consentimiento_id`,
`actor_usuario_id`, `estado` (CHECK con los 7 estados del contrato), `oferta_cifrada`, `creado_en`, `actualizado_en`.
`version_reglas` (migración `0005_version_reglas.py`, nula en las propuestas anteriores): la versión de las reglas de RATING con las que se fijó el precio
(CA-W03-01). Va en una columna y no en `oferta_cifrada` porque no es un dato personal y sirve para saber con qué reglas se vendió cada póliza; la API no la expone.
`evidencias_firma` (migración `0002_evidencias_firma.py`): `propuesta_id` (UNIQUE, FK), `documento_hash`,
`version_documento`, `documento_cifrado` (el documento exacto que se hasheó, cifrado), `firmante_id` (id opaco),
`referencia_proveedor`, `firmada_en`. **Append-only en la base**: triggers rechazan `UPDATE`, `DELETE` y `TRUNCATE`. Con
`documento_cifrado` se recalcula el hash y se prueba qué se firmó. `polizas` (migraciones `0003_polizas.py`, `0004_poliza_notificada.py`, que suma `notificada_en`, y `0006_aviso_omitido.py`, que suma `aviso_omitido_motivo`, el motivo por el que no se avisó): `propuesta_id` (UNIQUE, FK), `numero` (de la secuencia `polizas_numero_seq`, UNIQUE), `cliente_id` opaco, `vigencia_desde` y `vigencia_hasta`. `propuestas` suma `intentos_emision` (acota los reintentos del cobro pendiente) y `motivo_estado` (códigos de por qué quedó en revisión o no se emitió).

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `DATABASE_URL` | — (obligatoria) | PostgreSQL `policy` |
| `RATING_URL` | `http://localhost:8003` | Dónde leer la oferta |
| `POLICY_RATING_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima a RATING |
| `CIFRADO_CLAVE` | llave local pública | 32 bytes en base64; en Cloud Run llega de Secret Manager (`clave-cifrado`) y no puede ser la local |
| `ACL_URL` | `http://localhost:8005` | ACL Worker (la firma pasa por él, nunca directo al proveedor) |
| `POLICY_ACL_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima al ACL (el ACL corta a 700 ms) |
| `UNDER_URL`, `PAYMENTS_URL` | `http://localhost:8008`, `http://localhost:8004` | Decisión y primer cobro |
| `AUTH_URL` | `http://localhost:8001` | AUTH: el contacto y el nombre del cliente para el aviso con PDF (bloque 8) |
| `POLICY_AUTH_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima a AUTH |
| `POLICY_UNDER_TIMEOUT_SEGUNDOS`, `POLICY_PAYMENTS_TIMEOUT_SEGUNDOS` | `3.0`, `5.0` | Espera máxima |
| `TOKEN_RECAUDO_CREDITO` | `tok_recaudo_local` | Token opaco del convenio de recaudo con el banco del crédito (W03-5, REWIRE): la prima va con la cuota del crédito, no con tarjeta. En la nube no puede ser el valor local |
| `DISPARADOR` | `bucle` | `externo` en Cloud Run (los fija Terraform): `GCP_PROYECTO`, `GCP_REGION`, `TAREAS_CUENTA`, `URL_PROPIA`, `TAREAS_COLA` |
| `DOCUMENTO_AMBIENTE_PRUEBAS` | `true` | Pie del PDF que avisa que la firma y el cobro son simulados |
| `COBRO_PENDIENTE_DEMORA_SEGUNDOS`, `EMISION_MAX_INTENTOS` | `10`, `12` | Cada cuánto se vuelve a consultar un cobro pendiente y cuántas veces |
| `RECONCILIACION_LOTE_MAX`, `RECONCILIACION_DIAS` | `100`, `30` | Cuántas propuestas revisa el barrido por pasada y hasta cuántos días atrás mira un pago rechazado o pendiente |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run (RATING, el ACL, UNDER y PAYMENTS son privados) |

## Migraciones y pruebas

```bash
bash scripts/verificar-migraciones.sh policy   # upgrade, downgrade a base y upgrade, contra Postgres real
cd services/policy && python -m pytest
```

Código copiado de PAYMENTS (sin código compartido entre servicios): `observabilidad.py`, `autenticacion_servicios.py`,
`cifrado.py` (con la diferencia de que un valor en claro no se acepta como cifrado, porque aquí no hay filas anteriores) y
el esqueleto de Alembic.
