# Plan de HU-W07 y de los ajustes que la preceden

> **Estado: borrador (2026-10-27), sin aprobar ni empezar.** Ordena en un solo lugar lo que salió de revisar HU-W07
> (KAN-30) contra `develop`. Antes de W07 hay que ajustar las historias que reutiliza, porque Felipe redefinió la firma y
> el cobro de W03 (2026-10-26/27).
>
> Rama: `feat/KAN-26-firma-y-pago-interactivos`.
>
> El detalle de cada parte vive en su plan:
> - **W03:** el bloque 9 de [`plan-completar-hu-w03.md`](plan-completar-hu-w03.md), con las decisiones W03-19 a W03-31 y los CA propuestos.
> - **W07:** la v2 de `plan-completar-hu-w07.md`, en la rama `feat/KAN-30-socios-api`, que este documento actualiza.
>
> Las decisiones marcadas **Propuesta** esperan el visto bueno de Felipe.

**Por qué este orden.** El socio de W07 revende la póliza: cotiza (W01), acepta, firma y paga (W03) y cobra (W05). En la
v2 de W07, el socio firmaba con un enlace propio y el cobro iba por convenio de recaudo. Ahora W03 tendrá **un enlace de
firma, una pasarela con página propia y cobro síncrono** para el asesor. El socio hace el papel del asesor por la API,
así que W07 reutiliza todo eso en vez de construir su propia firma.

Figma `Solventa-web-Final` (`yKWD8jrwERtJkuSj0mk0Gz`):

| Grupo | Pantallas (nodo) |
|---|---|
| W01 | W01-01 `2001:1016` |
| W03 | W03-02 `2001:1832`, W03-02b `2020:32`, W03-03 `2001:1969`, W03-04 `2001:2057` y W03-05 `2001:2128` (W03-05 no se construye) |
| W07 | W07-01 Login `2001:3340`, W07-02 Dashboard `2001:3562` y W07-03 Documentación y playground `2001:3434` |

---

## 1. Ajustes por historia

### 1.1 HU-W01 · Cotización (KAN-24)

| # | Ajuste | Decisión | Estado |
|---|---|---|---|
| W01-A1 | El formulario W01-01 pide **nombre completo y correo** del cliente | Cotizar **no los guarda**, porque RATING no los usa. Viajan en `aceptarOferta` y POLICY los guarda cifrados en la propuesta solo si acepta un ASESOR (W03-25). **Es un criterio nuevo de W01 (KAN-24)**, no de W03, porque el formulario es de W01 (decidido el 2026-10-27) | **Cerrada** |
| W01-A2 | Revocar el consentimiento avisa a todos los que guardan datos del titular | Hoy el BFF solo avisa al ACL (`/fuentes/invalidar-cache`). Pasa a avisar también a **POLICY** (borra el contacto de las propuestas abiertas, W03-26) y a **RISK** (W01-A3). Son llamadas directas desde el BFF, como en el bloque 7 de W01: el evento `consentimiento.revocado` exigiría ampliar W05-8 (Pub/Sub solo para `perfil.actualizado`). Si un aviso falla, la revocación no falla: queda en el log y lo recoge la pasada diaria de W01-A4 | Propuesta |
| W01-A3 | **Hueco de RISK: el perfil no se borra al revocar** | El perfil de MongoDB guarda `consentimiento_id` y `consentimiento_vence_en` al crearse (`POST /perfiles` pasa a recibirlos). `POST /perfiles/consentimiento-revocado` (`titular_id`) borra el perfil y su caché del score en Redis. Las pólizas emitidas no se afectan, porque la propuesta ya copió el `nivel_riesgo` | Propuesta |
| W01-A4 | **Hueco de RISK: el perfil no vence** | Una pasada diaria (Cloud Scheduler hacia `POST /perfiles/_purgar`) borra los perfiles cuyo consentimiento venció. Los perfiles anteriores al cambio, sin `consentimiento_vence_en`, se tratan como vencidos a los 30 días de su `operation_time` | Propuesta |
| W01-A5 | **Hueco de RISK: el batch nocturno no revisa el consentimiento** | `batch_reproceso.py` omite los perfiles con el consentimiento vencido y los deja para la purga. No pregunta a AUTH perfil por perfil, porque frenaría EC017: se basa en la fecha guardada y en el aviso de revocación | Propuesta |
| W01-A6 | La oferta de Redis de RATING sigue viva al revocar (24 h) | Se deja como está. La oferta no guarda datos de las fuentes, solo el precio y el nivel de riesgo, y aceptar ya exige un consentimiento vigente. Si se quiere borrar al revocar, RATING necesitaría un índice por titular | Propuesta |

