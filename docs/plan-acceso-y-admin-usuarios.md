# Plan: ingreso sin selector de rol, recuperación de clave y panel de administración de usuarios

Estado: **planeado, sin ejecutar** (2026-11-05; decisiones A-1, A-2, A-3 y A-6 cerradas por Frans el mismo día).
Historia de Jira por asignar (`KAN-?`). Cubre backend y web; el móvil solo se toca en la recuperación de clave
(sección 3). Antes de construir, cerrar A-4 y A-5.

## 0. Cómo está hoy (verificado en `develop` el 2026-11-05)

| Tema | Hoy | Dónde |
|---|---|---|
| Rol al ingresar | La web muestra chips CLIENTE / ASESOR / BACKOFFICE y manda el elegido en `iniciarSesion(entrada: {rol})` (T17, v0.29). AUTH responde «Usuario o clave incorrectos.» si no coincide, y la web además compara `estado.rol !== rol` y cierra la sesión | `CredencialesInput.rol`, `services/auth/app/sesiones.py` (`rol_no_permitido`), web `apps/web/src/views/IngresoConRol.vue` |
| Rol en el token | **Ya viaja en el JWT** (`rol` y `scopes` por rol) y el BFF autoriza con él (`exigir_rol`). `Sesion.usuario.rol` también lo trae | `sesiones.py` (`SCOPES_POR_ROL`), `services/bff/app/esquema.py` |
| Recuperar la clave | **No existe.** Solo hay bloqueo por intentos (`MAX_INTENTOS`, `MINUTOS_BLOQUEO`) | — |
| Contacto | Solo los CLIENTE guardan correo (cifrado + `correo_hmac` para buscar). ASESOR y BACKOFFICE no tienen correo | `services/auth/app/usuarios.py` |
| Piezas reutilizables | Código OTP de 6 dígitos con huella HMAC en Redis, espera de 60 s y reintentos (onboarding); correos por el ACL con plantilla (`codigo-registro`, Resend o stub); `revocar_de_usuario` revoca todos los refresh | `services/bff/app/servicio_codigo_onboarding.py`, ACL `adapters/correo_resend.py`, `sesiones.py` |
| Administrar usuarios | En GraphQL: `usuarios(rol)`, `crearUsuario`, `cambiarRolUsuario`, `suspenderUsuario`, **solo BACKOFFICE**. No hay reactivar, desbloquear ni restablecer clave. **No hay pantalla en la web** | contrato, AUTH `/usuarios*` |
| Roles | `CLIENTE`, `ASESOR`, `BACKOFFICE` (CHECK en `0002_usuarios`). El admin local `admin.solventa` es BACKOFFICE | `docker-compose.yml` (`crear_usuario`) |

Conclusión del punto 1: **el backend ya tiene el rol en el token**. Lo que sobra es el selector de la web; el backend solo
deja de pedir y de usar `CredencialesInput.rol`.

## 1. Decisiones

| ID | Decisión | Estado |
|---|---|---|
| **A-1** | ¿Quién es «admin»? | **Cerrada:** rol nuevo `ADMIN` que administra **la plataforma**, no el negocio: el BACKOFFICE sigue operando cobros y siniestros. Por ahora el ADMIN solo tiene el módulo de usuarios; otros módulos de plataforma se agregan después. El panel no crea ni promueve ADMIN |
| **A-2** | ¿Cómo se recupera la clave? | **Cerrada:** código de 6 dígitos **por correo, para todos los roles** (mismo patrón que el onboarding; sirve en web y app) |
| **A-3** | ¿Y el personal, que hoy no tiene correo? | **Cerrada:** se guarda el correo de **todos los roles** (cifrado y con `correo_hmac`, como el del CLIENTE). El ADMIN no ve ni pone claves: solo **fuerza la recuperación**, que invalida la clave actual, revoca las sesiones y envía el código al correo del usuario. Se descarta la clave temporal |
| **A-4** | ¿Quién envía el correo? | Abierta. Recomendación: AUTH genera el código, guarda su hash y lo valida; se lo entrega al BFF por la red privada y el **BFF lo envía por el ACL** (como el onboarding). Alternativa: AUTH llama al ACL directamente (el código no pasa por el BFF, pero AUTH depende del ACL) |
| **A-5** | ¿Transición de `crearUsuario` y compañía? | Abierta. Recomendación: durante una liberación las usan ADMIN y BACKOFFICE; en la siguiente, solo ADMIN (aviso en `CHANGELOG.md`). Así no se rompen Newman ni las E2E que crean usuarios con `admin.solventa` |
| **A-6** | ¿Cómo nace el primer ADMIN? | **Cerrada:** lo **siembra AUTH** con un job idempotente (`crear_usuario --rol ADMIN --correo ...`), y AUTH guarda **solo el hash** argon2id, nunca la clave. En local, clave fija no secreta (como `admin.solventa`). En staging, la clave inicial sale de Secret Manager (`clave-admin-inicial`, que lee el job) y el usuario queda con `debe_cambiar_clave`; si se pierde, se recupera por correo |

