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
| W03-3 | Primer cobro pendiente o rechazado | Pendiente: la póliza espera (`PENDIENTE_PAGO`) y se emite sola al pasar a cobrado. Rechazado: no hay póliza (`PAGO_RECHAZADO`); el reintento manual de W05 la reabre | Propuesta |
| W03-4 | Quién llama a PAYMENTS | POLICY, de servicio a servicio (`policy` entra en el `llama` de `payments`). `crearCobro` del BFF sigue siendo solo de BACKOFFICE | Propuesta |
| W03-5 | `tokenMedioPago` | Token opaco del convenio de recaudo con el banco del crédito, por configuración (`TOKEN_RECAUDO_CREDITO`, marcado REWIRE). No hay captura de tarjeta | Propuesta |
| W03-6 | Asincronía en Cloud Run | La emisión se agenda con Cloud Tasks (copia de `PlanificadorCloudTasks` de PAYMENTS); en local corre inline. Nada de bucles de fondo | Propuesta |
| W03-7 | Aviso del cobro a POLICY | **Cambiada en el bloque 4: no se usa Pub/Sub.** La propuesta original (PAYMENTS publica `cobro.estado-cambiado` por Pub/Sub y POLICY lo recibe por push) ampliaba la decisión cerrada W05-8, que solo aprobó Pub/Sub para `perfil.actualizado`, y no hubo visto bueno del equipo. En su lugar, cuando el primer cobro queda pendiente POLICY lo vuelve a consultar con una tarea de Cloud Tasks, re-enviando a PAYMENTS la misma orden idempotente (devuelve el cobro existente sin cobrar de nuevo), y `poliza.emitida` va al bus de log, igual que `cobro.estado-cambiado`. Si el equipo quiere eventos reales, es una decisión aparte | Cambiada |
| W03-8 | Quién firma | Solo el CLIENTE titular firma (no repudio). El ASESOR puede aceptar la oferta pero no firmar | Propuesta |
| W03-9 | PDF | Se genera al pedirlo con `fpdf2` a partir de los datos de la póliza. Sin GCS ni costo nuevo | Propuesta |
| W03-10 | KYC | Puerto `PuertoKyc` en el ACL con un stub que simula Truora. El consolidador de KYC real queda para el Sprint 3 | Propuesta |
| W03-11 | Servicios | **Dos servicios nuevos**: `under` (sin almacén, decide) y `policy` (PostgreSQL, escritor único de pólizas) | **Cerrada** |
| W03-12 | Proveedor de firma | Solventa **no** construye un servicio de firma electrónica: lo contrata a un tercero detrás del ACL. `stubs/firma` simula solo lo que es de Solventa (idempotencia por referencia, hash, modos de falla) y **no** la identidad del firmante, el certificado ni el sello de tiempo, que son del proveedor. Su contrato (`referencia_comercio`, `documento_hash`, `SIGNED`) es inventado, no el de un proveedor real. Con uno elegido, el puerto y `evidencias_firma` ganarán campos opcionales (referencia del certificado, sello de tiempo, enlace al documento firmado) en una migración aditiva. Qué tipo de firma hace exigible la póliza lo valida el equipo con legal | Propuesta |
| W03-13 | Firma dentro del plazo del gateway (CA-W03-05) | Presupuesto propio de la firma en el ACL, **configurable y sin valores fijos en el código**: `PLAZO_RUTA_SINCRONA_MS − RESERVA_SALTOS_MS` (700 − 250 = 450 ms) para los proveedores de `PROVEEDORES_RUTA_SINCRONA`, y presupuestos por proveedor leídos como `PRESUPUESTO_MS_<PROVEEDOR>`. Hecho el 2026-10-24 (`docs/evidencias/CA-W03-05-firma-2026-10-24.md`) | **Cerrada** |
| W03-14 | Quién reabre una póliza con cobro rechazado o pendiente agotado (CA-W03-06) | **Solo el back-office y los procesos de reconciliación**, no el cliente. La reapertura es idempotente. Hecho en código y en Terraform el 2026-10-24 (`docs/evidencias/CA-W03-06-reapertura-2026-10-24.md`) | **Cerrada** |
| W03-15 | Textos de «qué hacer» y del aviso (CA-W03-06 y 08) | Revisado en Figma `Solventa-web-Final` el 2026-10-24: la pantalla «Resultado del pago» **no existe** (hay que crearla, D-03) y W03-04 dice que el documento «llega por correo o notificación». Los textos que ya sirve el BFF (`mensajeEstado`, tabla en `servicio_suscripcion.py`) son una **propuesta** para ajustar con UX; cambiarlos es editar una tabla y su traducción | Propuesta |
| W03-16 | Adjuntar el PDF al aviso de póliza emitida (CA-W03-08) | **Decidido (2026-10-24): el aviso llevará el PDF, en un bloque nuevo (8), por planear.** La recomendación del análisis era dejar solo el enlace, pero se eligió adjuntarlo. Adjuntarlo exige tres cosas que hoy no existen y que cambian el diseño (ver el bloque 8): el contacto del cliente, la regla del puerto de que el proveedor no recibe el documento, y el nombre del asegurado. Mientras tanto el aviso sigue llevando el enlace | **Decidida; diseño por planear** |

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

