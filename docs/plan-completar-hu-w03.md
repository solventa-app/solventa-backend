# Plan para completar HU-W03 · Backend

> Plan del **backend** de HU-W03 (KAN-26), Sprint 2. Las pantallas (KAN-210 y KAN-214) viven en `solventa-frontend-web`.
> Reglas compartidas con los planes de W01 y W05: las metas se miden en staging con proveedores simulados, ningún bloque
> crea costo nuevo sin confirmación, cada bloque actualiza sus documentos en el mismo PR y lo ya cerrado no se reinterpreta.
> Las decisiones marcadas **Propuesta** esperan el visto bueno del equipo; las **Cerrada** ya las tomó el equipo.

**Historia.** Como cliente o asesor comercial, al aceptar la oferta personalizada quiero pagar y firmar electrónicamente
para que la póliza digital se emita de forma automática e inmediata.

**Tareas de Jira.** KAN-209 a KAN-216 (S2-W03-1 a S2-W03-8) y las subtareas KAN-152 a KAN-157 y KAN-172.
**Habilitadoras.** HA-MOD-001 (ACL), HA-LAT-002 (circuit breaker y timeout duro).
**Escenarios.** EC005 (decisión + emisión p95 ≤ 1,5 s), EC007 (p99 ≤ 3 s bajo pico), EC009/EC010 (timeout y degradación),
EC031 (reemplazo de adaptador).

## Estado hoy (revisado en `develop`, 2026-10-22)

- No existe nada de W03 en el backend: `Query.oferta` devuelve `null` (RATING no guarda la oferta), no hay mutación para
  aceptar ni firmar, ni servicios `under` y `policy`, ni evento `poliza.emitida`, ni PDF, ni notificaciones.
- Sirve tal cual: `crearCobro` de PAYMENTS (idempotente por `idIntentoCobro`, estados `cobrando/cobrado/rechazado/pendiente`,
  reintento automático con Cloud Tasks y manual de rechazados), el patrón puerto + adaptador + stub del ACL Worker
  (`AdaptadorPasarela`, `ServicioCobro`, `stubs/pasarela`), el cifrado por campo de AUTH, el trigger append-only del
  consentimiento y el bus de Pub/Sub de RISK.
- PAYMENTS solo publica `cobro.estado-cambiado` a log (`BusEventosLog`) y `crearCobro` en el BFF es solo de BACKOFFICE.

## Decisiones

| # | Tema | Decisión | Estado |
|---|---|---|---|
| W03-1 | Quién guarda la oferta aceptada | **POLICY** (escritor único de propuestas y pólizas). RATING deja la oferta generada en Redis con TTL igual a su vigencia y la sirve en `GET /ofertas/{id}`; POLICY la lee de ahí y **nunca confía en el precio que manda el cliente**. Si Redis la perdió, se pide cotizar de nuevo | Propuesta |
| W03-2 | D-03: cuándo se cobra la primera prima | **Al firmar y antes de emitir.** La decisión de UNDER va antes del cobro para no cobrar a quien luego se rechaza | **Cerrada** |
| W03-3 | Primer cobro pendiente o rechazado | Pendiente: la póliza espera (`PENDIENTE_PAGO`) y se emite sola al pasar a cobrado. Rechazado: no hay póliza (`PAGO_RECHAZADO`); el reintento manual de W05 la reabre | Propuesta · **Cambiada (bloque 9):** un rechazo lleva a elegir otra tarjeta y no deja la propuesta en `PAGO_RECHAZADO` |
| W03-4 | Quién llama a PAYMENTS | POLICY, de servicio a servicio (`policy` entra en el `llama` de `payments`). `crearCobro` del BFF sigue siendo solo de BACKOFFICE | Propuesta |
| W03-5 | `tokenMedioPago` | Token opaco del convenio de recaudo con el banco del crédito, por configuración (`TOKEN_RECAUDO_CREDITO`, marcado REWIRE). No hay captura de tarjeta | Propuesta · **Cambiada (bloque 9):** el token lo genera la pasarela al capturar la tarjeta en su propia página |
| W03-6 | Asincronía en Cloud Run | La emisión se agenda con Cloud Tasks (copia de `PlanificadorCloudTasks` de PAYMENTS); en local corre inline. Nada de bucles de fondo | Propuesta · **Cambiada (bloque 9):** cobro y emisión son síncronos; Cloud Tasks queda solo para el aviso |
| W03-7 | Aviso del cobro a POLICY | **Cambiada en el bloque 4: no se usa Pub/Sub.** La propuesta original (PAYMENTS publica `cobro.estado-cambiado` por Pub/Sub y POLICY lo recibe por push) ampliaba la decisión cerrada W05-8, que solo aprobó Pub/Sub para `perfil.actualizado`, y no hubo visto bueno del equipo. En su lugar, cuando el primer cobro queda pendiente POLICY lo vuelve a consultar con una tarea de Cloud Tasks, re-enviando a PAYMENTS la misma orden idempotente (devuelve el cobro existente sin cobrar de nuevo), y `poliza.emitida` va al bus de log, igual que `cobro.estado-cambiado`. Si el equipo quiere eventos reales, es una decisión aparte | Cambiada |
| W03-8 | Quién firma | Solo el CLIENTE titular firma (no repudio). El ASESOR puede aceptar la oferta pero no firmar | Propuesta · **Se mantiene (bloque 9):** el titular firma desde su enlace, no desde una sesión |
| W03-9 | PDF | Se genera al pedirlo con `fpdf2` a partir de los datos de la póliza. Sin GCS ni costo nuevo | Propuesta |
| W03-10 | KYC | Puerto `PuertoKyc` en el ACL con un stub que simula Truora. El consolidador de KYC real queda para el Sprint 3 | Propuesta |
| W03-11 | Servicios | **Dos servicios nuevos**: `under` (sin almacén, decide) y `policy` (PostgreSQL, escritor único de pólizas) | **Cerrada** |
| W03-12 | Proveedor de firma | Solventa **no** construye un servicio de firma electrónica: lo contrata a un tercero detrás del ACL. `stubs/firma` simula solo lo que es de Solventa (idempotencia por referencia, hash, modos de falla) y **no** la identidad del firmante, el certificado ni el sello de tiempo, que son del proveedor. Su contrato (`referencia_comercio`, `documento_hash`, `SIGNED`) es inventado, no el de un proveedor real. Con uno elegido, el puerto y `evidencias_firma` ganarán campos opcionales (referencia del certificado, sello de tiempo, enlace al documento firmado) en una migración aditiva. Qué tipo de firma hace exigible la póliza lo valida el equipo con legal | Propuesta · **Cambiada (bloque 9):** la identidad la prueba un código por correo; el stub sigue sin verificarla |
| W03-13 | Firma dentro del plazo del gateway (CA-W03-05) | Presupuesto propio de la firma en el ACL, **configurable y sin valores fijos en el código**: `PLAZO_RUTA_SINCRONA_MS − RESERVA_SALTOS_MS` (700 − 250 = 450 ms) para los proveedores de `PROVEEDORES_RUTA_SINCRONA`, y presupuestos por proveedor leídos como `PRESUPUESTO_MS_<PROVEEDOR>`. Hecho el 2026-10-24 (`docs/evidencias/CA-W03-05-firma-2026-10-24.md`) | **Cerrada** |
| W03-14 | Quién reabre una póliza con cobro rechazado o pendiente agotado (CA-W03-06) | **Solo el back-office y los procesos de reconciliación**, no el cliente. La reapertura es idempotente. Hecho en código y en Terraform el 2026-10-24 (`docs/evidencias/CA-W03-06-reapertura-2026-10-24.md`) | **Cerrada** |
| W03-15 | Textos de «qué hacer» y del aviso (CA-W03-06 y 08) | Revisado en Figma `Solventa-web-Final` el 2026-10-24: la pantalla «Resultado del pago» **no existe** (hay que crearla, D-03) y W03-04 dice que el documento «llega por correo o notificación». Los textos que ya sirve el BFF (`mensajeEstado`, tabla en `servicio_suscripcion.py`) son una **propuesta** para ajustar con UX; cambiarlos es editar una tabla y su traducción | Propuesta · **Cambiada (bloque 9):** el resultado del pago lo muestra la página de la pasarela y luego W03-04 |
| W03-16 | Adjuntar el PDF al aviso de póliza emitida (CA-W03-08) | **Decidido (2026-10-24): el aviso llevará el PDF, en un bloque nuevo (8).** La recomendación del análisis era dejar solo el enlace, pero se eligió adjuntarlo, con tres decisiones de diseño: **AUTH guarda el correo y el teléfono**, **el proveedor sí puede recibir el PDF** (se cambia la regla del puerto) y **POLICY pide el nombre a AUTH al emitir**. Mientras no se construya, el aviso sigue llevando el enlace | **Cerrada; bloque 8 planeado** · **Cambiada en parte (bloque 9):** AUTH guarda el contacto solo del CLIENTE; con un ASESOR va cifrado en la propuesta |
| W03-17 | Quién captura el correo y el teléfono del cliente (bloque 8) | **Confirmado (2026-10-24):** `crearUsuario` (lo usa el BACKOFFICE) y una mutación para que el propio usuario los actualice, junto a idioma y moneda. Exige una pantalla en Web, que hoy no existe. **Detalle abierto, no bloquea:** AUTH guarda ambos datos (opcionales) y el adjunto viaja por correo; un cliente que solo tenga teléfono recibe el aviso sin PDF | **Cerrada** · **Cambiada (bloque 9):** el registro del CLIENTE exige correo y cédula; ya no bloquea el aviso |
| W03-18 | Cliente sin contacto (bloque 8) | **Confirmado (2026-10-24):** la póliza se emite igual y el aviso se omite con el motivo `SIN_CONTACTO`, sin reintentar para siempre; el cliente la descarga desde la web | **Cerrada** · **Cambiada (bloque 9):** casi no ocurre, porque el correo es obligatorio al aceptar |

