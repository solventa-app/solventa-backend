# Bitácora de contratos

Formato: fecha · historia · cambio · ¿afecta al frontend?

## v0 — 2026-10-03

- Versión inicial propuesta para el Sprint 1 (KAN-24 HU-W01, KAN-28 HU-W05): consentimiento, oferta, contacto con asesor y cobros.
- Eventos iniciales: `consentimiento.registrado`, `consentimiento.revocado`, `perfil.actualizado`, `cobro.estado-cambiado`.
- Estado: **propuesta** a refinar en PF2-1 contra las pantallas de Figma (plano v21). Afecta al frontend: sí.

## v0.1 — 2026-10-14

- KAN-24 (HU-M02, CA-W01-11): usuarios y sesión. Tipos `Usuario`, `Sesion`, `RolUsuario`, `EstadoUsuario`,
  `ErrorAutenticacion`, `ErrorUsuario` y las uniones `ResultadoSesion` / `ResultadoUsuario`. Consultas `yo` y `usuarios`;
  mutaciones `iniciarSesion`, `crearUsuario`, `cambiarRolUsuario` y `suspenderUsuario`. Solo cambios aditivos.
- Autenticación: el token de `iniciarSesion` se envía en `Authorization: Bearer <token>`. Con la bandera del BFF en
  `opcional` (valor actual), las operaciones existentes siguen funcionando sin token; cuando pase a `obligatoria`, todas
  exigirán token. Ese cambio se avisará antes y requiere el PR gemelo en `solventa-frontend`.
- Afecta al frontend: sí (operaciones nuevas; las existentes no cambian de forma).

## v0.2 — 2026-10-16

- KAN-28 (HU-W05, CA-W05-01/02): cobros para el back-office. `cobros` acepta el filtro opcional `polizaId`; `Cobro`
  suma los campos opcionales `motivo` e `intentos`. Solo cambios aditivos.
- `cobros` y `reintentarCobro` dejan de responder "no disponible": devuelven datos reales de PAYMENTS y **exigen token
  de un BACKOFFICE siempre**, aun con la bandera del BFF en `opcional` (son datos de pagos).
- `reintentarCobro` concilia con la pasarela antes de cobrar y reutiliza la misma clave de idempotencia. Sobre un cobro
  `cobrado` o `cobrando` lo devuelve sin cambios; sobre uno `rechazado` responde un error ("requiere otro medio de pago").
- Afecta al frontend: sí (pantalla de cobros del back-office). Requiere el PR gemelo en `solventa-frontend`.

## v0.3 — 2026-10-17

- KAN-28 (HU-W05): mutación `crearCobro(entrada: CrearCobroInput!): ResultadoCobro!` con los tipos nuevos
  `MontoInput`, `CrearCobroInput`, `ErrorCobro` y la unión `ResultadoCobro`. Solo la puede usar un BACKOFFICE y siempre
  con token. El cliente genera `idIntentoCobro` (un UUID por intento) y lo reenvía igual si repite la orden.
- Evento `cobro.estado-cambiado`: se agrega `idIntentoCobro`; `claveIdempotencia` queda obsoleta (mismo valor).
- Afecta al frontend: sí (operación nueva; las existentes no cambian de forma).

## v0.4 — 2026-10-17

- KAN-28: **cambio incompatible, aprobado por el equipo.** El evento `cobro.estado-cambiado` pasa a `version: 2`: se
  quita `claveIdempotencia` y `idIntentoCobro` es obligatorio. Es una excepción a las reglas 1 y 2 (no se esperó un
  sprint con el campo obsoleto): el evento aún no tiene consumidores, porque no hay Pub/Sub real ni nada desplegado.
- Afecta al frontend: no (el frontend no consume eventos).

## v0.5 — 2026-10-17

- KAN-28: el back-office ve el nombre del cliente en los cobros. Tipo nuevo `ClienteCobro { id, nombre }`; `Cobro` suma
  `cliente` (opcional); `CrearCobroInput` suma `clienteId` (opcional); `CrearUsuarioInput` suma `nombre` (opcional, solo
  para CLIENTE). Solo cambios aditivos.
- El nombre es dato personal: AUTH lo guarda cifrado y solo sale por `cobros` (solo BACKOFFICE, con token). `Usuario` no
  lo expone. Si AUTH no responde, el cobro llega con `cliente.nombre` en `null`, nunca un error.
- Afecta al frontend: sí (la tabla de cobros puede mostrar el cliente; el alta de CLIENTE puede pedir el nombre).

