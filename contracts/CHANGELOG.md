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