> Felipe dijo que indicaría cómo debe quedar el plan de RISK. W01-A3 a W01-A6 son la propuesta para que la ajuste.

### 1.2 HU-W03 · Firma, cobro y emisión (KAN-26)

Es el bloque 9 de `plan-completar-hu-w03.md` (W03-19 a W03-31 y los CA-W03-01 a 13 propuestos), sin la pasarela, que va
en la sección 2. En resumen:

| # | Ajuste | Dónde |
|---|---|---|
| W03-B1 | Contacto según el rol: un CLIENTE lo toma de AUTH y un ASESOR lo da en W01-01, cifrado en la propuesta. Se borra si el consentimiento vence o se revoca antes de emitir, y con la póliza emitida se conserva por el contrato | POLICY, BFF |
| W03-B2 | **Código de verificación por correo** que el ASESOR digita en el modal | POLICY, ACL (plantilla), BFF |
| W03-B3 | «Emitir» del ASESOR: **UNDER y KYC deciden ahí**; si aprueba, se envía un **enlace de un solo uso de 30 min** que cubre todo el proceso | POLICY, BFF |
| W03-B4 | Página del enlace: **contrato en PDF**, términos y **firma en tres modos** (trazo, imagen PNG o JPG validada, nombre estilizado). La imagen va cifrada en POLICY y se estampa en el PDF | POLICY, BFF |
| W03-B5 | **Cobro y emisión síncronos** al regresar de la pasarela; solo el correo con el PDF va por Cloud Tasks | POLICY, PAYMENTS |
| W03-B6 | Fallos: reintentar con conciliación, otra tarjeta al 3.er fallo seguido o con un rechazo | POLICY, PAYMENTS, BFF |
| W03-B7 | **Registro del CLIENTE con cédula y correo obligatorios.** Los usuarios de prueba se recrean | AUTH (migración `0008`), BFF |
| W03-B8 | EC005 y EC007 se vuelven a medir, ahora desde el regreso de la pasarela hasta la póliza emitida | Pruebas |

**Decisiones de W03 que cambian:** W03-3, 5, 6, 12, 15, 16 (en parte), 17 y 18. W03-8 se mantiene. Cada una está marcada
en la tabla del plan de W03.

### 1.3 HU-W05 · Cobros (KAN-28)

| # | Ajuste | Decisión | Estado |
|---|---|---|---|
| W05-A1 | **Sesiones de pago** en PAYMENTS | Tabla `sesiones_pago` con propuesta, intento, id del proveedor, estado, vencimiento y fallos seguidos. Endpoints `POST /sesiones-pago`, `POST /sesiones-pago/{id}/confirmar` y abrir otra si venció. Nunca un 5xx | **Cerrada** (W03-28 a 30) |
| W05-A2 | **Medios de pago** | Tabla `medios_pago`: el token del proveedor cifrado (W05-5), la marca y el tipo, crédito o débito. **Ni el número ni los últimos dígitos.** Queda listo para los cobros recurrentes, que siguen fuera de alcance | **Cerrada** |
| W05-A3 | El reintento de fondo no toca los cobros interactivos | `cobros.origen` (`INTERACTIVO` o `RECURRENTE`). `/cobros/_reintentar` excluye los interactivos: un pendiente abandonado solo se concilia | **Cerrada** (W03-30) |
| W05-A4 | **CA-W05-03 queda cubierto** | «Captura de tarjeta con componente tokenizado del proveedor» no se había construido (`docs/cobros-conciliacion.md`). Con la página de la pasarela de la sección 2 queda hecho, y hay que actualizar ese documento y su nota en Jira | — |
| W05-A5 | Tabla de conciliación del BACKOFFICE | Muestra el origen del cobro y la marca y el tipo del medio. Es aditivo en `Cobro` | Propuesta |

