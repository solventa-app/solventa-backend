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