## v0.5.1 — 2026-10-17

- KAN-176: `CrearCobroInput.clienteId` y `CrearUsuarioInput.nombre` declaran su valor por defecto (`= null`), que el BFF
  ya exponía. Sin cambio de comportamiento: siguen siendo opcionales. Lo detectó la prueba de equivalencia
  esquema↔contrato al alinear `graphql-core` 3.3.0 en el CI.
- Afecta al frontend: no.

## v0.6 — 2026-10-17

- KAN-28 (HU-W05, CA-W05-02): un cobro `RECHAZADO` se puede reintentar a mano **hasta 3 veces** (decisión del equipo
  según el prototipo W05-01). `Cobro` suma `reintentosRestantes` e `idTransaccion` (la referencia que devolvió la pasarela, columna "ID de transacción" de W05-01),
  ambos opcionales. Solo cambios aditivos en el esquema.
- Cambio de comportamiento: `reintentarCobro` sobre un `RECHAZADO` ya no responde "requiere otro medio de pago" de
  entrada: hace un intento nuevo ante la pasarela (con su propia referencia) y devuelve el cobro. Solo al agotar los 3
  responde el error "Se agotaron los 3 reintentos: el cobro requiere otro medio de pago.".
- Afecta al frontend: sí (W05-01: el botón de reintentar va en los rechazados con `reintentosRestantes > 0`). Requiere el
  PR gemelo en `solventa-frontend`.

## v0.7 — 2026-10-18

- KAN-28: cada usuario ve su propio nombre. `Usuario` suma `nombre` (opcional): solo lo llenan `iniciarSesion` y `yo`, y
  cada quien recibe el suyo; en `usuarios` y `crearUsuario` va en `null`. Solo cambios aditivos. (`Cobro.idTransaccion`,
  que se había pedido junto con esto, ya llegó en la v0.6.)
- `CrearUsuarioInput.nombre` pasa a admitirse en **cualquier rol** (en v0.5 era solo CLIENTE). Es más permisivo, no rompe
  a nadie.
- El nombre **no viaja en el JWT** (va en base64, sin cifrar y vale 60 minutos): el front lo pide por GraphQL tras
  iniciar sesión. Si es `null`, mostrar el alias (`usuario`). Los pagos siguen siendo solo del BACKOFFICE.
- Afecta al frontend: sí (saludo con el nombre).

## v0.8 — 2026-10-18

- KAN-28 (HU-W05, CA-W05-01): paginación en la consulta de cobros para la tabla del back-office.
- La consulta `cobros` recibe argumentos opcionales con valores por defecto: `pagina: Int = 1` y `tamano: Int = 10`.
- Cambio aditivo y compatible hacia atrás: clientes que no envíen estos parámetros reciben la primera página de 10 elementos.
- Afecta al frontend: sí (la tabla de W05 implementa paginación y controles de avance/retroceso). Requiere PR gemelo en `solventa-frontend`.

## v0.9 — 2026-10-18

- KAN-28 (Bloque I - Jurisdicciones): se define el catálogo canónico de jurisdicciones en `contracts/jurisdicciones.json`
  (copia idéntica en frontend `contrato/jurisdicciones.json`) para 7 países: CO (COP, CC), MX (MXN, CURP), BR (BRL, CPF),
  CL (CLP, RUT), PE (PEN, DNI), US (USD, SSN) y ES (EUR, DNI_NIE).
- Incluye parámetros regulatorios, límites de montos, plazos, DTI máximo y régimen de finanzas abiertas.
- Afecta al frontend: sí (catálogo compartido y validación de documentos por país).

## v0.10 — 2026-10-19

- KAN-24 (HU-W01, bloques 2, 4 y 8): cierre de pendientes de consentimiento, perfil de riesgo y motivos de error de
  oferta. Solo cambios aditivos.
- `ConsentimientoInput` suma los campos opcionales `documentoIdentidad`, `tipoDato`, `fuenteDatos` y `canal` (`= null`
  por defecto) para ligar el consentimiento al titular (hash opaco en AUTH, nunca documento en claro).
- `Consentimiento` suma `venceEn: DateTime!` (vigencia de 30 días por defecto, calculada al leer) y
  `estado: EstadoConsentimiento!` (`VIGENTE`, `REVOCADO`, `VENCIDO`).
- `ErrorOferta` suma `motivo: MotivoErrorOferta` (`CONSENTIMIENTO_NO_VIGENTE`, `DATOS_INSUFICIENTES`,
  `FUENTES_NO_DISPONIBLES`) para que el frontend distinga un consentimiento vencido/revocado sin depender del texto del
  mensaje.