Migración de PAYMENTS: `0008`, solo expandir.

### 1.4 Coordinación con otros planes en pausa

| Plan | Qué choca | Cómo se resuelve |
|---|---|---|
| HU-M01 (`feat/KAN-31-onboarding-movil`) | También pide la migración `0008` de AUTH | La toma el primero que llegue a `develop`; el otro renumera. Con este orden, W03 toma la `0008` y W07 la `0009` |
| Almacenamiento (`feat/almacenamiento-archivos`) | La imagen de la firma podría vivir allí | No existe todavía, así que la imagen va cifrada en POLICY (W03-24). Si el servicio llega después, se puede mover |
| Contrato | W03 y W07 piden la siguiente versión libre | **W03 toma v0.17 y W07 v0.18**, más el archivo nuevo `contracts/openapi/socios-v1.yaml` |

---

## 2. Stub de la pasarela de pagos completo

> **El stub no es parte de Solventa.** Vive en este repositorio (`stubs/pasarela`) para mostrar cómo sería integrar
> una pasarela real como PayU o Stripe. Tiene **su propia página de pago**, servida en su origen y con su puerto (8103).
> Solventa nunca ve un dato de tarjeta: **el stub captura, tokeniza y cobra**, y Solventa solo recibe un token y el
> resultado, que confirma de servidor a servidor (regla 6, CA-W03-09).

### 2.1 Lo que ya tiene y se mantiene

- `POST /v1/cobros` y `GET /v1/cobros/{referencia_comercio}`: el cobro con token, idempotente por referencia. Sirve para
  la conciliación y para los cobros recurrentes futuros.
- `/alterna/v2/charges…`: el segundo proveedor, para EC031.
- `GET /v1/estado`: la sonda del circuito.
- `/control/modo`: los modos `sano`, `lento`, `error`, `caido` y `rechaza`.
- La memoria en el proceso.

### 2.2 Lo nuevo

**API de servidor a servidor** (la llama el ACL Worker, nunca el navegador):

| Endpoint | Qué hace |
|---|---|
| `POST /v1/sesiones-pago` | Recibe monto, moneda, `referencia_comercio` (idempotente: la misma referencia devuelve la misma sesión), descripción y `url_retorno`. Devuelve `sesion_id`, `url_pago` y `vence_en`. **Vence a los 10 min** (`SESION_VIGENCIA_SEGUNDOS=600`). Obedece el modo simulado |
| `GET /v1/sesiones-pago/{id}` | Estado: `ABIERTA`, `PROCESANDO`, `COBRADA`, `RECHAZADA` (con motivo), `FALLIDA` o `VENCIDA`. Con `COBRADA` devuelve además el **token** del medio (`tok_…`), la marca, el tipo y la referencia del cobro. Obedece el modo simulado |
| `POST /alterna/v2/checkout-sessions` y `GET /alterna/v2/checkout-sessions/{id}` | Lo mismo con los nombres del segundo proveedor (EC031) |

**La página de pago** (la ve el navegador del cliente):

