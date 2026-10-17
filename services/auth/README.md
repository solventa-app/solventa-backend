# auth

AUTH: consentimiento, usuarios y sesiones.

## Responsabilidad

AUTH es el **único escritor del almacén de identidad** (PostgreSQL, regla de escritor único). En el Sprint 1:

- Registro **append-only** de consentimientos (actor, finalidad, versión del texto, sello de tiempo) y de su revocación (HA-SEG-001, EC023).
- Validación de consentimiento vigente antes de consultar cualquier fuente (CA-W01-01/02).
- Al revocar, invalida la caché del cliente en ≤ 5 min (CA-W01-08).
- Usuarios (`CLIENTE`, `ASESOR`, `BACKOFFICE`) e inicio de sesión con JWT (HU-M02, versión simple para web; ver
  [`docs/autenticacion-usuarios.md`](../../docs/autenticacion-usuarios.md)). Rotación y refresh quedan pendientes (HA-SEG-005).

Eventos que emite: `consentimiento.registrado`, `consentimiento.revocado` (ver `contracts/events/`).

## Consentimiento: MOCK en memoria (hasta T-W01-4)

Los endpoints ya tienen su forma definitiva; lo que es provisional es el almacén (`app/consentimientos.py`, en memoria):

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/consentimientos` `{finalidad, version_texto, actor?}` | `{id, finalidad, version_texto, otorgado_en, revocado_en, vigente, actor}` |
| `GET` | `/consentimientos/{id}` | el consentimiento, o `404` |
| `POST` | `/consentimientos/{id}/revocar` | marca `revocado_en` (append-only: nunca borra); idempotente; `404` si no existe |

Limitaciones del mock: el estado se pierde al reiniciar, **no se comparte entre instancias de Cloud Run**
(`max_instancias` = 2 en staging) y no emite eventos. T-W01-4 lo reemplaza con la tabla de la migración `0002`
sin cambiar las rutas, de modo que el BFF no se toca.

`actor` (opcional) es `{rol, usuario_id}` del usuario autenticado que otorgó el consentimiento; lo envía el BFF
(CA-W01-02, CA-W01-11). Sin sesión queda en `null`.

## Usuarios y sesiones (HU-M02, PostgreSQL)

Tabla `usuarios` en la base `auth` (migración `0002_usuarios`; mismo Postgres de siempre). Endpoints **internos**: solo
los llama el BFF, que ya validó el token y el rol.

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/sesiones` `{usuario, clave}` | `{token, expira_en, usuario}`, o `401` con mensaje genérico |
| `POST` | `/usuarios` `{usuario, clave, rol}` | el usuario (sin hash); `400` alias o clave inválidos; `409` alias repetido |
| `GET` | `/usuarios?rol=` | lista de usuarios |
| `GET` | `/usuarios/{id}` | el usuario, o `404` |
| `POST` | `/usuarios/{id}/rol` `{rol}` | el usuario con el rol nuevo, o `404` |
| `POST` | `/usuarios/{id}/suspender` | baja lógica (`estado = SUSPENDIDO`), o `404` |

- Clave: hash **argon2id** (`argon2-cffi`), verificación en tiempo constante; bloqueo de 15 min tras 5 fallos.
- JWT **HS256** (`pyjwt`), 60 min, con `sub`, `rol`, `scopes`, `iat`, `exp` y `jti`. Secreto `JWT_SECRETO`
  (`clave-jwt` en Secret Manager, el mismo que lee el BFF). En Cloud Run no arranca con el valor local, el marcador de
  Terraform ni uno de menos de 32 bytes.
- El alias no puede ser un correo ni un documento: la tabla no guarda datos personales.
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
- `0002_usuarios` crea la tabla de usuarios (HU-M02). La tabla de consentimiento (append-only, T-W01-4) irá en una
  migración posterior (`0003`).