## 2. Bloque 1: ingreso sin selector de rol

**Backend (contrato v0.36, aditivo):**

1. `CredencialesInput.rol` pasa a `@deprecated(reason: "El rol sale del token; no lo mandes.")`. No se borra antes de un
   sprint (regla 2 de `contracts/README.md`). AUTH lo sigue aceptando mientras exista, pero el BFF deja de reenviarlo,
   así que ya no influye en el resultado.
2. Nada más cambia: `Sesion.usuario.rol` y el JWT ya dicen el rol. El canal `app` sigue aceptando solo CLIENTE.
3. Pruebas: pytest de BFF (ingresar sin `rol` con los tres roles) y la carpeta de autenticación de Newman sin `rol`.

**Web (repo `solventa-frontend-web`):**

1. `IngresoConRol.vue` → `Ingreso.vue`: solo usuario y clave, más el enlace «¿Olvidaste tu clave?». Se quitan los chips
   y la comparación local `estado.rol !== rol`.
2. Tras ingresar, `router.push(rutaInicio())` según `Sesion.usuario.rol`: CLIENTE `/inicio`, ASESOR `/asesor`,
   BACKOFFICE `/operador` y, con A-1, ADMIN `/admin/usuarios`.
3. Ajustar Figma G-01 (nodo 2001:431), las pruebas unitarias de `useSesion` y las E2E de Cypress que eligen chip.

## 3. Bloque 2: recuperación de clave

### 3.1 CLIENTE (A-2, A-4)

Contrato v0.36:

```graphql
type SolicitudRecuperacion {
  correoEnmascarado: String!   # a•••@dominio.com, igual exista o no la cuenta
  segundosRestantes: Int       # solo si pidió otro código antes de 60 s
}
type ErrorRecuperacion {
  mensaje: String!
  codigo: CodigoErrorRecuperacion!   # CODIGO_INVALIDO, CODIGO_VENCIDO, SIN_INTENTOS, CLAVE_DEBIL
}
union ResultadoRecuperacion = Sesion | ErrorRecuperacion

extend type Mutation {
  # Pública. Siempre responde lo mismo, exista o no el correo (no revela cuentas).
  solicitarRecuperacionClave(correo: String!): SolicitudRecuperacion!
  # Pública. Con el código correcto cambia la clave, revoca todas las sesiones y deja una sesión nueva.
  restablecerClave(correo: String!, codigo: String!, claveNueva: String!): ResultadoRecuperacion!
}
```

AUTH (migración `0011_recuperacion_clave`, expandir):

- Tabla `recuperaciones_clave` (`id`, `usuario_id`, `hash_codigo` HMAC, `expira_en` a los 15 min, `intentos`,
  `usado_en`, `creado_en`). Un código nuevo invalida el anterior; 5 intentos como máximo; un solo uso.
- `POST /usuarios/recuperacion` (busca por `correo_hmac`, solo cuentas ACTIVAS; si no hay cuenta responde igual y no
  genera nada) y `POST /usuarios/recuperacion/confirmar` (valida, `validar_clave`, nuevo hash argon2id, limpia el
  bloqueo, `revocar_de_usuario`).
- Espera de 60 s por cuenta entre envíos. Además, un tope por IP en el gateway (Envoy local y API Gateway) para frenar
  el abuso del endpoint público.