| Ruta | Qué hace |
|---|---|
| `GET /checkout/{sesion_id}` | Página con: <br>• el resumen del pago (comercio «Solventa», descripción y monto); <br>• **el selector crédito o débito**; <br>• el formulario de la tarjeta (número, nombre, vencimiento y CVV); <br>• la cuenta regresiva de la sesión. <br>Con la sesión vencida muestra «La sesión venció» y el botón **«Regresar al comercio»** |
| `POST /checkout/{sesion_id}/pagar` | Lo envía la propia página. Valida (Luhn, vencimiento futuro, CVV de 3 o 4 dígitos), **tokeniza**, cobra según la tarjeta de prueba y el modo, y responde con la pantalla de resultado |
| Pantalla «Pagando…» | Animación de 2 a 3 s, en CSS y sin librerías, mientras se procesa |
| Pantalla de resultado | Aprobado, rechazado (con el motivo en lenguaje claro) o error, siempre con **«Regresar al comercio»**, que lleva a `url_retorno?sesion=<id>`. **El estado no viaja en la URL**: Solventa lo pregunta |

**Tarjetas de prueba** (al estilo de Stripe; la tabla se muestra en la propia página):

| Número | Tipo | Resultado |
|---|---|---|
| `4242 4242 4242 4242` | Crédito, Visa | Aprobado |
| `5555 5555 5555 4444` | Débito, Mastercard | Aprobado |
| `4000 0000 0000 0002` | Crédito | Rechazado: fondos insuficientes |
| `4000 0000 0000 0069` | Crédito | Rechazado: tarjeta vencida |
| `4000 0000 0000 0119` | Crédito | Error de procesamiento (la pasarela falla) |

**Reglas del stub:**

- **Ningún dato de tarjeta sale del stub ni se escribe en un log.** Se guarda en memoria solo el token y, asociados a
  él, la marca, el tipo y los últimos 4 dígitos para su propia pantalla. A Solventa le entrega solo el token, la marca y
  el tipo.
- **`url_retorno` contra una lista blanca** (`URLS_RETORNO_PERMITIDAS`), para que la página no sirva de redirección
  abierta.
- **HTML y JS propios, servidos por el stub, sin CDN.** Lleva una CSP estricta e idioma ES o EN según el parámetro
  `idioma`.
- **Reabrir:** una sesión vencida no se reabre. Solventa pide otra para la misma propuesta (W03-28), como haría un
  comercio con una pasarela real.
- **Los modos de falla** se aplican al crear la sesión, al consultarla y al pagar. Con `lento`, el cobro se hace antes
  de esperar, como hoy, para que la conciliación tenga qué detectar.
- **El README dice** que el stub no es parte de Solventa y lista las tarjetas de prueba.

### 2.3 Lo que cambia en el ACL Worker

- El puerto de la pasarela suma `crear_sesion_pago` y `consultar_sesion_pago`, en el adaptador principal y en el alterno.
- Mantiene el circuito y la sonda por `/v1/estado`.
- La pasarela entra en `PROVEEDORES_RUTA_SINCRONA` con su presupuesto propio (W03-13 y W03-29).

### 2.4 Infraestructura del stub

- **La página debe llegar al navegador.** En staging, `stub-pasarela` necesita ingreso público y su URL pública en la
  configuración (`PASARELA_URL_PUBLICA`). En local es `http://localhost:8103`.
- **Con mínimo 0 instancias, Cloud Run puede reciclar el stub** y perder sus sesiones. Solventa lo ve como una sesión
  inexistente y abre otra. Si molesta en las demostraciones, se pasa a instancia fija, que cobra por hora (decisión del
  bloque 6 de W03).

### 2.5 Pruebas del stub

- **Unitarias:** Luhn, vencimiento, cada tarjeta de prueba, idempotencia por referencia, vencimiento de la sesión, lista
  blanca de retorno, modos de falla y que ningún dato de tarjeta aparezca en los logs.
- **Contrato del ACL:** principal y alterno (EC031).
- **Cypress, en el front:** abre la página, paga con cada tarjeta y regresa al comercio.

---

## 3. HU-W07 · Socios de distribución (KAN-30)

La v2 del plan (rama `feat/KAN-30-socios-api`) sigue valiendo en: el portal, las 4 claves, los tokens ES256, el
presupuesto, el consumo, la salud y el modo prueba. Esta sección **reemplaza** lo que la v2 decía de la firma, el cobro,
la comisión y las fases, y absorbe su sección «Revisión contra `develop`».

