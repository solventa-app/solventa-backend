# auth

AUTH: consentimiento, usuarios y sesiones.

## Responsabilidad

AUTH es el **único escritor del almacén de identidad** (PostgreSQL, regla de escritor único). En el Sprint 1:

- Registro **append-only** de consentimientos (actor, finalidad, versión del texto, sello de tiempo) y de su revocación (HA-SEG-001, EC023).
- Validación de consentimiento vigente antes de consultar cualquier fuente (CA-W01-01/02).
- Al revocar, la siguiente solicitud de oferta se rechaza de inmediato; la caché del ACL expira sola en ≤ 5 min
  (TTL de 300 s, CA-W01-08).
- Usuarios (`CLIENTE`, `ASESOR`, `BACKOFFICE`) e inicio de sesión con JWT (HU-M02, versión simple para web; ver
  [`docs/autenticacion-usuarios.md`](../../docs/autenticacion-usuarios.md)). Rotación y refresh quedan pendientes (HA-SEG-005).

Eventos que emitirá: `consentimiento.registrado`, `consentimiento.revocado` (ver `contracts/events/`). **Pendiente:**
no hay Pub/Sub todavía, así que hoy no se emiten.

## Consentimiento: tabla append-only en PostgreSQL (T-W01-4, KAN-24)

Tabla `consentimientos` de la base `auth` (migraciones `0004_consentimientos` y `0005_consentimiento_titular_y_vencimiento`, `app/consentimientos.py`):

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/consentimientos` `{finalidad, version_texto, titular_id?, documento_identidad?, tipo_dato?, fuente_datos?, canal?, actor?}` | `{id, finalidad, version_texto, otorgado_en, vence_en, revocado_en, estado, vigente, titular_id, tipo_dato, fuente_datos, canal, actor}` |
| `GET` | `/consentimientos/{id}` | el consentimiento (con `estado` y `vigente` calculados al leer), o `404` |
| `POST` | `/consentimientos/{id}/revocar` | marca `revocado_en` (append-only: nunca borra); idempotente; `404` si no existe |

- **Titular opaco (`titular_id`):** si la solicitud envía `documento_identidad` y no `titular_id`, AUTH deriva `titular_id` con `HMAC-SHA256(CLIENTE_ID_CLAVE, documento_identidad)` (`app/identificadores.py`). El documento en claro nunca se persiste ni se registra en logs.
- **Vigencia de 30 días (`vence_en`):** al registrar, `vence_en = otorgado_en + CONSENTIMIENTO_VIGENCIA_DIAS` (por defecto 30 días, CA-W01-09). El estado (`VIGENTE`, `VENCIDO` o `REVOCADO`) y `vigente` se calculan al leer sin mutar la fila cuando vence.
- **Append-only en la base, no solo en el código:** un trigger (`trg_consentimientos_append_only`) rechaza cualquier `DELETE` y cualquier `UPDATE` que no sea fijar `revocado_en` por primera vez (protege también `titular_id`, `tipo_dato`, `fuente_datos`, `canal` y `vence_en`).
- `actor` (opcional) es `{rol, usuario_id}` del usuario autenticado que otorgó el consentimiento; lo envía el BFF (CA-W01-02, CA-W01-11). Sin sesión queda en `null`.

## Usuarios y sesiones (HU-M02, PostgreSQL)

Tabla `usuarios` en la base `auth` (migración `0002_usuarios`; mismo Postgres de siempre). Endpoints **internos**: solo
los llama el BFF, que ya validó el token y el rol.

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/sesiones` `{usuario, clave}` | `{token, expira_en, usuario}` (con el `nombre` propio, o `null`; el JWT no lo lleva), o `401` con mensaje genérico |
| `POST` | `/usuarios` `{usuario, clave, rol, nombre?, correo?, telefono?}` | el usuario (sin hash, nombre ni contacto; con `tiene_contacto`); `400` alias, clave, nombre o contacto inválidos (el nombre se admite en cualquier rol; el contacto solo en CLIENTE); `409` alias repetido |
| `POST` | `/usuarios/nombres` `{ids}` | `{id: nombre}` de los CLIENTE pedidos (hasta 200; ids en el cuerpo). Dato personal: solo lo llama el BFF para un BACKOFFICE |
| `GET` | `/usuarios?rol=` | lista de usuarios (el `nombre` siempre en `null`) |
| `GET` | `/usuarios/{id}` | el usuario con su `nombre` (el BFF solo lo llama con el id del propio usuario, para `yo`), o `404` |
| `POST` | `/usuarios/{id}/rol` `{rol}` | el usuario con el rol nuevo, o `404` |
| `POST` | `/usuarios/{id}/suspender` | baja lógica (`estado = SUSPENDIDO`), o `404` |
| `POST` | `/usuarios/{id}/contacto` `{correo?, telefono?}` | el usuario con `tiene_contacto` (nunca el contacto). Lo que no llega se conserva; `400` si no llega ninguno, es inválido o el usuario no es CLIENTE; `404` |
| `GET` | `/usuarios/{id}/contacto` | `{correo, telefono}` **en claro** de un CLIENTE (`null` en lo que no tiene), o `404` si no existe o no es CLIENTE. Dato personal: solo lo llama POLICY, para enviarle el PDF de su póliza; nunca se loguea |