### Bloque 8 · Adjuntar el PDF al aviso de póliza emitida (por planear)

Decidido el 2026-10-24 (W03-16, CA-W03-08: «el documento se le envía por notificación»). **Sin empezar.** Antes de construirlo hay que resolver tres cosas, porque
cada una cambia el diseño de hoy; se anotan aquí y recién entonces se parte en filas:

| # | Qué falta decidir | Por qué importa | Hoy |
|---|---|---|---|
| 1 | **A quién se envía.** ¿AUTH guarda un correo o teléfono del cliente (dato personal nuevo, cifrado en reposo, con su migración) o el proveedor de notificaciones lo resuelve con su propia base? | Sin un destino, adjuntar no sirve. Un correo en AUTH amplía lo que protegen los EC022 y la regla #6 | AUTH no guarda correo ni teléfono y su alias no puede ser un correo; el aviso va al id opaco y el proveedor resuelve el contacto |
| 2 | **La regla del puerto.** Hoy el proveedor «nunca recibe el contenido del documento ni un dato personal». Adjuntar el PDF le entrega prima, cobertura y, quizá, el nombre | Es una decisión de seguridad, no de código: hay que cambiar la regla de forma explícita (y revisar el contrato del proveedor real) | `PuertoNotificaciones` y su adaptador envían solo plantilla, id opaco y datos de la plantilla |
| 3 | **El nombre del asegurado.** ¿El PDF adjunto sale sin nombre, o POLICY se lo pide a AUTH al emitir? | El PDF de hoy lleva el nombre solo al descargarlo (viene en el cuerpo de la petición): POLICY no lo tiene, y guardarlo rompe esa decisión (W03-9) | `POST /polizas/{id}/documento` recibe el nombre en el cuerpo |

Además queda un detalle de web, ajeno al adjunto: el enlace del aviso es una ruta relativa que exige sesión (sin token, 401), así que Web debe llevarlo a una página
de «Mi póliza» que pida el PDF con el token.

**Riesgo, sin criterio detrás:** ni el BFF ni el gateway tienen CORS (el gateway sólo acepta `GET` y `POST`, no `OPTIONS`). El frontend lo evita en desarrollo con el
proxy de Vite; una web desplegada que llame al gateway desde otro origen lo necesitará.

**Documentos:** `contracts/CHANGELOG.md` (fila 3 hecha en la v0.13; filas 4 y 8 en la v0.15), `docs/plan-completar-hu-w03.md`, `docs/evidencias/` (filas 1, 4, 6, 8 y 9) y los README de `policy`, `bff` y `acl-worker`.
Las claves de Jira de estas filas están por decidir: las tareas de back de W03 ya están cerradas o en revisión.

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
| 7 · Brechas contra los criterios oficiales | **Hecho en código y en local (2026-10-24); falta una decisión y staging** | Hechas las filas 1 (PR #280), 2 y 3 (PR #281), 5 (PR #282, con su Cloud Scheduler), 6 (PR #283), 4 y 8 (contrato v0.15). Decididas: W03-13 y W03-14. **Decidida:** la fila 7 (W03-16: el aviso llevará el PDF, bloque 8 por planear). **Por ajustar:** los textos de W03-15, que son una propuesta. **Parcial:** la fila 9 (lo que no necesita staging está hecho; Cloud Tasks real, el `plan` completo y EC005/EC007 esperan una liberación a `main`). Ver el bloque 7 |
| 8 · PDF adjunto al aviso | **Por planear (decidido el 2026-10-24)** | W03-16: el aviso llevará el PDF. Sin empezar: faltan tres decisiones de diseño (contacto del cliente, regla del puerto y nombre del asegurado). Ver el bloque 8 |

## Decisiones abiertas

- Ligar al usuario con el titular de la oferta: hoy un CLIENTE solo acepta lo que él mismo consintió (si el consentimiento guarda su actor), pero AUTH no sabe de qué documento es titular el usuario. Mientras tanto `Query.oferta` queda en `null`.
- Confirmar W03-1, 3 a 6 y 8 a 12 y los CA propuestos. W03-7 (Pub/Sub) quedó sin ejecutar: si el equipo quiere eventos reales, ampliar W05-8.
- Qué pasa con una propuesta en `PAGO_RECHAZADO` o `PENDIENTE_PAGO` agotado: hoy quedan así y alguien del back-office debe resolverlas; no hay pantalla ni tarea que las reabra.
- De dónde sale realmente el `tokenMedioPago` cuando la prima va con la cuota del crédito (convenio de recaudo): hoy es una
  configuración simulada.
- Diseñar en Figma la pantalla de resultado del pago (cobrado, en proceso, rechazado): el prototipo no la tiene.