### 3.1 Decisiones que cambian o se agregan

| # | Tema | Antes (v2) | Ahora | Estado |
|---|---|---|---|---|
| W07-5 | Firma | Enlace del proveedor que el socio abre en su app; `stubs/firma` con su página | **El mismo enlace de W03** (W03-22): Solventa lo envía **al correo del titular**. El titular firma y paga en la página de Solventa | Propuesta |
| W07-6 | Cobro | Convenio de recaudo (W03-5) | **La pasarela de la sección 2:** el titular paga con su tarjeta en la página del stub. El socio nunca toca la tarjeta | **Cerrada** (sigue W03-28) |
| W07-7 | Comisión | Al recibir `cobro.estado-cambiado` | **Al emitir**, porque la emisión ocurre al confirmarse el primer cobro (síncrono). Comisión = prima × tasa congelada al aceptar. Los cobros recurrentes siguen fuera de alcance | Propuesta |
| W07-15 | Identidad del cliente final | — | **El socio hace el papel del asesor:** pide el código (llega al correo del titular), lo verifica con lo que el titular le dicta en su app y luego «emite» | Propuesta |
| W07-16 | Contacto del cliente final | Abierta (usuario `SIN_ACCESO` en AUTH, entre otras) | **Cerrada por W03-25:** el socio envía nombre y correo al aceptar y POLICY los guarda cifrados en la propuesta. Se borran con el consentimiento si no se emite (W03-26) | **Cerrada** |
| W07-17 | El enlace no vuelve al socio | — | La API responde solo el estado y el vencimiento del enlace, **nunca la URL**. Así el titular es el único que puede abrirla (no repudio, W03-8). El socio reenvía con `POST /v1/propuestas/{id}/reenviar-enlace` | Propuesta |
| W07-18 | Reabrir | — | El socio **no** reabre una propuesta (W03-14). Un rechazo lo resuelve el titular eligiendo otra tarjeta en la pasarela | **Cerrada** |
| — | Fases A, B y C | Por la dependencia con W03 | **Desaparecen.** W07 se construye después de los ajustes de la sección 1 y de la pasarela (sección 2) | **Cerrada** |

### 3.2 La API `/v1` (contrato `contracts/openapi/socios-v1.yaml`, con la GraphQL en v0.18)

| Grupo | Endpoints | Qué reutiliza |
|---|---|---|
| Acceso (públicos) | `POST /v1/token`, `GET /v1/.well-known/jwks.json`, `GET /v1/docs`, `GET /v1/openapi.json` | Sin cambios desde la v2 |
| Consentimiento | `POST /v1/consentimientos`, `POST /v1/consentimientos/{id}/revocar` | `ConsentimientoInput`. La revocación avisa a POLICY y a RISK (W01-A2) |
| Cotización | `POST /v1/cotizaciones` → oferta y `restantes` del presupuesto | `ServicioOferta.solicitar`. **Es la única que descuenta presupuesto** (W07-9) |
| Propuesta | `POST /v1/propuestas` (`ofertaId` y **`contacto {nombre, correo}` obligatorio**), `GET /v1/propuestas/{id}` | `aceptarOferta` con el contacto (W03-25) |
| Identidad | `POST /v1/propuestas/{id}/codigo`, `POST /v1/propuestas/{id}/codigo/verificar` | W03-20 |
| Emisión | `POST /v1/propuestas/{id}/emitir` (UNDER, KYC y envío del enlace) y `POST /v1/propuestas/{id}/reenviar-enlace` | W03-21 y W03-22 |
| Seguimiento | `GET /v1/propuestas/{id}`, con estado, `mensajeEstado` y el vencimiento del enlace, por consulta periódica (W07-8) | `explicar_estado` del BFF |
| Póliza | `GET /v1/polizas/{id}` y `GET /v1/polizas/{id}/documento` (el PDF firmado) | La ruta REST del PDF que ya tiene el BFF |
| Cuenta | `GET /v1/estado`, `GET /v1/consumo`, `GET /v1/ventas`, `GET /v1/credenciales` y `POST /v1/credenciales/{entorno}/rotar` | Sin cambios desde la v2 |