- `PerfilRiesgo { score, nivelRiesgo, fechaVigencia }` (`NivelRiesgo`: `BAJO`, `MEDIO`, `ALTO`) y `Oferta.perfilRiesgo`
  (opcional), junto con el score calculado y cacheado en RISK.
- Evento `perfil.actualizado`: suma los campos opcionales `nivelRiesgo`, `fechaVigencia` y `versionReglas` para la
  suscripción de Pub/Sub en RATING (`crear_pubsub`).
- Afecta al frontend: sí (`useCotizacion` envía los campos de titular/canal en `registrarConsentimiento` y reacciona a
  `motivo = CONSENTIMIENTO_NO_VIGENTE`). Requiere el PR gemelo en `solventa-frontend-web`.


## v0.11 — 2026-10-21

- KAN-209 (idioma y moneda): `Usuario` suma `idioma: String!` y `moneda: String!` (por defecto `ES` y `COP`), que
  `iniciarSesion` y `yo` devuelven para que la web aplique las preferencias al entrar. Mutación nueva
  `actualizarPreferencias(entrada: PreferenciasInput!): ResultadoUsuario!` (el propio usuario, con token) y consulta
  nueva `convertirMontos(montos: [MontoInput!]!, moneda: String!): [Monto!]!`, que convierte montos para mostrarlos con
  las tasas del ACL Worker (`GET /tasas-cambio`). Solo cambios aditivos.
- Sin cambio de esquema: el BFF devuelve sus mensajes en inglés si la petición trae `Accept-Language: en`.
- Afecta al frontend: sí (selectores de idioma y moneda del header y preferencias en Mi perfil). Requiere el PR gemelo
  en `solventa-frontend-web`.

## v0.12 — 2026-10-22

- KAN-26 (HU-W03, bloque 0): suscripción, firma y emisión de la póliza. Solo cambios aditivos.
- Mutaciones nuevas `aceptarOferta(ofertaId: ID!): ResultadoAceptacion!` y `firmarPropuesta(entrada: FirmarPropuestaInput!):
  ResultadoFirma!`; consultas nuevas `propuesta(id)`, `documentoFirma(propuestaId)` y `poliza(id)`.
- Tipos nuevos: `Propuesta`, `EstadoPropuesta` (`ACEPTADA`, `EN_EMISION`, `EN_REVISION`, `PENDIENTE_PAGO`,
  `PAGO_RECHAZADO`, `NO_EMITIDA`, `EMITIDA`), `DocumentoFirma`, `FirmaRegistrada`, `Poliza`, `EstadoPoliza`, los errores
  `ErrorAceptacion` y `ErrorFirma` con sus motivos (`MotivoErrorAceptacion`, `MotivoErrorFirma`) y las uniones
  `ResultadoAceptacion` y `ResultadoFirma`.
- La emisión corre en segundo plano después de firmar: el cliente consulta `propuesta` hasta ver `EMITIDA`. El precio
  nunca lo manda el cliente. Solo el CLIENTE titular firma.
- Evento nuevo `poliza.emitida` v1 (`polizaId`, `numeroPoliza`, `propuestaId`, `clienteId` opaco, `vigenciaDesde`,
  `vigenciaHasta`).
- Afecta al frontend: sí (pantallas W03-01 a W03-05). Requiere el PR gemelo en `solventa-frontend-web`.

## v0.13 — 2026-10-23

- KAN-27 (HU-W04, siniestros asistidos y peritaje): contrato nuevo `contracts/graphql/siniestros.graphql` del **dummy**
  `stubs/siniestros`. Es un microservicio aparte (sin base de datos ni tokens) que expone su propio GraphQL en
  `/graphql/siniestros`; **no** cambia `schema.graphql` ni el BFF.
- Consultas `siniestros(estado)`, `siniestro(id)` y `peritos(siniestroId)`; mutaciones `aprobarSiniestro`,
  `rechazarSiniestro(id, motivo, comentario)` y `derivarSiniestro(id, peritoId, instrucciones)`, que devuelven
  `ResultadoResolucion = Siniestro | ErrorSiniestro`. `EstadoSiniestro`: `PENDIENTE`, `EN_REVISION`, `APROBADO`,
  `RECHAZADO` (derivar a perito deja `EN_REVISION`).
- `Poliza` del dummy copia la forma de la tabla `polizas` y del tipo `Poliza` de HU-W03 (ver
  `stubs/siniestros/app/datos/contrato-poliza.json`); el número sigue el formato `SOL-<año>-<6 dígitos>`.
