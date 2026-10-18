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