Reglas comunes, sin cambios: montos como texto decimal, `ErrorPublico` en ES o EN, nunca un 5xx, 429 solo en
cotizaciones y 404 si la propuesta no es del socio.

### 3.3 Bloques de backend

| Bloque | Qué | Cambio frente a la v2 |
|---|---|---|
| W07-1 · Contrato | `socios-v1.yaml`, GraphQL v0.18 (`estadoSistema` para BACKOFFICE), `contracts/salud.json` y su validación | Agrega los endpoints de identidad, emisión y reenvío. Quita `solicitud-firma` |
| W07-2 · AUTH | Socios, credenciales, presupuestos, consumos, ES256, JWKS y CLI `crear-socio` | **Migración `0009`** (la `0008` es de W03-B7). `SOCIO` entra en `ck_consentimientos_actor_rol` |
| W07-3 · BFF `/v1` | Sub-app con autenticación JWKS y registro de consumo. Cotización con reserva de presupuesto. Propuesta, código, emitir y seguimiento reutilizan los servicios de W03 con el actor `SOCIO` | `emision.py` deja de tener firma propia: llama a los mismos servicios que el modal del asesor |
| W07-3b · Salud | Porcentaje ponderado, disponibilidad y latencia desde Cloud Monitoring o Prometheus | La API **Emisión** suma UNDER y la pasarela pasa a ser síncrona. **Pesos propuestos nuevos:** bff 20, auth 15, acl 15, policy 12, rating 10, risk 8, under 8, payments 8, consolidador 4. Las APIs externas de Emisión son firma, KYC, pasarela y notificaciones |
| W07-4 · Modo prueba | El dummy del BFF: cotización por documento de prueba y propuesta en Redis con TTL | Simula también el código (uno fijo, `000000`), el emitir y un **enlace de prueba** a una página del dummy que firma y «paga» sola. Nunca llama a la pasarela ni a ningún stub |
| W07-5 · Comisiones | POLICY: `propuestas.canal`, `socio_id` y `tasa_comision`; tabla `comisiones`, append-only y única por cobro | **Migración `0008` de POLICY** (la `0007` es de W03). La comisión se causa al emitir. `GET /socios/{id}/ventas` |
| W07-6 · Gateway e infraestructura | Envoy `/v1` con `jwt_authn` y CORS; GCP `openapi-socios.yaml` detrás de `crear_gateway_socios`; secreto `clave-firma-socios`; `monitoring.viewer` para el BFF | Sin cambios, salvo las rutas nuevas |
| ~~Firma por enlace propia~~ | — | **Se elimina:** la cubre W03 |

**Lo que hace Felipe a mano:** crear `clave-firma-socios` en Secret Manager de staging, dar de alta el socio demo con
`crear-socio` y validar los pesos de `salud.json` con el equipo.

---

## 4. Orden de ejecución

Cada paso es un PR hacia `develop`. Los pasos de la misma fila pueden ir en paralelo.