- Temporal: se reemplaza por el contrato del servicio real de siniestros. Por ahora solo corre en local.
- Afecta al frontend: sí (pantallas W04-01 a W04-03c). Requiere el PR gemelo en `solventa-frontend-web`.

## v0.13 — 2026-10-24

- KAN-26 (HU-W03, CA-W03-01 y D-11): **sin cambios de campos ni de tipos**; se aclara una semántica. Una oferta
  `PRELIMINAR` no se puede aceptar y ahora el BFF lo dice ofreciendo recalcularla: `ErrorAceptacion` con motivo
  `OFERTA_PRELIMINAR` devuelve `puedeReintentar: true` (antes `false`) y el mensaje «Esta oferta es preliminar y no se
  puede aceptar. Vuelve a calcularla.». Reintentar significa volver a cotizar (`solicitarOferta`), no repetir
  `aceptarOferta` con la misma oferta: sería el mismo error. Los demás motivos no cambian.
- Afecta al frontend: sí, solo en cómo trata ese error (mostrar «Recalcular» y llevar a una cotización nueva). Aún no
  hay una pantalla W03 construida que lo use.

## v0.14 — 2026-10-24

- KAN-26 (HU-W03, CA-W03-06 y decisión W03-14): **sin cambios de campos ni de tipos**; se aclara una semántica.
  `PAGO_RECHAZADO` deja de ser un estado final: si el primer cobro se recupera después (el back-office reintenta un cobro
  rechazado, o la pasarela cobra uno pendiente tras agotar las consultas), POLICY reabre la propuesta y pasa a `EMITIDA`,
  con su póliza y un solo aviso al cliente. Lo reabren el back-office y el proceso de reconciliación; **el cliente no**
  puede, y no hay mutación nueva.
- Afecta al frontend: sí, en cómo trata la consulta de `propuesta`. Un cliente que ve `PAGO_RECHAZADO` y vuelve a
  consultar puede encontrar `EMITIDA`; no debe tratar el rechazo como definitivo. Aún no hay una pantalla W03 que lo use.

## v0.15 — 2026-10-24

- KAN-26 (HU-W03, CA-W03-06 y CA-W03-01): **solo cambios aditivos**.
  - `Propuesta` suma tres campos opcionales: `motivoEstado: MotivoEstadoPropuesta`, `mensajeEstado: String` y
    `accionSugerida: AccionSugerida`. Vienen cuando la propuesta no avanza (`EN_REVISION`, `PENDIENTE_PAGO`,
    `PAGO_RECHAZADO` y `NO_EMITIDA`) y son `null` en los demás estados. Enums nuevos: `MotivoEstadoPropuesta`
    (`PAGO_FONDOS_INSUFICIENTES`, `PAGO_RECHAZADO`, `PAGO_EN_PROCESO`, `REVISION_MANUAL`, `NO_APROBADA`) y
    `AccionSugerida` (`ESPERAR`, `CONTACTAR_ASESOR`). El motivo es una categoría para el cliente: el código interno de la
    decisión de suscripción (p. ej. riesgo alto o KYC rechazado) y el de la pasarela **no salen del BFF**. Los textos de
    `mensajeEstado` son una propuesta (decisión W03-15) y siguen el idioma de `Accept-Language`.
  - `oferta(id)` ya no devuelve siempre `null`: entrega la oferta que RATING guardó al calcularla, con la misma validación
    de dueño que `aceptarOferta` (un CLIENTE solo ve la que consintió él; un ASESOR, la de su cliente). Es `null` si venció,
    no existe o no es suya, y un error con reintento si RATING o AUTH no responden. Sin cambio de esquema.
- Afecta al frontend: sí. La pantalla «Resultado del pago» (por crear en Figma) puede mostrar `mensajeEstado` y decidir el
  botón con `accionSugerida` (`CONTACTAR_ASESOR` lleva a `contactarAsesor`); `oferta(id)` sirve para «ver fuentes» sin
  guardar la oferta en memoria. Los campos son opcionales: no rompen el codegen. Aún no hay una pantalla W03 que los use.

## v0.16 — 2026-10-24