## Criterios de aceptación (oficiales)

Salen del documento **«Solventa Detalle de Historias de Usuario del Sprint 2 (versión 1.0.0).docx»** (carpeta `MISO/`, fuera del repo), que este
plan daba por no encontrado. Los criterios «propuestos» que había aquí **no coincidían** (en particular, 05, 06, 07 y 09).

| CA | Criterio (resumen del texto oficial) | Escenario o HA |
|---|---|---|
| CA-W03-01 | Oferta `COMPLETA` vigente aceptada: se guarda la oferta aceptada (**prima, cobertura, versión de reglas y consentimiento**) y se muestra su resumen. Una `PRELIMINAR` no se acepta: **se ofrece recalcularla** (D-11) | — |
| CA-W03-02 | El cliente ve el documento a firmar en un resumen legible y la autorización de cobro recurrente; firmar sólo se habilita al aceptarla | — |
| CA-W03-03 | Evidencia de no repudio append-only (hash del documento, titular, actor, sello de tiempo e identificador del proveedor); firma siempre el titular aunque lo acompañe un asesor | HA-MOD-001 |
| CA-W03-04 | Primera prima cobrada una sola vez con `idIntentoCobro` idempotente | HA-MOD-001 |
| CA-W03-05 | Si la firma, el KYC o la pasarela superan 700 ms o fallan: el circuito abre, **nunca llega un 5xx a la interfaz** y el cliente ve un estado claro (cobro pendiente con reintento automático, o **firma por reintentar**) | EC009, EC010, HA-LAT-002 |
| CA-W03-06 | Con un cobro rechazado o pendiente, el cliente **sabe qué pasa con su póliza y qué hacer** (D-03) | D-03 |
| CA-W03-07 | Póliza `Emitida` con número y evento `poliza.emitida`; decisión + emisión p95 ≤ 1,5 s en operación normal y p99 ≤ 3 s en pico | EC005, EC007 |
| CA-W03-08 | El cliente abre y descarga el documento formal (membrete, asegurado, crédito, cobertura y firma) y **el documento se le envía por notificación** | — |
| CA-W03-09 | Datos personales y del crédito cifrados en reposo (por campo) y en tránsito; ningún log ni traza con datos personales | EC022, HA-SEG-002 |
| CA-W03-10 | Cambiar el proveedor de firma o de KYC no cambia ni redespliega ningún servicio del Core | EC031, HA-MOD-001 |

La carga de EC005 y EC007 **tampoco la define** ese documento («k6 del flujo de aceptación», semana 2, staging).

## Flujo

```
aceptarOferta (BFF) ─► POLICY crea Propuesta(ACEPTADA) con los datos que lee de RATING
firmarPropuesta     ─► POLICY ─► ACL /firma/firmar (stub) ─► evidencia append-only
                       └─ agenda la emisión con Cloud Tasks (local: inline) ─► responde EN_EMISION
_emitir (tarea)     ─► UNDER /decisiones (KYC por ACL /kyc + reglas)
                       ├─ RECHAZADA / REVISION_MANUAL ─► NO_EMITIDA | EN_REVISION
                       └─ APROBADA ─► PAYMENTS POST /cobros (idIntentoCobro = uuid5(propuestaId))
                                      ├─ cobrado   ─► POLICY emite: póliza EMITIDA + poliza.emitida
                                      ├─ pendiente ─► PENDIENTE_PAGO; emite al llegar cobro.estado-cambiado = cobrado
                                      └─ rechazado ─► PAGO_RECHAZADO (sin póliza)
poliza.emitida      ─► POLICY ─► ACL /notificaciones (stub) con el enlace al PDF
```

## Plan de cambios (backend)

### Bloque 0 · Contratos y plan (KAN-26)

| Dónde | Cambio |
|---|---|
| `contracts/graphql/schema.graphql` | **v0.12**, solo aditivo: `aceptarOferta`, `firmarPropuesta`, `propuesta`, `documentoFirma`, `poliza` y sus tipos, enums y uniones |
| `contracts/events/poliza.emitida.schema.json` | Evento v1 con el mismo sobre que los demás |
| `contracts/CHANGELOG.md` | Entrada v0.12 y aviso al frontend (PR gemelo en `solventa-frontend-web`) |
| `services/bff/app/esquema.py`, `idioma.py` | Esqueleto de los tipos nuevos (el test de contrato compara ambos sentidos). Las consultas devuelven `null` y las mutaciones un error con reintento hasta el bloque 1 |
| `docs/` | Este plan; D-03 cerrada en `docs/sprint-1.md` y `docs/cobros-conciliacion.md` §10 |

### Bloque 1 · POLICY: propuesta y oferta aceptada (KAN-209, CA-W03-01)

| Dónde | Cambio |
|---|---|
| `services/policy` (nuevo, puerto 8007) | FastAPI con capas simples (sin hexagonal) y PostgreSQL `policy`. Migración `0001` con `propuestas`; `polizas` (bloque 4) y `evidencias_firma` (bloque 2, append-only con el trigger de `services/auth/migrations/versions/0005_*.py`) llegan en su propia migración. Cifrado por campo copiado de PAYMENTS (llave directa; el cifrado de sobre con KMS de `services/auth/app/cifrado.py` queda para cuando `policy` entre en `kms.tf`). `POST /propuestas` idempotente por `oferta_id` |
| `services/rating` | Escribir la oferta en Redis con TTL de vigencia (`OFERTA_VIGENCIA_SEGUNDOS`, 24 h) y exponer `GET /ofertas/{id}`. La solicitud lleva ahora `consentimiento_id` (aditivo) para ligar la oferta a su consentimiento |
| `services/bff` | `aceptarOferta` lee la oferta de RATING, valida el consentimiento vigente, el titular y (para un CLIENTE) que él lo registró, y luego llama a POLICY; `propuesta(id)` lee de POLICY y un CLIENTE solo ve las suyas. `PuertoPolicy` / `ClientePolicyHttp`. **`Query.oferta` sigue en `null`**: nada liga hoy al usuario con el titular de la oferta, así que exponerla por id sería una fuga (ver decisiones abiertas) |
| Registro del servicio | `.github/servicios.json`; `python scripts/validar_servicios.py` dice qué más falta (compose con `migrar-policy`, `dev/postgres/01-bases.sql`, `ci.yml`, `dependabot.yml`, `scripts/verificar-migraciones.sh`, `infra/cloud-sql.tf`) |

### Bloque 2 · Firma electrónica con no repudio (KAN-211, KAN-210, CA-W03-02, 03 y 10)

| Dónde | Cambio |
|---|---|
| `services/acl-worker` | `PuertoFirmaElectronica` en `app/domain/puertos.py` (recibe solo el hash del documento); `app/adapters/firma.py` (copia de `AdaptadorPasarela`, con `sondear()`, circuito `NOMBRE_CIRCUITO_FIRMA`, `con_reintento` y `presupuesto_ms_firma` de 700 ms); `ServicioFirma` con la misma degradación que `ServicioCobro`; `POST /firma/firmar`; segundo adaptador `firma_alterna.py` elegido por `PROVEEDOR_FIRMA` (EC031) |
| `stubs/firma` (puerto 8104) | Modos sano, lento, error y caído |
| `services/policy` | `GET /propuestas/{id}/documento-firma` y `POST /propuestas/{id}/firma`: llama al ACL, guarda la evidencia y agenda `_emitir` |
| `services/bff` | `documentoFirma` y `firmarPropuesta`, solo para el CLIENTE titular |