- Clave: hash **argon2id** (`argon2-cffi`), verificación en tiempo constante; bloqueo de 15 min tras 5 fallos.
- JWT **HS256** (`pyjwt`), 60 min, con `sub`, `rol`, `scopes`, `iat`, `exp` y `jti`. Secreto `JWT_SECRETO`
  (`clave-jwt` en Secret Manager, el mismo que lee el BFF). En Cloud Run no arranca con el valor local, el marcador de
  Terraform ni uno de menos de 32 bytes.
- El alias no puede ser un correo ni un documento. Los datos personales son el **nombre** de cualquier rol (KAN-28) y el **correo y teléfono** de los CLIENTE (HU-W03, bloque 8; migración `0007`, opcionales): van cifrados por campo (AES-256-GCM con un dato asociado, `app/cifrado.py`). El nombre usa el id del usuario; el correo y el teléfono usan `correo:<id>` y `telefono:<id>`, así que un cifrado movido de una columna a otra no se descifra. El correo se guarda en minúsculas (solo se revisa su forma, no que exista) y el teléfono solo con dígitos y un `+` opcional (7 a 15 dígitos). Solo los CLIENTE tienen contacto y solo POLICY puede leerlo.
  - **Cifrado por sobre (envelope encryption) con KMS (HA-SEG-002, EC022):** si se configuran `KMS_KEY_NAME` y `CIFRADO_CLAVE_ENVUELTA` (DEK envuelta en base64 desde Secret Manager), AUTH desenvuelve la DEK al iniciar mediante Cloud KMS (`DesenvolvedorGoogleKms`). Si la versión de llave en KMS está deshabilitada o inaccesible, falla de inmediato con `ErrorDeCifrado` sin degradar a texto plano ni clave local.
  - **Fallback local:** sin configuración KMS, usa `CIFRADO_CLAVE` (local no secreta; en Cloud Run exige sobre KMS válido o `CIFRADO_CLAVE` de producción). Rotarla sin re-cifrar deja los nombres ilegibles.
- Primer BACKOFFICE: `CLAVE_INICIAL=... python -m app.crear_usuario --usuario <alias> --rol BACKOFFICE` (idempotente).
  En compose lo hace la tarea `crear-backoffice` con credenciales **solo locales**: `admin.solventa` / `admin-local-solventa`.
- Un CLIENTE, un ASESOR y un operador de prueba, por el BFF: `python scripts/crear_usuarios.py`. El primer BACKOFFICE de
  staging se crea directo en AUTH con `--auth <url-de-auth>` (ver `infra/README.md`, «Usuarios de staging»).
- Logs: solo el id opaco del usuario; nunca el alias, la clave ni el token.

## Operación

- Puerto local: `8001`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-auth .`

## Migraciones

PostgreSQL `auth` (este servicio es su único escritor). Alembic con SQL explícito, sin ORM; el esquema lo definen las migraciones.

- Aplicar: `alembic upgrade head` (necesita `DATABASE_URL`). En `docker compose` lo hace `migrar-auth` antes de arrancar el servicio; en staging, el Cloud Run Job `migrar-auth` con la misma imagen.
- Nueva migración: `alembic revision -m "texto" --rev-id 0002`, escribir `upgrade` **y** `downgrade`, y verificar con `bash ../../scripts/verificar-migraciones.sh auth` (una sola cabeza; subir, bajar a base y subir).
- Compatibles hacia atrás: agregar sí; renombrar o borrar en dos despliegues (ADR-07). Nunca edites una migración ya integrada a `main`.
- `0002_usuarios` crea la tabla de usuarios (HU-M02), `0004_consentimientos` la de consentimiento (append-only, T-W01-4) y `0005_consentimiento_titular_y_vencimiento` agrega `titular_id`, `tipo_dato`, `fuente_datos`, `canal` y `vence_en`.