- KAN-26 (HU-W03, bloque 8, W03-16 y W03-17): el correo y el teléfono del cliente, para enviarle el PDF de su póliza.
  **Solo cambios aditivos.**
  - `CrearUsuarioInput` suma `correo` y `telefono`, opcionales (`null` por omisión). Solo para un CLIENTE: con otro rol
    `crearUsuario` responde `ErrorUsuario` («El contacto solo se guarda para clientes.»).
  - Mutación nueva `actualizarContacto(entrada: ContactoInput!): ResultadoUsuario!` con el input nuevo `ContactoInput`
    (`correo`, `telefono`): la usa el propio CLIENTE (el id sale del token; siempre exige token) y lo que no llega se
    conserva. Un dato inválido o sin ningún campo devuelve `ErrorUsuario` con un mensaje en el idioma de `Accept-Language`.
  - `Usuario` suma `tieneContacto: Boolean!`: dice si ya hay contacto (para que la web sepa cuándo pedirlo), **nunca cuál**.
    El correo y el teléfono se guardan cifrados en AUTH y **no se pueden leer por GraphQL** en ningún camino.
  - El correo se guarda en minúsculas (solo se revisa su forma) y el teléfono con dígitos y un `+` opcional (7 a 15).
- Afecta al frontend: sí. Web debe construir la pantalla donde el cliente deja su correo y su teléfono (hoy no existe en
  Figma) y llamar a `actualizarContacto`; puede usar `tieneContacto` de `yo` o `iniciarSesion` para pedirlo. Hasta que un
  cliente tenga contacto, el aviso de su póliza sigue llevando solo el enlace. Los campos son opcionales: no rompen el codegen.

## v0.17 — 2026-10-27

- KAN-250 (habilitadora de almacenamiento de archivos, base de HU-M01 y de otras historias): tipos `Archivo`,
  `EstadoArchivo`, `SubidaArchivo`, `EncabezadoSubida`, `DescargaArchivo`, `ErrorArchivo`, la entrada
  `SolicitudSubidaArchivoInput` y las uniones `ResultadoSubidaArchivo`, `ResultadoArchivo` y
  `ResultadoDescargaArchivo`. Consulta `archivo(id)`; mutaciones `solicitarSubidaArchivo`, `confirmarSubidaArchivo` y
  `descargarArchivo`. Todas exigen token siempre. Solo cambios aditivos.
- Los bytes no pasan por el BFF: el cliente hace `PUT` a la URL firmada con los `encabezados` devueltos y luego
  confirma. El catálogo de tipos es `contracts/tipos-archivo.json`; eventos nuevos `archivo.disponible` y
  `archivo.rechazado` (`contracts/events/`).
- Afecta al frontend: sí, cuando una pantalla suba archivos (hoy ningún tipo activo lo sube un CLIENTE directamente).

## v0.18 — 2026-10-27

- KAN-26 (HU-W03, bloque 9; decisiones W03-19 a W03-37 de `docs/plan-hu-w07-y-ajustes.md`) y lo que W05 necesita del
  pago (W05-A3 y W05-A5). Parte del borrador del front (`contrato/borrador-v0.17.graphql` de `solventa-frontend-web`),
  renombrado a v0.18 porque la v0.17 ya es del almacenamiento. **Solo cambios aditivos.**
  - **Flujo del ASESOR:** `aceptarOferta` suma `contacto: ContactoTitularInput` (opcional en el contrato, obligatorio
    para el ASESOR). Mutaciones `enviarCodigoVerificacion`, `verificarCodigo`, `emitirPropuesta` y
    `reenviarEnlaceFirma`, con `CodigoEnviado`, `ErrorCodigo`, `MotivoErrorCodigo`, `EnlaceEnviado`, `ErrorEmision` y
    las uniones `ResultadoEnvioCodigo`, `ResultadoVerificacion` y `ResultadoEmision`.
  - **Página del enlace o sesión del CLIENTE (W03-33):** `firmaPorEnlace`, `solicitarSubidaFirma` (devuelve el
    `ResultadoSubidaArchivo` de la v0.17, tipo `firma-imagen`), `firmarPorEnlace`, `iniciarPago` y `confirmarPago`,
    con `EnlaceFirma`, `FirmarPorEnlaceInput`, `ModoFirma`, `SesionPago`, `ErrorPago`, `MotivoErrorPago`,
    `ResultadoInicioPago` y `ResultadoPago`. Aceptan el token del enlace en `X-Enlace-Firma` **o** la sesión del
    CLIENTE titular; con sesión, `propuestaId` es obligatorio. `emitirPropuesta` es también el «Firmar» del CLIENTE.
  - **Estados:** `EstadoPropuesta` suma `IDENTIDAD_VERIFICADA`, `EN_FIRMA`, `FIRMADA` y `CANCELADA`;
    `MotivoErrorFirma` suma `ENLACE_VENCIDO` e `IMAGEN_INVALIDA`.
  - **«Mis pólizas» / «Pólizas emitidas» (W03-35):** consulta `propuestas(estado: EstadoVisible, pagina, tamano)` que
    devuelve `PaginaPropuestas`, filtrada por el rol del token. `Propuesta` suma `estadoVisible`, `creadaEn`,
    `poliza`, `nombreTitular` y `nombreAsesor` (estos dos solo para el ASESOR y el BACKOFFICE).
  - **Avisos en vivo (W03-37):** mutación `ticketAvisos` (`TicketAvisos`: ticket de un solo uso de 60 s y la URL del
    servicio `avisos`). La forma de los eventos SSE (`propuesta` y `cobro`) va documentada en `schema.graphql`.
  - **W05:** `Cobro.origen` (`OrigenCobro`) y `Cobro.medio` (`MedioPago`: marca y `TipoMedioPago`; nunca el número).
  - **Usuarios (W03-27 y W03-34):** `CrearUsuarioInput.documentoIdentidad`, `Usuario.documentoEnmascarado` y
    `SolicitudOfertaInput.documentoIdentidad` pasa a opcional (obligatorio para el ASESOR; un CLIENTE lo toma de AUTH).
  - Plantillas de notificación nuevas: `codigo-verificacion`, `enlace-firma` y `poliza-emitida-asesor`
    (`stubs/notificaciones/README.md`).
