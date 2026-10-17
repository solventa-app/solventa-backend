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

## Consentimiento: tabla append-only en PostgreSQL (T-W01-4)

Tabla `consentimientos` de la base `auth` (migración `0004_consentimientos`, `app/consentimientos.py`):

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/consentimientos` `{finalidad, version_texto, actor?}` | `{id, finalidad, version_texto, otorgado_en, revocado_en, vigente, actor}` |
| `GET` | `/consentimientos/{id}` | el consentimiento, o `404` |
| `POST` | `/consentimientos/{id}/revocar` | marca `revocado_en` (append-only: nunca borra); idempotente; `404` si no existe |

**Append-only en la base, no solo en el código:** un trigger (`trg_consentimientos_append_only`) rechaza cualquier
`DELETE` y cualquier `UPDATE` que no sea fijar `revocado_en` por primera vez. Verificado en vivo: borrar, re-revocar o
cambiar la finalidad fallan con `consentimientos es append-only`, y un consentimiento sobrevive a reiniciar AUTH.
Las rutas son las mismas que tenía el mock en memoria (hoy un doble en `tests/fakes.py`), así que el BFF no cambió.

`actor` (opcional) es `{rol, usuario_id}` del usuario autenticado que otorgó el consentimiento; lo envía el BFF
(CA-W01-02, CA-W01-11). Sin sesión queda en `null`.

## Usuarios y sesiones (HU-M02, PostgreSQL)

Tabla `usuarios` en la base `auth` (migración `0002_usuarios`; mismo Postgres de siempre). Endpoints **internos**: solo
los llama el BFF, que ya validó el token y el rol.

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/sesiones` `{usuario, clave}` | `{token, expira_en, usuario}`, o `401` con mensaje genérico |
| `POST` | `/usuarios` `{usuario, clave, rol, nombre?}` | el usuario (sin hash ni nombre); `400` alias, clave o nombre inválidos (el nombre es solo de CLIENTE); `409` alias repetido |
| `POST` | `/usuarios/nombres` `{ids}` | `{id: nombre}` de los CLIENTE pedidos (hasta 200; ids en el cuerpo). Dato personal: solo lo llama el BFF para un BACKOFFICE |
| `GET` | `/usuarios?rol=` | lista de usuarios |
| `GET` | `/usuarios/{id}` | el usuario, o `404` |
| `POST` | `/usuarios/{id}/rol` `{rol}` | el usuario con el rol nuevo, o `404` |
| `POST` | `/usuarios/{id}/suspender` | baja lógica (`estado = SUSPENDIDO`), o `404` |

- Clave: hash **argon2id** (`argon2-cffi`), verificación en tiempo constante; bloqueo de 15 min tras 5 fallos.
- JWT **HS256** (`pyjwt`), 60 min, con `sub`, `rol`, `scopes`, `iat`, `exp` y `jti`. Secreto `JWT_SECRETO`
  (`clave-jwt` en Secret Manager, el mismo que lee el BFF). En Cloud Run no arranca con el valor local, el marcador de
  Terraform ni uno de menos de 32 bytes.
- El alias no puede ser un correo ni un documento. El único dato personal es el **nombre del CLIENTE** (KAN-28): va cifrado por campo (AES-256-GCM con el id del usuario como dato asociado, `app/cifrado.py`), con la llave `CIFRADO_CLAVE` (local no secreta; en la nube, `clave-cifrado`, y no arranca con la local). Rotarla sin re-cifrar deja los nombres ilegibles.
- Primer BACKOFFICE: `CLAVE_INICIAL=... python -m app.crear_usuario --usuario <alias> --rol BACKOFFICE` (idempotente).
  En compose lo hace la tarea `crear-backoffice` con credenciales **solo locales**: `admin.solventa` / `admin-local-solventa`.
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
- `0002_usuarios` crea la tabla de usuarios (HU-M02) y `0004_consentimientos` la de consentimiento (append-only,
  T-W01-4).