### Bloque 3 · UNDER con KYC (KAN-213, CA-W03-07 y 09)

| Dónde | Cambio |
|---|---|
| `services/under` (nuevo, puerto 8008) | Sin almacén. `POST /decisiones` con reglas simples (nivel de riesgo de la oferta, KYC aprobado, monto) → APROBADA, RECHAZADA o REVISION_MANUAL |
| `services/acl-worker` | `PuertoKyc` + `app/adapters/kyc.py` + circuito propio; `stubs/kyc` (puerto 8105). Si el KYC no responde, la propuesta pasa a `EN_REVISION`, nunca a 5xx |

### Bloque 4 · Cobro dentro del flujo y emisión (KAN-212, KAN-213, CA-W03-04 a 07)

| Dónde | Cambio |
|---|---|
| `services/policy` | `ServicioEmision`: UNDER decide **antes** del cobro (D-03) → si aprueba, `POST /cobros` a PAYMENTS con `id_intento_cobro = uuid5(propuesta)` → `cobrado` emite (póliza con número `SOL-AAAA-NNNNNN` de una secuencia, vigencia por el plazo y evento `poliza.emitida` al bus de log), `rechazado` deja `PAGO_RECHAZADO`, `pendiente` deja `PENDIENTE_PAGO` y se vuelve a consultar. UNDER rechaza → `NO_EMITIDA`; pide revisión → `EN_REVISION`; si UNDER o PAYMENTS no responden la propuesta no cambia y se reintenta. `POST /propuestas/{id}/_emitir` (lo invoca Cloud Tasks; 503 solo si un servicio no respondió) y `GET /polizas/{id}`. Migración `0003`: tabla `polizas` (UNIQUE por propuesta), secuencia, `intentos_emision` y `motivo_estado`. Firmar encola la emisión, y volver a firmar una propuesta que quedó `EN_EMISION` la reencola |
| `services/bff` | `poliza(id)` lee de POLICY; un CLIENTE solo ve las suyas. `Poliza.documentoUrl` apunta a `/polizas/{id}/documento`, que llega con el bloque 5 |
| `infra/` | Cola `emision-polizas` y sus permisos en `tareas.tf`; `policy` con `disparador_externo` y llamando a `payments` en `servicios.json`. **No** se crean temas de Pub/Sub ni se toca PAYMENTS (ver W03-7). El cifrado con KMS de POLICY queda fuera: cifra con la llave directa, como PAYMENTS |

### Bloque 5 · PDF y notificación (KAN-215, CA-W03-08)

| Dónde | Cambio |
|---|---|
| `services/policy` | `POST /polizas/{id}/documento` genera el PDF con `fpdf2` al pedirlo (no se almacena): membrete, asegurado, crédito, prima, vigencia y sello de la firma (referencia, fecha y huella). El nombre del asegurado va en el **cuerpo**, nunca en la URL. Al emitir, avisa al titular que firmó con `poliza-emitida-{id}` (idempotente) y registra `notificada_en` (migración `0004`); si el aviso no sale, la póliza queda emitida y se reintenta solo el aviso |
| `services/bff` | `GET /polizas/{id}/documento`: exige sesión siempre, solo CLIENTE y ASESOR, y un CLIENTE solo descarga la suya (una ajena responde 404 igual que una inexistente). Pide a AUTH el nombre del firmante solo para ponerlo en el documento; si AUTH falla, el PDF sale con la referencia opaca. `Poliza.documentoUrl` apunta a esta ruta |
| `services/acl-worker` + `stubs/notificaciones` (puerto 8106) | `PuertoNotificaciones` con circuito, presupuesto de 700 ms y sonda propios; `POST /notificaciones/enviar` degrada a `pendiente` sin 5xx. El stub es idempotente por referencia y expone `GET /control/enviados` |
| `infra/gateway/` | La ruta `GET /polizas/{poliza_id}/documento` en `openapi.yaml` (con `path_translation: APPEND_PATH_TO_ADDRESS`) y en `envoy.yaml` (`safe_regex`), porque el BFF es privado y solo el gateway lo invoca. `scripts/validar_gateway.py` compara rutas con parámetro |

### Bloque 6 · Pruebas y evidencia (KAN-216, EC005, EC007)

| Escenario | Meta | Cómo se prueba | Evidencia |
|---|---|---|---|
| EC005 | p95 ≤ 1,5 s | `tests/k6/ec005-emision.js` con `scripts/correr_k6.sh` | `docs/evidencias/EC005-*.md` |
| EC007 | p99 ≤ 3 s en pico | `tests/k6/ec007-emision-pico.js` | `docs/evidencias/EC007-*.md` |
| EC009 / EC010 | 0 % de 5xx con firma, KYC o pasarela lentos o caídos | Modos de los stubs | `docs/evidencias/` |
| EC031 | Cambiar el proveedor de firma sin tocar el Core | Prueba de reemplazo de adaptador | Prueba en `services/acl-worker/tests/` |
| Extremo a extremo | Solicitar → aceptar → firmar → `EMITIDA` → PDF | Colección Newman por el gateway `:8080` | Salida de Newman |

Más: pruebas unitarias por servicio, pruebas de contrato del esquema v0.12 y de `poliza.emitida`, y un tablero `hu-w03.json`.

### Bloque 7 · Brechas contra los criterios oficiales (en curso, casi cerrado)

Salió al cruzar lo construido con los criterios oficiales (2026-10-24). **Todo es back**; las pantallas son de Web (KAN-152 a 157, 172, 209, 210 y 214).
Cada fila se verificó en el código o en las corridas de hoy. Orden sugerido: 1, luego 2 y 3 juntas, 4, 5, 6 y el resto.