- **Diferencias con el borrador del front:** `FirmarPorEnlaceInput` cambia `firma: String!` (base64) por
  `imagenArchivoId` (la imagen va al almacenamiento, W03-24 revisada) y `nombreFirma`, y suma `aceptaTerminos` y
  `propuestaId`; `EnlaceFirma.venceEn` es opcional (no aplica con sesión); `iniciarPago` y `confirmarPago` suman
  `propuestaId` opcional. Lo demás conserva los nombres del borrador.
- **Estado en el BFF:** implementado en F-B6 (PR #368): las operaciones del asesor, las de la página del enlace, `propuestas`
  y `ticketAvisos` leen y escriben en POLICY y AUTH. `solicitarOferta` sin documento sigue dando `DATOS_INSUFICIENTES`
  para el ASESOR; un CLIENTE cotiza con el documento de su cuenta (W03-34). `mensajeEstado` solo sale en los cuatro
  estados que lista `Propuesta` (no en `CANCELADA`).
- Afecta al frontend: sí. Web copia este esquema, borra `contrato/borrador-v0.17.graphql` y regenera tipos (F-F1). Los
  enums de salida que crecen (`EstadoPropuesta`, `MotivoErrorFirma`) piden manejar los valores nuevos.

## v0.20 — 2026-10-28

- KAN-31 (HU-M01): onboarding de identidad del móvil. La v0.19 queda reservada para HU-W07. **Solo cambios aditivos.**
  - **Recorrido:** mutaciones `iniciarOnboarding` (pública), `registrarConsentimientoOnboarding`,
    `solicitarSubidaOnboarding`, `confirmarSubidaOnboarding`, `crearCuentaOnboarding`, `enviarVerificacion` y
    `reintentarOnboarding`; consulta `onboarding(id)`. Llevan el token del recorrido en la cabecera
    `X-Onboarding-Token` (30 min; no es una sesión). Tipos `Onboarding`, `InicioOnboarding`, `CuentaOnboarding`,
    `ErrorOnboarding` (con `codigo` estable), los enums `EstadoOnboarding`, `MotivoOnboarding`, `PasoOnboarding` y
    `TipoDocumentoOnboarding`, los inputs `IniciarOnboardingInput` y `CuentaOnboardingInput` y las uniones
    `ResultadoInicioOnboarding`, `ResultadoOnboarding`, `ResultadoSubidaOnboarding` (reusa `SubidaArchivo` de la v0.17)
    y `ResultadoCuentaOnboarding`.
  - **Consentimiento:** `registrarConsentimientoOnboarding` registra en AUTH la finalidad `onboarding-biometrico`
    (canal `APP`) antes de cualquier captura (regla 7).
  - **Cuenta y sesión:** `crearCuentaOnboarding` crea un CLIENTE en `PENDIENTE_VERIFICACION` (inicia sesión con su
    correo) y devuelve una sesión con el único scope `verificacion-pendiente`, que solo entra a `yo`, `miVerificacion`
    y `actualizarSesion`. Consulta nueva `miVerificacion` y mutación nueva `actualizarSesion` (token con los permisos
    del estado actual y la misma expiración).
  - **Usuarios:** `EstadoUsuario` suma `PENDIENTE_VERIFICACION` y `RECHAZADA`; `ErrorAutenticacion` suma `codigo`
    (`VERIFICACION_PENDIENTE` cuando una cuenta sin verificar entra por la web).
  - **Eventos:** `onboarding.verificado` y `onboarding.rechazado` (`contracts/events/`), solo ids opacos y códigos.
- Detrás: el servicio `onboarding` lleva el resultado a AUTH (`POST /usuarios/{id}/verificacion`, migración `0002` del
  onboarding) y el BFF pasa el canal al iniciar sesión.
- Afecta al frontend: sí. El móvil (HU-M01) usa las operaciones nuevas. La web solo debe manejar los valores nuevos de
  `EstadoUsuario` y el `codigo` `VERIFICACION_PENDIENTE` en el login.
## v0.21 — 2026-10-28

- KAN-242 (HU-W04, mesa de siniestros del BACKOFFICE; plan `docs/plan-completar-hu-w04.md`). Toma la v0.21 porque la
  v0.19 está reservada para HU-W07 y la v0.20 es del onboarding. Los tipos del dummy
  `contracts/graphql/siniestros.graphql` pasan al contrato principal con **los mismos nombres, argumentos y campos**:
  `Siniestro`, `Asegurado`, `Ubicacion`, `Evidencia`, `Verificacion`, `Perito`, `Resolucion`, `ErrorSiniestro`, la
  union `ResultadoResolucion` y los enums `EstadoSiniestro`, `TipoEvidencia`, `TipoResolucion` y `MotivoRechazo`.
  Consultas `siniestros(estado)`, `siniestro(id)` y `peritos(siniestroId)`; mutaciones `aprobarSiniestro`,
  `rechazarSiniestro` y `derivarSiniestro`. Todas exigen token de BACKOFFICE. **Solo cambios aditivos.**
  - Se reutilizan `Poliza`, `Monto` y `EstadoPoliza`. `Poliza` suma `ramo` (un solo producto hoy, lo pone el BFF).
  - `Evidencia.url` es la URL firmada de lectura del almacenamiento (vence en minutos), ya no una ruta relativa.
  - Eventos nuevos `siniestro.aprobado`, `siniestro.rechazado` y `siniestro.derivado` (`contracts/events/`), sin datos
    personales.
  - Tipos de archivo nuevos `siniestro-foto` y `siniestro-documento` (`contracts/tipos-archivo.json`).
- **Estado en el BFF:** conectado al servicio `claims` (puerto 8012). También expone `/v1/graphql/{web,app,movil}`
  con las rutas actuales como alias (W04-10).
- Afecta al frontend: sí, cuando la web cambie del dummy al BFF. Pasos documentados en
  `solventa-frontend-web/docs/plan-ejecucion-hu-w04-siniestros.md` («Conexión al backend real»). `siniestros.graphql`
  se conserva para el dummy, que sigue funcionando sin cambios.
- **2026-10-29 (KAN-27, W04-13):** se retira `contracts/graphql/siniestros.graphql` junto con el dummy `stubs/siniestros`:
  la web ya consume la mesa de siniestros del contrato principal por `/v1/graphql/web`. Afecta al frontend: no (la web
  dejó de copiar ese contrato en `solventa-frontend-web` #94).

## v0.22 — 2026-10-29

- KAN-30 (HU-W07, socios de distribución; plan `docs/plan-completar-hu-w07.md`). **La v0.19 deja de estar reservada
  y no se usa:** W07 toma la siguiente libre (decisión W07-23). **Solo cambios aditivos.**
  - **API REST nueva para socios:** `contracts/openapi/socios-v1.json` (OpenAPI 3.1, en JSON). Rutas bajo `/v1/`,
    fuera de `/v1/graphql/*`, que sigue igual (W07-24). Token ES256 de 15 min (`POST /v1/token`, JWKS público),
    consentimiento, cotización (la única que descuenta presupuesto), propuesta con el contacto del titular, código de
    verificación, emitir y reenviar el enlace (la URL nunca vuelve al socio), póliza y su PDF, y la cuenta (estado,
    consumo, ventas y credenciales). Errores con `ErrorPublico` (`codigo` estable); nunca un 5xx crudo.
  - **GraphQL:** consulta `estadoSistema(ventana)`, solo BACKOFFICE, con los tipos `EstadoSistema`, `SaludApi`,
    `SaludMicro`, `SaludExterna` y los enums `ColorSalud` y `EstadoCircuito`. `Propuesta` suma `nombreSocio` (solo
    BACKOFFICE).
  - **`contracts/salud.json`:** pesos, objetivos de latencia, ventanas, umbrales de color y las APIs del socio. Una API
    queda en ROJO si uno de sus micros no responde, y la barra general no es VERDE si alguna API está en ROJO.
- **Estado en el BFF:** implementado (2026-10-29). La API `/v1` de socios (con el modo prueba), `estadoSistema` y
  `nombreSocio`. El contrato REST ajustó `plazoMeses` (1 a 60) y el documento (al menos 6 dígitos) a lo que ya valida
  W01.
- Afecta al frontend: sí. El portal de socios (W07) genera sus tipos de `socios-v1.json`; la web del BACKOFFICE puede
  copiar el esquema y usar `estadoSistema` y `nombreSocio` cuando el BFF los implemente.

### Adición en v0.22 (KAN-31 · Validación OTP de correo e idempotencia en HU-M01) — 2026-10-31

- **Solo cambios aditivos** en `contracts/graphql/schema.graphql`:
  - Tipo `CodigoOnboarding { destino: String!, venceEn: DateTime!, reenviarEnSegundos: Int! }` y unión
    `ResultadoCodigoOnboarding = CodigoOnboarding | ErrorUsuario`.
  - Mutación `solicitarCodigoOnboarding(correo: String!): ResultadoCodigoOnboarding!` (canal `app`): genera un código OTP
    de 6 dígitos, guarda su HMAC-SHA256 en Redis (o memoria en local/pruebas) con vigencia de 10 minutos y máximo 5
    intentos (`services/bff/app/servicio_codigo_onboarding.py`), y lo envía por `acl-worker` (`POST /notificaciones/enviar`,
    plantilla `codigo-verificacion`). Si el correo ya existe en `AUTH` con estado `ACTIVO`, devuelve `ErrorUsuario` con
    `motivo: "CORREO_YA_REGISTRADO"`.
  - Mutación `verificarCodigoOnboarding(correo: String!, clave: String!, codigo: String!): ResultadoSesion!` (canal `app`):
    verifica el OTP de 6 dígitos (`CODIGO_INVALIDO`, `CODIGO_VENCIDO`, `INTENTOS_AGOTADOS`) y crea o recupera de forma
    idempotente la cuenta `CLIENTE` en estado `PENDIENTE_VERIFICACION`, devolviendo `Sesion`.
  - `crearCuentaOnboarding` pasa a ser idempotente cuando la cuenta ya estaba en `PENDIENTE_VERIFICACION` con la misma
    clave (permite reintentos tras caídas de red sin quedar bloqueado en `CORREO_YA_REGISTRADO`).
- Afecta al frontend: sí (`solventa-frontend-mobile` usa las nuevas mutaciones en `M01-05b` y `M01-05c`; no rompe
  operaciones existentes).

## v0.23 — 2026-11-01

- KAN-27 (OP-07, D-3 de `docs/plan-revision-qa-2026-10-31.md`): `Resolucion.automatica: Boolean!`, true si el sistema aprobó
  el siniestro porque `venceDictamenEn` llegó sin gestión. En ese caso el evento `siniestro.aprobado` lleva
  `actorUsuarioId = SISTEMA`. Un caso derivado a perito no se aprueba solo.
- Afecta al frontend: sí (aditivo; W04-02 lo muestra).
- KAN-30 (SO-02): API de socios `POST /v1/conversiones`, con los montos en otra moneda solo para mostrar. No descuenta
  presupuesto. Con un token de PRUEBAS usa tasas fijas (W07-10). Afecta al frontend: sí (aditivo; el panel del socio).

## v0.24 — 2026-11-01

- KAN-26 (T9 del plan de cierre de HU-W03, perfil común): `Usuario.correo: String`, solo en `iniciarSesion` y `yo` y solo
  del propio usuario; en `usuarios`, `crearUsuario` y los demás cambios va en null, y null si no tiene (solo los CLIENTE
  guardan correo). Nunca viaja dentro del JWT.
- Privacidad (EC022, W03-16): cambia que el correo no volvía por GraphQL. El **teléfono sigue sin volver** y el contacto
  completo (`GET /usuarios/{id}/contacto`) sigue siendo solo para POLICY. AUTH cifra el correo en reposo y lo descifra
  únicamente con el id del token; no se registra en logs.
- Afecta al frontend: sí (aditivo; el perfil común de web y mobile, T10).