| Paso | Qué | Historia | Depende de |
|---|---|---|---|
| 1 | Contrato **v0.17**: `aceptarOferta` con contacto, mutaciones del asesor, operaciones del enlace, `iniciarPago` y `confirmarPago`, estados nuevos, `CrearUsuarioInput.documentoIdentidad` y el `Cobro` aditivo | W03, W05 | — |
| 2a | **Stub de la pasarela** completo y el **ACL** con sus dos operaciones nuevas (sección 2) | W03, W05 | 1 |
| 2b | **RISK:** huecos W01-A3 a A5, con su Cloud Scheduler | W01 | — (independiente) |
| 2c | **AUTH `0008`:** cédula y correo obligatorios para el CLIENTE; recrear los usuarios de prueba | W03 | 1 |
| 3 | **PAYMENTS `0008`:** sesiones de pago, medios de pago y origen del cobro | W05 | 2a |
| 4 | **POLICY `0007`:** contacto por rol, código, «Emitir» con UNDER, enlace, firma en tres modos, emisión síncrona, aviso por Cloud Tasks y purga | W03 | 2c, 3 |
| 5 | **BFF:** aceptar con contacto, modal del asesor, operaciones del enlace sin sesión y revocación que avisa a POLICY y a RISK | W01, W03 | 2b, 4 |
| 6 | **Infra W03:** rutas de Envoy y de API Gateway, ingreso público de `stub-pasarela` en staging, Cloud Scheduler (purgas de POLICY y RISK) y `policy → payments` en `servicios.json` | W03 | 5 |
| 7 | **Pruebas W03:** Newman (carpeta 6), EC005 y EC007 desde el regreso de la pasarela, y EC022 del contacto, la imagen y el token | W03 | 6 |
| 8 | Contrato **v0.18** y `socios-v1.yaml` | W07 | 7 |
| 9a | **AUTH `0009`:** socios, claves, presupuesto, ES256 y CLI | W07 | 8 |
| 9b | **POLICY `0008`:** canal, socio, tasa y comisiones al emitir | W07 | 8 |
| 10 | **BFF `/v1`** con el dummy del modo prueba | W07 | 9a, 9b |
| 11 | **Salud** (bloque 3b) con los pesos nuevos | W07 | 10 |
| 12 | **Gateway de socios** e infraestructura de W07 | W07 | 10 |
| 13 | **Pruebas W07:** pytest, Newman de socios y la evidencia `HU-W07-<fecha>.md` | W07 | 11, 12 |

**El frontend** arranca cuando existe el contrato de su parte: después del paso 1 para W01 y W03 y del paso 8 para
W07. Va contra el BFF con datos simulados hasta que el backend de esa parte se integre.

**Lo que no cambia de lugar:** nada de `terraform apply` a mano (ADR-09). Lo que cobra por hora lo enciende el equipo
con el workflow «Costos staging».

---

## 5. Tareas de backend y de frontend

### 5.1 Backend (`solventa-backend`)

| Historia | Tareas |
|---|---|
| W01 | <br>• RISK: `consentimiento_id` y su vencimiento en el perfil, borrado al revocar, purga diaria y batch que omite los vencidos. <br>• BFF: la revocación avisa a POLICY y a RISK. <br>• Cloud Scheduler de la purga de RISK |
| W03 | <br>• Contrato v0.17. <br>• AUTH `0008` (cédula y correo) y recrear los usuarios de prueba. <br>• POLICY `0007`: contacto por rol cifrado, código, «Emitir» con UNDER, enlace de 30 min, firma en tres modos con validación de la imagen, PDF con la firma, emisión síncrona, aviso por Cloud Tasks y purga del contacto. <br>• BFF: operaciones del asesor y del enlace, `mensajeEstado` con los estados nuevos y el PDF por enlace. <br>• ACL: plantillas del código y del enlace. <br>• Infra: Envoy, API Gateway y Cloud Scheduler. <br>• Pruebas: Newman, EC005 y EC007 y EC022 |
| W05 | <br>• PAYMENTS `0008`: sesiones de pago, medios de pago y origen; `_reintentar` sin los interactivos. <br>• `Cobro` aditivo para el BACKOFFICE. <br>• Actualizar `docs/cobros-conciliacion.md` (CA-W05-03 queda cubierto) |
| Pasarela (sección 2) | <br>• `stubs/pasarela`: API de sesiones, **página de pago propia** (HTML y JS servidos por el stub: formulario, animación, resultado y «Regresar al comercio»), tokenización, tarjetas de prueba, lista blanca de retorno y modos de falla. <br>• ACL: `crear_sesion_pago` y `consultar_sesion_pago`, principal y alterno. <br>• Infra: ingreso público del stub en staging. <br>**La página de la pasarela es trabajo de backend**, porque es parte del stub y no del front de Solventa |
| W07 | <br>• Contrato v0.18 y `socios-v1.yaml`. <br>• AUTH `0009` y la CLI. <br>• BFF `/v1` y el dummy. <br>• Salud y `salud.json`. <br>• POLICY `0008` y las comisiones. <br>• Gateway de socios. <br>• Pruebas y evidencia |