BFF: las dos mutaciones; envía el correo por el ACL con la plantilla nueva `recuperar-clave` (stub de notificaciones y
Resend). Ni el correo ni el código van a los logs (regla 6).

Web: pantallas «Recuperar clave» (correo → código → clave nueva), con la espera de 60 s visible. **Móvil:** el mismo flujo
en la app (el contrato es igual en el canal `app`).

### 3.2 Todos los roles y recuperación forzada (A-3)

- El flujo de 3.1 vale para **cualquier rol** con correo. En la web aparece para todos; en la app solo entra el CLIENTE.
- **Correo del personal:** no hace falta columna nueva (`correo_cifrado` y `correo_hmac` ya existen, con el índice único
  parcial `ux_usuarios_correo_hmac` de `0009`). La restricción está en el código: `usuarios.py` responde «El contacto solo
  se guarda para clientes.» y se quita para el correo (el teléfono sigue siendo solo del CLIENTE). `crearUsuario` exige
  `correo` para todos los roles (en el esquema sigue opcional para no romper; AUTH responde `ErrorUsuario` si falta) y
  ahora llena también `correo_hmac`.
- **Relleno de `correo_hmac`:** hoy solo lo tienen las cuentas del onboarding (`crear_pendiente`); los CLIENTE creados
  por el BACKOFFICE tienen el correo cifrado pero no se encuentran por correo. Un comando de AUTH
  (`python -m app.rellenar_correo_hmac`, Cloud Run Job en staging porque necesita la llave de KMS para descifrar; no va
  en la migración) lo calcula para las filas que tienen correo. Es idempotente y, si dos cuentas comparten correo, **no
  toca ninguna de las dos** y lo reporta por id opaco para que el ADMIN lo resuelva.
- **Cuentas del personal que hoy no tienen correo:** el ADMIN se lo pone desde el panel con
  `actualizarCorreoUsuario(id, correo)`. Hasta entonces, su recuperación forzada responde «La cuenta no tiene correo».
- `forzarRecuperacionClave(id: ID!): ResultadoUsuario!` (solo ADMIN): invalida la clave actual (el hash deja de servir),
  revoca todas las sesiones y envía el código al correo del usuario, que elige su clave nueva con `restablecerClave`. El
  ADMIN nunca ve ni escribe la clave de nadie.
- `debe_cambiar_clave` (columna nueva en `0011`) solo la usa el ADMIN sembrado en staging (A-6): al ingresar, la sesión
  sale con el scope `cambio-clave-pendiente` (como el `verificacion-pendiente` de HU-M01) y solo admite `cambiarClave`.
- `cambiarClave(claveActual: String!, claveNueva: String!): ResultadoSesion!`, para cualquier usuario con sesión
  (también desde «Mi perfil»).

## 4. Bloque 3: panel de administración de usuarios

**Rol (A-1):** el ADMIN administra la plataforma; este bloque solo trae el módulo de usuarios. Migración `0011` amplía el CHECK a `('CLIENTE', 'ASESOR', 'BACKOFFICE', 'ADMIN')` (expandir; el
`downgrade` falla con un mensaje claro si ya existe algún ADMIN). `RolUsuario` gana `ADMIN` en el contrato (agregar un
valor a un enum es un cambio «peligroso» para los clientes: la web y el móvil deben tolerar valores desconocidos).
`SCOPES_POR_ROL["ADMIN"] = ["usuarios"]`; el BACKOFFICE pierde el scope `usuarios` al final de la transición (A-5). El
canal `app` rechaza ADMIN, igual que hoy rechaza a todo el que no sea CLIENTE.

**Operaciones (contrato v0.36; todas `siempre=True` y solo ADMIN):**

| Operación | Estado | Notas |
|---|---|---|
| `paginaUsuarios(rol, estado, busqueda, pagina, tamano): PaginaUsuarios!` | Nueva | `busqueda` solo por alias (el nombre va cifrado y no se busca). `usuarios(rol)` sigue, con aviso de deprecación |
| `crearUsuario` | Existe | Con correo obligatorio para todos los roles; el panel no ofrece ADMIN como rol |
| `actualizarCorreoUsuario(id, correo)` | Nueva | Para el personal que hoy no tiene correo |
| `cambiarRolUsuario` | Existe | No a ADMIN ni desde ADMIN; nadie se cambia a sí mismo |
| `suspenderUsuario` | Existe | Nadie se suspende a sí mismo; revoca sesiones (ya lo hace) |
| `reactivarUsuario(id)` | Nueva | Vuelve a ACTIVO y limpia el bloqueo por intentos |
| `forzarRecuperacionClave(id)` | Nueva | Sección 3.2: invalida la clave, revoca sesiones y envía el código |