| # | CA | Brecha verificada | Qué hacer | Dónde |
|---|---|---|---|---|
| 1 | 05 | **Hecho (2026-10-24, W03-13).** Con la firma caída, `firmarPropuesta` por el gateway devolvía un 504 (el primer intento tardaba ~780 ms y el gateway corta a 700 ms) | Presupuesto propio de la firma, configurable, dentro del plazo del gateway. Newman 4.29 y 4.51 (caída y lenta) responden HTTP 200 con `ErrorFirma` por el gateway | `acl-worker` |
| 2 | 01 | **Hecho (2026-10-24).** No se guardaba la versión de reglas: RATING la calculaba pero no la incluía en la oferta ni la propuesta la recibía | RATING incluye `versionReglas` en la oferta; POLICY la guarda en `propuestas.version_reglas` (migración `0005`, solo expandir, con `downgrade` probado) | `rating`, `policy` |
| 3 | 01 | **Hecho (2026-10-24, D-11).** La `PRELIMINAR` no ofrecía recalcular: el mensaje era «Solo puedes aceptar una oferta completa.» y `puedeReintentar` era `false` | Mensaje «Esta oferta es preliminar y no se puede aceptar. Vuelve a calcularla.» y `puedeReintentar: true`; reintentar es volver a cotizar. Contrato v0.13 (sin cambio de campos) y el diccionario `EN` | `bff`, `contracts` |
| 4 | 06 | **Hecho (2026-10-24; textos en W03-15, propuesta).** `Propuesta` sólo exponía `estado`. El motivo se guardaba en POLICY pero **no se mostraba**: el cliente no sabía qué hacer | Contrato v0.15 (aditivo): `motivoEstado` (categoría para el cliente), `mensajeEstado` (qué pasa y qué hacer, en el idioma de `Accept-Language`) y `accionSugerida` (`ESPERAR` o `CONTACTAR_ASESOR`) cuando la propuesta no avanza (`EN_REVISION`, `PENDIENTE_PAGO`, `PAGO_RECHAZADO`, `NO_EMITIDA`). Lo arma el BFF (`explicar_estado`) con el estado y el motivo que POLICY ya entregaba; el código interno de UNDER o de la pasarela **no sale del BFF** (hay una prueba que lo vigila). Los textos viven en una sola tabla. Newman 4.12 y 4.21 lo verifican por el gateway | `contracts`, `bff` |
| 5 | 06 | **Hecho en código y en Terraform (2026-10-24, W03-14); el job se crea al aplicar desde `main`.** Un cobro rechazado que se reintentaba, o un `PENDIENTE_PAGO` agotado, no reabría la póliza | `reconciliar_cobro` en POLICY: reenvía la misma orden idempotente (PAYMENTS devuelve el cobro existente sin cobrar de nuevo) y, si está `cobrado`, emite. Lo disparan el BFF tras `reintentarCobro` (back-office) y el barrido `POST /reconciliacion/cobros`; el cliente no. Cloud Scheduler `reconciliacion-cobros-policy` llama al barrido cada 15 min (`reconciliacion_cobros_cron`, `infra/tareas.tf`; `terraform validate` pasa, `plan` sin correr) | `policy`, `bff` |
| 6 | 09 | **Hecho (2026-10-24).** El cifrado existía pero no había evidencia de EC022 para POLICY ni una prueba que vigilara los logs | Verificado en la base real (847 propuestas y firmas, 0 con datos en claro) y en los logs de 8 servicios (0 fugas); prueba de regresión que falla si se escribe el cliente, el firmante, el dinero o el medio de pago en un log. Informe `docs/evidencias/EC022-policy-2026-10-24.md`. Queda sin verificar el tránsito (HTTPS), las trazas y Cloud KMS real; y se señala `motivo_estado` en claro | `policy` |
| 7 | 08 | **Sin cambios de código; decidido (W03-16): se adjuntará el PDF, en el bloque 8.** El criterio dice que **el documento se envía por notificación**; hoy el aviso lleva la plantilla `poliza-emitida`, el id opaco del firmante, el número y la ruta `/polizas/{id}/documento`, no el PDF | Verificado en el código: el puerto de notificaciones está hecho para que el proveedor **no reciba el contenido del documento ni datos personales** (resuelve el contacto con su propia base), AUTH no guarda correo ni teléfono, y el nombre del asegurado solo entra al PDF al descargarlo. Además la ruta es relativa y **exige sesión** (sin token, 401; Newman 2.6): un clic desde el correo no la descarga sin una página de la web que la pida con el token | `policy`, `acl-worker` |
| 8 | 01 | **Hecho (2026-10-24).** `Query.oferta(id)` devolvía siempre `null` (su comentario decía que RATING no guarda ofertas; desde el bloque 1 sí). La pantalla KAN-152 muestra «ver fuentes» | Lee la oferta que RATING guardó (`GET /ofertas/{id}`) con la misma validación de dueño que `aceptarOferta`; `null` si venció, no existe o no es suya, y error con reintento si RATING o AUTH no responden. Web no tiene aún código ni PR de W03 que use la consulta (revisado en `solventa-app/solventa-frontend`), así que no se rompe a nadie. Se actualizaron la colección de W01 y su README, que afirmaban `null` | `bff` |
| 9 | — | **Parcial (2026-10-24).** Sin verificar de los bloques anteriores: Cloud Tasks real y `terraform plan` de la cola `emision-polizas`; la sintaxis `path_translation` en GCP API Gateway; el fallo sin explicar de «UNDER caído»; EC005 y EC007 en staging | Hecho sin staging: `path_translation` confirmado en la documentación de Google (no en un gateway real); `plan` de solo lectura dirigido a la cola y al Scheduler = **2 por crear, 0 por cambiar, 0 por destruir**; «UNDER caído» 10 de 10 veces correcto (no se repitió el fallo, que sigue sin explicarse). **Falta, y necesita una liberación a `main`** (staging corre el Sprint 1: `main` está 126 commits detrás de `develop`): Cloud Tasks real, el `plan` completo (pide la llave de MongoDB Atlas, del equipo) y EC005/EC007 en staging, con la carga por definir. Informe `docs/evidencias/bloque-7-filas-4-7-8-9-2026-10-24.md` | `infra`, `tests` |

### Bloque 8 · Adjuntar el PDF al aviso de póliza emitida (hecho en código y en local; falta staging)