### 5.2 Frontend (`solventa-frontend-web`)

| Historia | Pantalla o pieza | Qué cambia |
|---|---|---|
| W01 | W01-01 Solicitud y consentimiento | Suma **nombre completo y correo**. Con un CLIENTE se rellenan desde su perfil. Se envían al aceptar la oferta, no al cotizar |
| W03 | Registro de CLIENTE (`crearUsuario`) | Cédula y correo obligatorios |
| W03 | **Modal del asesor** (nuevo) | Correo enmascarado y editable, «Enviar código», campo del código, **«Emitir»**, «Reenviar enlace» y los mensajes de UNDER (rechazo y revisión) |
| W03 | **Página del enlace** (W03-02) | Ruta pública `/firma#t=…` **sin perfil ni migas**. Lee el token del fragmento y lo envía en `X-Enlace-Firma`. Muestra: <br>• el **contrato en PDF**; <br>• la casilla de términos; <br>• **firma en tres modos**: lienzo para dibujar, carga de PNG o JPG con validación previa de tipo, tamaño y fondo, y nombre estilizado; <br>• un **estado vacío** si el enlace venció. <br>Retoma el paso donde iba |
| W03 | W03-02b Confirmar firma y autorización de cobro | Sin perfil ni migas. Textos corregidos: el cobro va a la tarjeta, no «con la cuota del crédito», y la póliza se emite al pagar |
| W03 | W03-03 Firma confirmada | «Ir al inicio» pasa a ser **«Pagar»**, que llama a `iniciarPago` y redirige a la página de la pasarela. Se quitan «validación biométrica», «en segundo plano» y el avance automático |
| W03 | **Retorno de la pasarela** (nuevo) | Ruta `/pago/retorno?sesion=…`: llama a `confirmarPago` y lleva a W03-04. Si falló, ofrece **«Reintentar»** y, al 3.er fallo seguido o con un rechazo, **«Pagar con otra tarjeta»** (una sesión nueva). Si la sesión venció, la vuelve a abrir |
| W03 | W03-04 Póliza emitida | Sin «Ir al inicio». **«Descargar documento» abre el PDF en una pestaña nueva** con el token del enlace. Hay que revisar «Ver detalle de fuentes consultadas», porque exige sesión |
| W03 | W03-05 Documento de póliza | **No se construye** |
| W05 | Conciliación de pagos (W05-01) | Columnas de origen del cobro y de marca y tipo del medio |
| W07 | Portal de socios (W07-01, 02 y 03) | Lo del bloque 7 de la v2, sin cambios: ingreso con la clave, panel con credenciales, estado, consumo, ventas y comisiones, Swagger de producción y de prueba |
| W07 | Estado del sistema (BACKOFFICE) | La barra general y la tabla por micro, con `estadoSistema` |

### 5.3 Pendiente antes de empezar

1. **El visto bueno de Felipe** sobre las decisiones marcadas como Propuesta: W01-A2 a A6, W05-A5, W07-5, 7, 15 y 17,
   los pesos nuevos de salud y los detalles de W03 (código, límites de la imagen, presupuesto de la pasarela).
2. **Pasar a Jira:**
   - los CA nuevos y cambiados de KAN-26;
   - el criterio nuevo de KAN-24 (W01-01 pide nombre y correo, W01-A1);
   - la nota de CA-W05-03.
3. **Integrar en la v2 de W07** lo de la sección 3, en la rama `feat/KAN-30-socios-api`, cuando se retome.