**Auditoría:** tabla append-only `auditoria_usuarios` (`actor_id`, `accion`, `usuario_id`, `antes`, `despues`, `en`),
sin datos personales. La escribe AUTH en la misma transacción que el cambio. Se consulta con
`auditoriaUsuario(id)` en el detalle del panel.

**Web:** la ruta `/admin/usuarios` (`roles: ['ADMIN']` en `pantallas.ts`, que el guardia ya respeta) muestra:

- Tabla paginada con filtros por rol y estado y búsqueda por alias.
- Alta de usuario (modal, con correo) y detalle con cambiar rol, suspender/reactivar, poner correo y forzar la
  recuperación, siempre con confirmación. El panel nunca muestra ni pide claves de otros.
- Historial de cambios.

Hace falta diseño en Figma; mientras tanto se arma con los componentes `Sol*` existentes.

**Semilla (A-6):** `admin.solventa` se queda en BACKOFFICE (lo usan Newman y las E2E) y se agrega
`admin.plataforma` con rol ADMIN y correo `admin.plataforma@solventa.local` (buzón del stub) en `docker-compose.yml`.
En staging, un Cloud Run Job de la imagen de AUTH (`crear_usuario`, idempotente) lo crea con la clave de
`clave-admin-inicial` (Secret Manager; AUTH guarda solo el hash) y `debe_cambiar_clave`. Terraform crea el secreto vacío
y el equipo carga la versión, como `clave-firma-socios`.

## 5. Orden de trabajo y estimación

| # | Bloque | Backend | Web | Móvil |
|---|---|---|---|---|
| 1 | Ingreso sin selector | ½ día (contrato y BFF) | 1 día | — |
| 2 | Recuperación por correo (todos los roles) | 2½ días (AUTH `0011`, correo del personal, BFF, plantilla en ACL y stub, gateway) | 1½ días | 1 día |
| 3 | Rol ADMIN + panel | 2½ días (rol, operaciones, auditoría, transición) | 2½ días | — |
| 4 | Recuperación forzada, semilla del ADMIN y `cambiarClave` | 1 día (más el Job y el secreto en `infra/`) | 1 día | ½ día (`cambiarClave` en perfil) |

Se puede liberar por bloques: el 1 no depende de nada; el 2 y el 3 comparten la migración `0011`, así que van en la misma
liberación o se parte en `0011` y `0012`.

## 6. Verificación

- pytest en AUTH y BFF por regla: sin enumeración (misma respuesta y tiempo parecido con y sin cuenta), código vencido,
  usado o con intentos agotados, sesiones revocadas al restablecer, recuperación forzada que deja inservible la clave
  vieja, cuenta sin correo, ADMIN que intenta suspenderse o promover a ADMIN, semilla idempotente del ADMIN, y un
  BACKOFFICE que pierde acceso al final de la transición.
- `bash scripts/verificar-migraciones.sh auth` (con `downgrade`).
- Newman nueva `acceso-admin` por el gateway: ingreso de los cuatro roles sin `rol`; recuperación completa con el buzón
  del stub de notificaciones; panel de punta a punta; y permisos.
- E2E de Cypress en la web: ingreso sin chips, recuperación con el buzón simulado (`/dev/correo`) y panel.
- Seguridad: ZAP sobre las dos mutaciones públicas y el tope por IP del gateway.

## 7. Documentos que actualiza

`contracts/graphql/schema.graphql` y `CHANGELOG.md` (v0.36), `services/auth/README.md`, `services/bff/README.md`,
`tests/newman/README.md`, `PROJECT_STATUS.md` y `CLAUDE.md` (estado por historia). En la web: su
`docs/plan-...` gemelo y Figma G-01.