Decidido el 2026-10-24 (W03-16, CA-W03-08: «el documento se le envía por notificación»). **Hecho en código y en local el 2026-10-24** (PR #325 a #328 y este); falta staging. Las cinco decisiones están tomadas (W03-16 a W03-18, esta última confirmación el 2026-10-24); queda un detalle por confirmar que no bloquea (correo, teléfono o ambos, ver W03-17).

**Decisiones tomadas (2026-10-24):**

| # | Tema | Decisión | Qué implica |
|---|---|---|---|
| 1 | A quién se envía | **AUTH guarda el correo y el teléfono del cliente.** (La recomendación del análisis era que el proveedor resolviera el contacto; se eligió guardarlo) | Dato personal nuevo en AUTH: cifrado por campo, con su migración, dentro del alcance de EC022 y de la regla #6 |
| 2 | La regla del puerto | **El proveedor sí puede recibir el PDF.** La regla «nunca recibe el contenido del documento ni un dato personal» se cambia de forma explícita | El PDF (prima, cobertura, vigencia y nombre) y el destino viajan al proveedor por HTTPS, sin guardarse ni escribirse en logs. El contrato del proveedor real se revisa con el equipo y con legal, como W03-12 |
| 3 | El nombre del asegurado | **POLICY se lo pide a AUTH al emitir** | El nombre vive en memoria solo mientras se genera el PDF (no se guarda: W03-9 se mantiene). Dependencia nueva `policy → auth`. Si AUTH no responde, la póliza se emite igual y el aviso se reintenta |

**Qué cambia, por servicio** (propuesto):

| # | Dónde | Cambio |
|---|---|---|
| 1 | `contracts` | v0.16 (aditivo): `CrearUsuarioInput` suma `correo` y `telefono` opcionales. **No se devuelven** por GraphQL (solo escritura). Opcional: `Usuario.tieneContacto` para que la web sepa cuándo pedirlo |
| 2 | `auth` | **Hecho (2026-10-24).** Migración `0007` (solo expandir: columnas nulas `correo_cifrado` y `telefono_cifrado`, con `downgrade` probado en una base aparte); validación de formato (el correo se guarda en minúsculas, el teléfono solo con dígitos y un `+` opcional); cifrado con el mismo `CifradorCampos` que `nombre_cifrado`, pero **cada campo con su propio contexto** (`correo:<id>` y `telefono:<id>`) para que un cifrado movido de columna no se descifre; **solo los CLIENTE** tienen contacto (el resto da 400); `POST /usuarios/{id}/contacto` (lo que no llega se conserva) y endpoint **privado** `GET /usuarios/{id}/contacto` para servicios, nunca para un usuario. `UsuarioResponse` suma `tiene_contacto` (solo si hay, nunca cuál). Ni el correo ni el teléfono en logs ni en etiquetas. `auth` sigue sin `llama` |
| 3 | `bff` | `crearUsuario` los recibe y los reenvía a AUTH; la captura por el propio usuario depende de W03-17 |
| 4 | `acl-worker` | **Hecho (2026-10-24).** `SolicitudNotificacion` suma `correo`, `telefono` y `adjuntos` (nombre, tipo y contenido en base64; en vez del `destino` único que se propuso, para espejar a AUTH) y el adaptador los reenvía solo si existen, así que un aviso sin contacto ni adjunto es igual al de antes. La regla del puerto cambia de forma explícita en su docstring, en el del adaptador, en el README y en `docs/arquitectura-backend.md`. Reglas del adjunto validadas en la ruta con un `422` que no repite el contenido: solo con correo, solo `application/pdf`, base64 válido y no vacío, hasta 3 y hasta `ADJUNTO_MAXIMO_BYTES` (5 MB por omisión). Ni el correo, ni el teléfono ni el contenido van a los logs. Con un PDF real de 4.059 bytes el envío tarda p95 125 ms (presupuesto de 700 ms) |
| 5 | `stubs/notificaciones` | **Hecho (2026-10-24).** Acepta `correo`, `telefono` y `adjuntos`; rechaza con `422` un adjunto sin correo o que no sea base64. En `GET /control/enviados` guarda del aviso solo el canal (`correo`, `sms` o `null`) y una huella corta del destino (12 caracteres de su SHA-256) y, de cada adjunto, solo `nombre`, `tipo`, `bytes`, `sha256` y `es_pdf`: nunca el contenido ni el destino en claro. Sigue siendo idempotente por referencia |
| 6 | `policy` | **Hecho (2026-10-24).** `_avisar` pide a AUTH el contacto y el nombre del firmante (`ClienteAuth`, que nunca asume «sin contacto» si AUTH falla), genera el PDF con `generar_pdf` y llama a `notificar`: **con correo** sale con el PDF adjunto; **con solo teléfono**, por SMS sin PDF; **sin contacto** la póliza se emite y el aviso se omite (`aviso_omitido_motivo = SIN_CONTACTO`, migración `0006`, solo expandir y con `downgrade` probado) sin reintentar para siempre (W03-18); si el ACL rechaza la solicitud (422) tampoco se reintenta (`AVISO_RECHAZADO`). Si AUTH no responde lanza `EmisionPendiente`: la póliza ya está emitida y solo se reintenta el aviso. Nada nuevo se guarda en POLICY y nada va a los logs |
| 7 | `infra` | **Hecho (2026-10-24).** `.github/servicios.json`: `policy` llama a `auth`; `scripts/validar_servicios.py` consistente; `AUTH_URL` y la dependencia de `auth` en el compose. Terraform lo deriva solo de `llama` (la variable `AUTH_URL` de `policy` y el permiso `policy->auth`): `terraform validate` pasa y `terraform console` muestra ambos. **El `plan` completo no se pudo correr** (pide la llave de MongoDB Atlas, del equipo). Sin recursos facturables nuevos |
| 8 | `tests` | **Hecho (2026-10-24).** Pruebas unitarias en cada servicio; la colección de Newman de HU-W03 suma la carpeta 5 (29 peticiones): el titular con correo recibe el aviso **por correo con el PDF**, un cliente solo con teléfono **por SMS sin PDF**, y uno sin contacto **tiene póliza y el stub no recibe nada**; `actualizarContacto` valida, respeta los roles y no devuelve el dato; el contacto no se puede leer por GraphQL. Informe `docs/evidencias/EC022-contacto-2026-10-24.md`: en la base real de AUTH 0 filas con el contacto en claro, 0 coincidencias en 478.275 líneas de log de 20 contenedores, y 0 columnas de contacto en POLICY y PAYMENTS. Suites: AUTH 146, BFF 282, POLICY 211, ACL 175 y stub 20 (834, 0 fallos); Newman 285 aserciones, 0 fallos, 0 respuestas 5xx |

Orden sugerido: 1 y 2 (contrato y AUTH), 4 y 5 (ACL y stub), 6 y 7 (POLICY e infra), 3 y, al final, 8.

**Decisiones nuevas (confirmadas el 2026-10-24):**

| # | Tema | Decisión |
|---|---|---|
| W03-17 | Quién captura el correo y el teléfono, y dónde | `crearUsuario` (lo usa el BACKOFFICE) y una mutación para que el propio usuario los actualice, junto a idioma y moneda. Exige una pantalla en Web, que hoy no existe. Detalle abierto que no bloquea: AUTH guarda ambos (opcionales) y el adjunto viaja por correo; con solo teléfono el aviso sale sin PDF |
| W03-18 | Cliente sin contacto (hoy son todos) | La póliza se emite igual y el aviso se marca omitido con el motivo `SIN_CONTACTO`, sin reintentar para siempre; el cliente la descarga desde la web |

**Riesgos:**

- El contrato del proveedor de notificaciones es inventado (como el de firma, W03-12): `destino` y `adjuntos` cambiarán con el proveedor real.
- Enviar la prima, la cobertura y el nombre a un tercero exige un acuerdo de tratamiento de datos, que valida el equipo con legal.
- Un correo y un teléfono en AUTH amplían el dato personal que protege el cifrado en reposo, y Cloud KMS real sigue sin probarse (ver `CLAUDE.md`).
- Mientras no exista la pantalla de captura (W03-17) ningún cliente tendrá contacto y el adjunto no saldrá. Se puede construir y probar igual con el stub.

Además queda un detalle de web, ajeno al adjunto: el enlace del aviso es una ruta relativa que exige sesión (sin token, 401), así que Web debe llevarlo a una página
de «Mi póliza» que pida el PDF con el token.

**Riesgo, sin criterio detrás:** ni el BFF ni el gateway tienen CORS (el gateway sólo acepta `GET` y `POST`, no `OPTIONS`). El frontend lo evita en desarrollo con el
proxy de Vite; una web desplegada que llame al gateway desde otro origen lo necesitará.

**Documentos:** `contracts/CHANGELOG.md` (fila 3 hecha en la v0.13; filas 4 y 8 en la v0.15), `docs/plan-completar-hu-w03.md`, `docs/evidencias/` (filas 1, 4, 6, 8 y 9) y los README de `policy`, `bff` y `acl-worker`.
Las claves de Jira de estas filas están por decidir: las tareas de back de W03 ya están cerradas o en revisión.

### Bloque 9 · Firma y pago interactivos con el asesor (cambio de KAN-26; planeado el 2026-10-27, sin empezar)

El 2026-10-27 Felipe redefinió cómo firma y paga el cliente. El flujo es **interactivo**: el cliente está esperando su
póliza. Por eso **el cobro y la emisión son síncronos** y solo el correo con el PDF va en segundo plano. El cambio
reemplaza, de los bloques 2, 4 y 8:

- cómo se prueba la identidad;
- cuándo decide UNDER;
- de dónde sale el medio de pago;
- de dónde sale el contacto.

**Se mantiene:**

- D-03 (cobrar antes de emitir);
- la evidencia append-only;
- el cobro idempotente y la conciliación;
- el circuito y el timeout del ACL;
- el PDF con `fpdf2`;
- el aviso con el PDF adjunto;
- W03-13 y W03-14.

Pantallas de Figma `Solventa-web-Final` (`yKWD8jrwERtJkuSj0mk0Gz`):

| Pantalla | Nodo |
|---|---|
| W01-01 | `2001:1016` |
| W03-02 | `2001:1832` |
| W03-02b | `2020:32` |
| W03-03 | `2001:1969` |
| W03-04 | `2001:2057` |
| W03-05 | `2001:2128`; no se construye |

**Flujo nuevo:**

```
W01-01 (ASESOR)    ─► el formulario suma nombre y correo del cliente (cotizar no los guarda)
aceptarOferta      ─► POLICY: Propuesta(ACEPTADA)
                      ├─ con el contacto cifrado (si acepta un ASESOR)
                      └─ con «contacto en AUTH» (si acepta un CLIENTE)
enviarCodigo       ─► POLICY genera un código (guarda su huella; vence y tiene intentos)
                      └─ ACL /notificaciones (plantilla codigo-verificacion)
verificarCodigo    ─► el ASESOR digita en el modal el código que le dicta el cliente ─► IDENTIDAD_VERIFICADA
emitir (modal)     ─► UNDER /decisiones (KYC por ACL)
                      ├─ RECHAZADA / REVISION_MANUAL ─► NO_EMITIDA | EN_REVISION
                      └─ APROBADA ─► enlace de un solo uso, 30 min (POLICY guarda su huella)
                                     └─ ACL /notificaciones (plantilla enlace-firma) ─► EN_FIRMA
página del enlace  ─► contrato en PDF (borrador) ─► acepta términos ─► firma (trazo | imagen | nombre)
                      └─ ACL /firma/firmar ─► evidencia append-only + imagen ─► FIRMADA
                      └─ W03-02b ─► W03-03 «Pagar»
Pagar              ─► POLICY ─► PAYMENTS ─► ACL ─► stub POST /v1/sesiones-pago (vence a los 10 min)
                      └─ el navegador va a la página de pago del stub (no es Solventa)
página del stub    ─► tarjeta de crédito o débito ─► el stub tokeniza y cobra
                      └─ animación ─► «Regresar al comercio» (?sesion=…)
confirmarPago      ─► POLICY ─► PAYMENTS ─► ACL ─► stub GET /v1/sesiones-pago/{id}
                      ├─ cobrado      ─► token cifrado + POLICY emite (EMITIDA, poliza.emitida) ─► W03-04
                      │                  └─ Cloud Tasks: correo con el PDF firmado
                      ├─ rechazado    ─► sesión nueva: otra tarjeta
                      └─ falla o calla ─► se concilia; «Reintentar» (al 3.er fallo seguido: otra tarjeta)
```

**Decisiones nuevas** (2026-10-27):

| # | Tema | Decisión | Estado |
|---|---|---|---|
| W03-19 | Quién inicia el proceso | **Por ahora siempre hay un ASESOR**: él acepta la oferta, pide el código y pulsa «Emitir». El autoservicio del CLIENTE queda fuera | **Cerrada** |
| W03-20 | Identidad del firmante | **Código por correo** (POLICY lo genera y lo envía por el ACL) que **el ASESOR digita en el modal**. Detalles propuestos: 6 dígitos, se guarda su huella (HMAC), vence a los 10 min, admite 5 intentos y pedir otro anula el anterior | **Cerrada**; los detalles, Propuesta |
| W03-21 | Cuándo decide UNDER | **Al pulsar «Emitir»**, antes de enviar el enlace. Así no firma quien luego se rechaza (sigue W03-2) y la decisión no consume el plazo del pago | Propuesta |
| W03-22 | Enlace de firma | Token aleatorio de un solo uso por propuesta; POLICY guarda solo su huella. **Vence a los 30 min y cubre todo el proceso**, desde abrir el contrato hasta la póliza emitida y la descarga del PDF. Un enlace nuevo anula el anterior. Vencido, se muestra un estado vacío: el ASESOR reenvía y el cliente **retoma donde iba**. Una sesión de pago abierta antes de vencer se acepta al regresar, para no perder un cobro hecho | **Cerrada** (los 30 min y el alcance); lo demás, Propuesta |
| W03-23 | Cómo viaja el token del enlace | En el **fragmento** de la URL (`/firma#t=…`), que el navegador no envía al servidor y que no queda en los logs. La web lo lee y lo manda en una cabecera (`X-Enlace-Firma`) | Propuesta |
| W03-24 | Modos de firma | Trazo con el mouse (llega como PNG), imagen **PNG o JPG** validada o **nombre estilizado** (se guarda el modo y el texto; el PDF lo dibuja con una fuente). **POLICY valida la imagen**: tipo real por su contenido, tamaño y dimensiones máximos (propuesto: 500 KB, 1200×600) y bordes blancos o transparentes (Pillow). La imagen se guarda **cifrada en POLICY** (no existe el servicio de almacenamiento) y se estampa en el PDF final | **Cerrada** (los modos); los límites, Propuesta |
| W03-25 | Contacto según el rol | **CLIENTE:** POLICY lo lee de AUTH al usarlo, como hoy (W03-16), y no lo copia. **ASESOR:** el nombre y el correo vienen de W01-01 y se guardan **cifrados en la propuesta**. La propuesta guarda de dónde sale el contacto. El modal muestra el correo enmascarado y deja corregirlo | **Cerrada** |
| W03-26 | Retención del contacto de quien no es cliente | Sigue al consentimiento. Si el consentimiento **vence o se revoca antes de emitir**, la propuesta pasa a `CANCELADA` y se borran el nombre, el correo y la imagen de la firma; la evidencia conserva la huella, la versión y el sello. Al revocar, el BFF avisa a POLICY (como ya avisa al ACL). Al vencer, lo borra **una pasada diaria con Cloud Scheduler**. **Con la póliza emitida, el contacto se conserva por el contrato** | **Cerrada** |
| W03-27 | Registro del CLIENTE | `crearUsuario` de un CLIENTE **exige cédula y correo**. La cédula se guarda como `titular_id` (el HMAC del consentimiento) más el documento cifrado. **Los usuarios de prueba se recrean** (no hay migración de datos) | **Cerrada** |
| W03-28 | Pasarela | **El stub `pasarela` no es parte de Solventa**: simula a PayU o Stripe. Sirve **su propia página de pago** (*checkout* alojado, en su origen) con: <br>• **solo tarjeta de crédito o débito**, sin PSE, porque PSE no deja un token para el cobro recurrente; <br>• sesión de **10 min** que se puede reabrir si vence; <br>• animación de «pagando» y botón «Regresar al comercio». <br>El stub **tokeniza**. Solventa nunca ve un dato de tarjeta y **confirma el pago de servidor a servidor**, sin confiar en la redirección | **Cerrada** |
| W03-29 | Plazo de 700 ms | Solo aplica a **las dos llamadas de pago**: crear la sesión y confirmar al regresar (con la emisión). Leer, firmar y llenar la pasarela son tiempos del cliente. La pasarela entra en `PROVEEDORES_RUTA_SINCRONA` con su presupuesto (W03-13) | **Cerrada**; el presupuesto, Propuesta |
| W03-30 | Fallos del pago | **Falla la pasarela** (error, caída o sin respuesta): se ofrece «Reintentar», que **concilia antes** de cobrar. Al **3.er fallo seguido**, se pide otra tarjeta. **Rechazo:** sesión nueva con otra tarjeta, sin perder la firma. El contador lo lleva PAYMENTS por propuesta. **El reintento de fondo (`/cobros/_reintentar`) no toca estos cobros**: un pendiente abandonado solo se concilia y, si salió pagado, se emite | **Cerrada** (el comportamiento) |
| W03-31 | Pantallas | <br>• W03-02, 02b y 03 sin perfil ni migas. <br>• W03-02 muestra el contrato en PDF. <br>• W03-03 cambia «Ir al inicio» por «Pagar». <br>• W03-04 quita «Ir al inicio» y «Descargar documento» abre el PDF en una pestaña nueva. <br>• W03-05 no se construye. <br>Textos por corregir: W03-02b dice que se cobra «junto con la cuota del crédito» y que la póliza se emite al confirmar la firma; W03-03 dice que el cobro sigue «en segundo plano» y que hubo una «validación biométrica» | **Cerrada** |

**Criterios de aceptación propuestos** (cambio de KAN-26; Felipe los ajusta en Jira, porque el texto oficial vive en el `.docx` del Sprint 2):

| CA | Texto propuesto |
|---|---|
| 01 | Sin cambios |
| 02 | El cliente, desde el enlace de firma, ve el **contrato en PDF** y la autorización de cobro recurrente. Firmar solo se habilita al aceptar los términos. La firma puede ser **dibujada, una imagen PNG o JPG validada** (tipo, tamaño y fondo blanco o transparente) **o su nombre estilizado** |
| 03 | Evidencia append-only (hash, titular, actor, sello de tiempo, identificador del proveedor), más la **verificación de identidad por código** y la **imagen de la firma**, estampada en la póliza. Firma siempre el titular, desde su enlace; el asesor no firma |
| 04 | Primera prima cobrada **una sola vez** aunque el cliente reintente, regrese de la pasarela varias veces o cambie de tarjeta |
| 05 | Si la firma, el KYC o la pasarela superan su tiempo o fallan, el circuito abre, **nunca llega un 5xx** y el cliente ve qué hacer. Si **falla la pasarela** se ofrece reintentar, y al **3.er fallo seguido** se pide otra tarjeta. Un pago sin respuesta se **concilia** antes de reintentar |
| 06 | Si la pasarela **rechaza** el pago, el cliente elige otra tarjeta de crédito o débito sin perder su firma |
| 07 | Con el pago confirmado, la póliza queda `Emitida` con número y `poliza.emitida` **en la misma respuesta**. Desde el regreso de la pasarela hasta la póliza emitida: p95 ≤ 1,5 s y p99 ≤ 3 s en pico |
| 08 | El cliente abre la póliza emitida **en una pestaña nueva** desde la confirmación, y el documento **firmado** le llega por correo **en segundo plano** |
| 09 | Como hoy, más **nombre, correo, imagen de la firma y token del medio de pago** cifrados. **Solventa no recibe ningún dato de tarjeta**: se capturan en la página de la pasarela |
| 10 | Como hoy, sumando **la pasarela** |
| 11 (nuevo) | El asesor envía un **código al correo** del cliente y lo digita en el modal; solo con el código válido se habilita «Emitir». El código vence en minutos y admite pocos intentos |
| 12 (nuevo) | «Emitir» envía un **enlace de un solo uso vigente 30 min** que cubre la firma, el pago y la póliza emitida. Vencido, el cliente ve un estado vacío, el asesor puede reenviarlo y el cliente retoma donde iba. Un enlace nuevo anula el anterior |
| 13 (nuevo) | «Pagar» abre la **página de la pasarela**, externa a Solventa, donde el cliente captura su tarjeta y paga en una sesión de **10 min** que se puede reabrir. Al **regresar al comercio**, Solventa **confirma el pago con la pasarela** |
| 14 (nuevo) | El formulario W01-01 pide **nombre completo y correo** del cliente, que se usan para el código, el enlace, el documento y el aviso. Toca W01 (KAN-24); Felipe decide dónde se registra |

**Qué cambia, por servicio** (propuesto):

| # | Dónde | Cambio |
|---|---|---|
| 1 | `contracts` | **v0.17**, solo aditivo: <br>• `aceptarOferta` suma el argumento opcional `contacto: ContactoTitularInput` (nombre y correo), que el BFF exige a un ASESOR; <br>• mutaciones del ASESOR: `enviarCodigoVerificacion`, `verificarCodigo`, `emitirPropuesta` y `reenviarEnlaceFirma`; <br>• operaciones del enlace, sin sesión y con el token en `X-Enlace-Firma`: `firmaPorEnlace` (estado, documento y si venció), `firmarPorEnlace` (versión, términos, autorización de cobro, modo y firma), `iniciarPago` (devuelve la URL de la pasarela) y `confirmarPago` (por id de sesión; devuelve la póliza o qué hacer); <br>• estados nuevos de `Propuesta`: `IDENTIDAD_VERIFICADA`, `EN_FIRMA`, `FIRMADA` y `CANCELADA` (aviso al frontend: un enum de salida que crece); <br>• `CrearUsuarioInput.documentoIdentidad`. <br>Plantillas `codigo-verificacion` y `enlace-firma` en el contrato de notificaciones. W07 tomará la versión siguiente |
| 2 | `stubs/pasarela` | Página de pago propia, en HTML sencillo servido por el FastAPI del stub y en su origen: <br>• **`POST /v1/sesiones-pago`** (monto, referencia idempotente, URL de retorno; devuelve el id y la URL; vence a los 10 min); <br>• **`GET /v1/sesiones-pago/{id}`** (estado, token del medio, marca y tipo); <br>• la página, que captura la tarjeta (Luhn, fecha y CVV, solo en memoria y nunca en logs), **tokeniza** (`tok_…`), cobra, anima y vuelve con «Regresar al comercio»; <br>• tarjetas de prueba al estilo de Stripe (una aprueba, otra rechaza) y los modos de falla de siempre (`sano`, `lento`, `error`, `caido`, `rechaza`); <br>• el cobro con token (`/v1/cobros`) se mantiene para los cobros recurrentes. <br>El README dice que el stub **no es parte de Solventa** |
| 3 | `acl-worker` | El puerto de la pasarela suma `crear_sesion_pago` y `consultar_sesion_pago`, en el adaptador principal y en el alterno (EC031). La pasarela entra en la ruta síncrona con su presupuesto (W03-13) |
| 4 | `payments` | Migración `0008` (solo expandir): <br>• `sesiones_pago` (propuesta, intento, id del proveedor, estado, vencimiento y fallos seguidos); <br>• `medios_pago` (token cifrado, marca y tipo crédito o débito; **ni número ni últimos dígitos**); <br>• `cobros.origen` (`INTERACTIVO` o `RECURRENTE`), para que `_reintentar` excluya los interactivos. <br>Endpoints: `POST /sesiones-pago`, `POST /sesiones-pago/{id}/confirmar` (concilia, cuenta fallos y nunca da 5xx) y la reapertura de una sesión vencida. El cobro sigue idempotente por intento, y cada tarjeta nueva es un intento nuevo (`uuid5(propuesta, n)`) |
| 5 | `auth` | Migración `0008` (solo expandir): `usuarios.titular_id` y `documento_cifrado`. `crearUsuario` de un CLIENTE exige cédula y correo. Se recrean los usuarios de prueba. **Choca con la `0008` de M01**: la toma quien llegue primero |
| 6 | `policy` | Migración `0007` (solo expandir): <br>• en `propuestas`: `origen_contacto`, `nombre_cifrado`, `correo_cifrado` y `contacto_borrado_en`; <br>• tablas `verificaciones_identidad` (huella, vencimiento, intentos, verificada) y `enlaces_firma` (huella, vencimiento, anulado); <br>• en `evidencias_firma`: `modo_firma` y `imagen_firma_cifrada`. <br>Servicios: código, verificación, «Emitir» (UNDER + enlace), firma por enlace con la validación de la imagen, **emisión síncrona** al confirmar el pago y aviso por Cloud Tasks (`_emitir` queda solo para el aviso). El PDF estampa la firma y sale por el token del enlace. `POST /_purgar-contactos` (Cloud Scheduler) y la purga al revocar. Se quitan `contacto_de` y `nombre_de` cuando el origen es la propuesta |
| 7 | `bff` | `aceptarOferta` con el contacto según el rol (el ASESOR lo exige). Mutaciones del ASESOR. Operaciones del enlace **sin sesión**, autorizadas solo por el token. Revocar el consentimiento avisa también a POLICY. Ruta del PDF por enlace. `mensajeEstado` con los estados nuevos |
| 8 | `infra` | <br>• Rutas nuevas en Envoy y en API Gateway; <br>• la página del stub debe **llegar al navegador** (ingreso público solo para `stub-pasarela` en staging); <br>• Cloud Scheduler para la purga diaria; <br>• `policy → payments` en `.github/servicios.json`. <br>Sin recursos que cobren por hora |
| 9 | `tests` | Unitarias por servicio; carpeta 6 de Newman (código, enlace vencido, tres modos de firma, imagen inválida, pago aprobado, rechazado, 3 fallos y pago sin respuesta conciliado); k6 de EC005 y EC007 medido desde el regreso de la pasarela; EC022 del contacto, la imagen y el token |

**Front (`solventa-frontend-web`), para su propio plan:**

- W01-01 suma nombre y correo.
- Modal del ASESOR: código, «Emitir» y reenvío.
- Página del enlace: W03-02 con el PDF, los tres modos de firma y el estado vacío; luego W03-02b, W03-03 con «Pagar» y W03-04.
- Las correcciones de W03-31.

**Orden sugerido:**

1. Contrato.
2. Stub de la pasarela y ACL.
3. PAYMENTS.
4. AUTH.
5. POLICY.
6. BFF.
7. Infra.
8. Pruebas.

**Riesgos:**

- **El pago cruza cinco saltos** (gateway, BFF, POLICY, PAYMENTS y ACL) hasta la pasarela, y después emite. Hay que medir si cabe en 700 ms. Si no cabe, se baja el presupuesto de la pasarela o BFF llama a PAYMENTS directo.
- **El token del enlace autoriza sin sesión.** Si se filtra, se puede firmar o pagar mientras esté vigente. Lo acotan el código previo, el vencimiento de 30 min y que un enlace nuevo anula el anterior.
- **La imagen de la firma es un dato personal** que no existía. Va cifrada y entra en EC022.
- **Exponer el stub al navegador en staging** es una superficie nueva, aunque sin datos reales.
- **CA-W03-05, 07 y 08 cambian de texto** y EC005 se mide desde otro punto. Hay que alinearlo con el equipo.

**Fuera de este bloque:**

- PSE;
- los cobros recurrentes con el token guardado;
- el autoservicio del CLIENTE;
- los huecos de RISK: el perfil no vence y el batch no revisa el consentimiento. Se abren aparte.

## Cambios en despliegue, infraestructura y pipelines

| Dónde | Cambio | Bloque | Costo |
|---|---|---|---|
| Cloud Run | `policy`, `under` y los stubs nuevos, mínimo 0 instancias | 1 a 5 | Casi nulo |
| Cloud SQL | Base `policy` en la **misma instancia** | 1 | Sin instancia nueva |
| Pub/Sub, Cloud Tasks | 2 temas y la cola `emision-polizas` | 4 | Centavos al mes |

Terraform se aplica desde el pipeline al hacer merge a `main` (ADR-09); nadie lo aplica a mano.

## Documentos que actualiza cada bloque

| Bloque | Documentos |
|---|---|
| 0 | Este plan, `contracts/CHANGELOG.md`, `docs/sprint-1.md` (D-03), `docs/cobros-conciliacion.md` §10 |
| 1 a 5 | `docs/arquitectura-backend.md` (tabla de servicios, puertos del ACL, flujo de emisión) y el README de cada servicio |
| 6 | `docs/evidencias/`, `docs/observabilidad.md` (tablero) |
| Al terminar | `CLAUDE.md` → **Estado**; pasar KAN-209, 211, 212, 213, 215 y 216 en Jira |

## Estado de implementación (2026-10-22, rama `feat/KAN-26-hu-w03-bloque-0-contratos`)

| Bloque | Estado | Detalle |
|---|---|---|
| 0 · Contratos y plan | **En curso** | Contrato v0.12, evento y esqueleto del BFF listos y con pruebas en verde; falta el PR |
| 1 · POLICY y oferta aceptada | **Hecho en código (2026-10-22), rama `feat/KAN-209-hu-w03-bloque-1-policy`** | POLICY, RATING y BFF con pruebas en verde (policy 23, rating 85, bff 197). Migración probada en Postgres real (upgrade, downgrade, upgrade) y el servicio en vivo contra Postgres con un RATING simulado: 8 aceptaciones simultáneas dan una fila, la prima queda cifrada y los logs no llevan cliente ni prima. **Sin verificar:** el compose completo (Docker no estaba corriendo), el PR y el CI. `policy` no es instancia fija (mínimo 0): decidir en el bloque 6 si se fija para medir EC005/EC007 |
| 2 · Firma electrónica | **Hecho en código (2026-10-22), rama `feat/KAN-211-hu-w03-bloque-2-firma`** | ACL (130 pruebas), `stubs/firma` (12), POLICY (47) y BFF (213) en verde. Migración `0002` y triggers append-only probados en Postgres real. En vivo con el ACL y el stub reales: camino feliz, idempotencia, proveedor lento (corte a ~719 ms, reintento con la misma referencia), caído (circuito abre al 3.er intento, falla en ~10 ms) y recuperación por la sonda; los logs no llevan firmante ni prima. **Sin verificar:** el compose completo, el PR y el CI. `stub-firma` no es instancia fija: su memoria y su modo se pierden si Cloud Run recicla la instancia (decidirlo en el bloque 6) |
| 3 · UNDER y KYC | **Hecho en código (2026-10-22), rama `feat/KAN-213-hu-w03-bloque-3-under`** | ACL (137 pruebas), `stubs/kyc` (12), UNDER (35) y POLICY (48) en verde. En vivo con el ACL y el stub reales: aprobada, riesgo alto/desconocido y cobertura sobre el tope van a revisión, KYC rechazado rechaza, KYC lento (corte a ~740 ms) y caído (circuito abre al 3.er intento, ~17 ms) van a revisión con 200, y la sonda lo cierra sola. **Sin verificar:** el compose completo, el PR y el CI. Los umbrales de las reglas son supuestos, no reglas validadas. **Para el bloque 4:** POLICY debe guardar el `nivelRiesgo` de la oferta en la propuesta (hoy solo guarda tipo, prima, cobertura y plazo) para pasárselo a UNDER; `policy` ya figura en `servicios.json` llamando a `under` |
| 4 · Cobro y emisión | **Hecho en código (2026-10-22), rama `feat/KAN-212-hu-w03-bloque-4-emision`** | POLICY (109 pruebas), BFF (220), ACL (137), UNDER (35) y PAYMENTS (93) en verde. Migración `0003` y el recorrido completo probados en vivo con Postgres real y los servicios reales (POLICY, UNDER, PAYMENTS, ACL Worker y los tres stubs): firma a póliza en ~210-435 ms con los stubs sanos; idempotente (firmar de nuevo y 8 `_emitir` simultáneos dejan 1 póliza y 1 cobro); KYC rechazado → `NO_EMITIDA` sin cobro; riesgo alto → `EN_REVISION` sin cobro; pasarela que rechaza → `PAGO_RECHAZADO` sin póliza; pasarela lenta → `PENDIENTE_PAGO` y, al conciliarse el cobro, `EMITIDA` con un solo cobro; UNDER caído → la propuesta espera y se emite al volver. **Sin verificar:** el compose completo, Cloud Tasks real (probado con un doble de la sesión HTTP y con el planificador local), `terraform plan`, el PR y el CI. Un fallo del caso «UNDER caído» en la primera corrida no se pudo explicar y no se repitió en tres corridas más. Los reintentos de un cobro rechazado (W05) no reabren la póliza todavía |
| 5 · PDF y notificación | **Hecho en código (2026-10-22), rama `feat/KAN-215-hu-w03-bloque-5-pdf-notificacion`** | POLICY (145 pruebas), BFF (232), ACL (143) y `stubs/notificaciones` (12) en verde. En vivo con **AUTH, BFF, POLICY, UNDER, PAYMENTS, ACL y los cuatro stubs reales** y Postgres real, 27 comprobaciones por el BFF: aceptar, firmar, esperar `EMITIDA` (~300 ms con los stubs sanos), `poliza(id)`, descarga del PDF (con el nombre real del asegurado tomado de AUTH), un solo aviso con la plantilla, el id opaco y el enlace; otro CLIENTE no ve ni descarga (404), sin token 401, BACKOFFICE 403, ASESOR 200; con las notificaciones caídas la póliza se emite igual, el PDF se descarga y el aviso sale solo al volver (~2 s) con un solo aviso por póliza; riesgo alto no emite ni avisa; ni el nombre, ni la clave, ni el cliente opaco, ni la prima en ningún log. **Sin verificar:** el compose completo y Envoy (Docker apagado; el CI levanta el gateway), la sintaxis `path_translation` de GCP API Gateway (no hay un gateway real), el PR y el CI |
| 6 · Pruebas y evidencia | **Hecho en local (2026-10-24), rama `feat/KAN-216-hu-w03-bloque-6-pruebas-evidencia`; falta staging** | k6 `ec005-emision.js` y `ec007-emision-pico.js`, colección Newman `hu-w03.postman_collection.json` (78 peticiones por el gateway) y tablero `hu-w03.json`. **Medido en local con el compose** (informes en `docs/evidencias/`): EC005 p95 370 ms con 301 emisiones, todas `EMITIDA` (umbral 1,5 s); EC007 p99 1.042 ms con 479 emisiones, todas `EMITIDA`, máximo 2.558 ms sin causa identificada (umbral 3 s); 0 % de 5xx; Newman dos veces seguidas sin fallos (153 aserciones). **Supuestos:** la carga (1 firma por segundo, 3× en pico) no está definida en el repo; local emite en línea, sin Cloud Tasks ni arranques en frío, así que es un piso optimista. **Hallazgos:** con la firma caída el primer intento por el gateway da 504 (corte de 700 ms); el BACKOFFICE recibe 403 al pedir el PDF (decisión del bloque 5; el CA-W03-08 oficial sólo habla del cliente, sin conflicto). **Ya estaban** las pruebas de contrato de `poliza.emitida` y del esquema, y la de reemplazo de adaptador de firma (EC031). **Falta:** la corrida en staging, confirmar la carga oficial y decidir si `policy`, `under` y los stubs de firma, KYC y notificaciones pasan a `instancia_fija` (cobra por hora) |
| 7 · Brechas contra los criterios oficiales | **Hecho en código y en local (2026-10-24); falta una decisión y staging** | Hechas las filas 1 (PR #280), 2 y 3 (PR #281), 5 (PR #282, con su Cloud Scheduler), 6 (PR #283), 4 y 8 (contrato v0.15). Decididas: W03-13 y W03-14. **Decidida:** la fila 7 (W03-16: el aviso llevará el PDF, bloque 8 planeado). **Por ajustar:** los textos de W03-15, que son una propuesta. **Parcial:** la fila 9 (lo que no necesita staging está hecho; Cloud Tasks real, el `plan` completo y EC005/EC007 esperan una liberación a `main`). Ver el bloque 7 |
| 8 · PDF adjunto al aviso | **Hecho en código y en local (2026-10-24); falta staging** | W03-16 a W03-18 cerradas y las ocho filas hechas: contrato v0.16, AUTH (`0007`), BFF, ACL, stub, POLICY (`0006`), infra y pruebas. **Falta:** una liberación a `main` para verlo en staging, el `plan` completo de Terraform (pide la llave de MongoDB Atlas), Cloud KMS real y una pantalla en Web para que el cliente deje su contacto (W03-17): **mientras no exista, ningún cliente tiene contacto y su aviso se omite**. Detalle abierto que no bloquea: con correo y teléfono el aviso va por correo. Ver el bloque 8 |
| 9 · Firma y pago interactivos | **Planeado (2026-10-27), sin empezar**; rama `feat/KAN-26-firma-y-pago-interactivos` | Decisiones W03-19 a W03-31 y los CA propuestos en el bloque 9. Reemplaza el cobro en segundo plano y el token por configuración |

## Decisiones abiertas

- Ligar al usuario con el titular de la oferta: hoy un CLIENTE solo acepta lo que él mismo consintió (si el consentimiento guarda su actor), pero AUTH no sabe de qué documento es titular el usuario. Mientras tanto `Query.oferta` queda en `null`.
- Confirmar W03-1, 3 a 6 y 8 a 12 y los CA propuestos. W03-7 (Pub/Sub) quedó sin ejecutar: si el equipo quiere eventos reales, ampliar W05-8.
- Qué pasa con una propuesta en `PAGO_RECHAZADO` o `PENDIENTE_PAGO` agotado: hoy quedan así y alguien del back-office debe resolverlas; no hay pantalla ni tarea que las reabra.
- ~~De dónde sale realmente el `tokenMedioPago`~~: resuelto en el bloque 9 (W03-28). Lo genera la pasarela al capturar la tarjeta.
- ~~Diseñar en Figma la pantalla de resultado del pago~~: resuelto en el bloque 9. La muestra la página de la pasarela y luego W03-04.
- Bloque 9: los detalles propuestos (código, límites de la imagen, presupuesto de la pasarela) y dónde se registra el CA-W03-14 (W01 o W03).
