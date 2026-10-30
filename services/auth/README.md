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
| `POST` | `/sesiones` `{usuario, clave}` | `{token, expira_en, usuario}` (con el `nombre` y el `documento_enmascarado` propios, o `null`; el JWT no los lleva), o `401` con mensaje genérico |
| `POST` | `/usuarios` `{usuario, clave, rol, nombre?, correo?, telefono?, documento_identidad?}` | el usuario (sin hash, nombre, contacto ni documento; con `tiene_contacto`); `400` alias, clave, nombre, contacto o documento inválidos (el nombre se admite en cualquier rol; el contacto y el documento solo en CLIENTE, y **un CLIENTE exige documento y correo**, W03-27); `409` alias repetido o documento de otro CLIENTE |
| `POST` | `/usuarios/nombres` `{ids}` | `{id: nombre}` de los CLIENTE pedidos (hasta 200; ids en el cuerpo). Dato personal: solo lo llama el BFF para un BACKOFFICE |
| `GET` | `/usuarios?rol=` | lista de usuarios (el `nombre` siempre en `null`) |
| `GET` | `/usuarios/{id}` | el usuario con su `nombre` y su `documento_enmascarado` (`•••• 4050`; el BFF solo lo llama con el id del propio usuario, para `yo`), o `404` |
| `GET` | `/usuarios/{id}/identidad` | `{titular_id, documento}` **en claro** de un CLIENTE con la identidad verificada, o `404`. Dato personal: solo lo llama el BFF con el id del token, para cotizar sin pedirle la cédula (W03-34) y autorizar por titular (W03-33); nunca se loguea |
| `POST` | `/usuarios/{id}/rol` `{rol}` | el usuario con el rol nuevo, o `404` |
| `POST` | `/usuarios/{id}/suspender` | baja lógica (`estado = SUSPENDIDO`), o `404` |
| `POST` | `/usuarios/{id}/contacto` `{correo?, telefono?}` | el usuario con `tiene_contacto` (nunca el contacto). Lo que no llega se conserva; `400` si no llega ninguno, es inválido o el usuario no es CLIENTE; `404` |
| `GET` | `/usuarios/{id}/contacto` | `{correo, telefono}` **en claro** de un CLIENTE (`null` en lo que no tiene), o `404` si no existe o no es CLIENTE. Dato personal: solo lo llama POLICY, para enviarle el PDF de su póliza; nunca se loguea |

- Clave: hash **argon2id** (`argon2-cffi`), verificación en tiempo constante; bloqueo de 15 min tras 5 fallos.
- JWT **HS256** (`pyjwt`), 60 min, con `sub`, `rol`, `scopes`, `iat`, `exp` y `jti`. Secreto `JWT_SECRETO`
  (`clave-jwt` en Secret Manager, el mismo que lee el BFF). En Cloud Run no arranca con el valor local, el marcador de
  Terraform ni uno de menos de 32 bytes.
- El alias no puede ser un correo ni un documento. Los datos personales son el **nombre** de cualquier rol (KAN-28) y el **correo y teléfono** de los CLIENTE (HU-W03, bloque 8; migración `0007`, opcionales): van cifrados por campo (AES-256-GCM con un dato asociado, `app/cifrado.py`). El nombre usa el id del usuario; el correo y el teléfono usan `correo:<id>` y `telefono:<id>`, así que un cifrado movido de una columna a otra no se descifra. El correo se guarda en minúsculas (solo se revisa su forma, no que exista) y el teléfono solo con dígitos y un `+` opcional (7 a 15 dígitos). Solo los CLIENTE tienen contacto y solo POLICY puede leerlo.
- **Documento y titular del CLIENTE (W03-27, migración `0008`):** `crearUsuario` de un CLIENTE exige documento (5 a 20
  letras o números, en mayúsculas) y correo. Se guarda cifrado (`documento:<id>`) y como `titular_id` =
  `HMAC-SHA256(CLIENTE_ID_CLAVE, documento)`, **la misma clave y la misma normalización del BFF**, así que coincide con
  el titular del consentimiento y con el `cliente_id` de las pólizas de POLICY. Lo crea un BACKOFFICE, que verifica la
  identidad: por eso el titular se fija al crear (W03-36; la otra vía es la verificación de M01). Único: un documento,
  un CLIENTE. En Cloud Run no arranca sin `CLIENTE_ID_CLAVE` (secreto `clave-cliente-id`, el mismo del BFF).
  - **Cifrado por sobre (envelope encryption) con KMS (HA-SEG-002, EC022):** si se configuran `KMS_KEY_NAME` y `CIFRADO_CLAVE_ENVUELTA` (DEK envuelta en base64 desde Secret Manager), AUTH desenvuelve la DEK al iniciar mediante Cloud KMS (`DesenvolvedorGoogleKms`). Si la versión de llave en KMS está deshabilitada o inaccesible, falla de inmediato con `ErrorDeCifrado` sin degradar a texto plano ni clave local.
  - **Fallback local:** sin configuración KMS, usa `CIFRADO_CLAVE` (local no secreta; en Cloud Run exige sobre KMS válido o `CIFRADO_CLAVE` de producción). Rotarla sin re-cifrar deja los nombres ilegibles.
- Primer BACKOFFICE: `CLAVE_INICIAL=... python -m app.crear_usuario --usuario <alias> --rol BACKOFFICE` (idempotente).
  En compose lo hace la tarea `crear-backoffice` con credenciales **solo locales**: `admin.solventa` / `admin-local-solventa`.
- Un CLIENTE, un ASESOR y un operador de prueba, por el BFF: `python scripts/crear_usuarios.py`. El primer BACKOFFICE de
  staging se crea directo en AUTH con `--auth <url-de-auth>` (ver `infra/README.md`, «Usuarios de staging»).
- Logs: solo el id opaco del usuario; nunca el alias, la clave ni el token.

## Cuentas del onboarding del móvil (HU-M01)

- `POST /usuarios/onboarding {correo, clave}`: CLIENTE en `PENDIENTE_VERIFICACION` (M01-05b). El alias es opaco (`c-…`):
  el cliente entra con su **correo**, que se guarda cifrado (`correo_cifrado`, sirve también para el aviso de póliza) y se
  busca por su HMAC (`correo_hmac`, con `CLIENTE_ID_CLAVE`). Un correo, una cuenta (`409`).
- `POST /sesiones {usuario, clave, canal}`: `usuario` puede ser el correo. Una cuenta sin verificar (`PENDIENTE_VERIFICACION`
  o `RECHAZADA`) recibe en `canal = app` un token con el único scope `verificacion-pendiente`, y en cualquier otro canal
  `403 VERIFICACION_PENDIENTE` (solo con la clave correcta; no cuenta como intento fallido).
- `POST /usuarios/{id}/verificacion {aprobada, nombre?, documento?}`: lo llama el servicio `onboarding` al cerrar. ACTIVO
  con el nombre y el documento leídos (cifrados) y su `titular_id` (W03-36, liga las pólizas previas), o RECHAZADA.
  Idempotente; `404` si la cuenta no está pendiente; `409` si el documento ya es de otra cuenta.
- `POST /sesiones/renovar {usuario_id, expira_en}`: `actualizarSesion`. Un token con los scopes del estado actual y **la
  misma expiración** (no alarga la sesión). El BFF lo llama con un token que ya validó.

## Socios de distribución (HU-W07)

Endpoints internos, como el resto: solo los llama el BFF, que ya validó el token del socio. Plan:
`docs/plan-completar-hu-w07.md`.

- **Alta sin API (W07-21):** `python -m app.crear_socio --id … --nombre … --tasa-comision 0.10
  --presupuesto-cotizaciones N --presupuesto-cotizaciones-pruebas M`. Idempotente: fija el presupuesto y crea la
  credencial de cada entorno solo si no hay una vigente. Las claves al azar se imprimen una vez por la salida
  estándar; `SOCIO_CLIENT_ID_<ENTORNO>` y `SOCIO_CLAVE_<ENTORNO>` las fijan. En compose, `crear-socios-demo` crea
  `socio-demo-1` y `socio-demo-2` con claves **solo locales** (el 2 tiene 3 cotizaciones de producción, para el 429).
- `POST /socios/tokens {client_id, clave}`: JWT **ES256** de 15 min (`iss=solventa-auth`, `aud=solventa-socios`,
  `rol=SOCIO`, `entorno`). `401` genérico; 5 fallos seguidos bloquean la credencial 15 min.
  `GET /.well-known/jwks.json`: la clave pública, para el API Gateway y el BFF.
- `GET /socios/{id}` (con la `tasa_comision`), `POST /socios/nombres {ids}`, `GET /socios/{id}/credenciales` (la clave
  en claro, para el portal) y `POST /socios/{id}/credenciales/{entorno}/rotar`.
- `POST /socios/{id}/presupuesto/{entorno}/cotizaciones/reservar` (`429` si se agotó; un solo `UPDATE` atómico) y
  `…/devolver` (la unidad de una cotización que falló por un error nuestro).
- `POST /socios/{id}/consumos` (append-only, sin datos personales) y `GET /socios/{id}/consumo?entorno=&desde=&hasta=`.
- **Secretos:** la clave del socio se guarda cifrada (AES-GCM, contexto = id del socio). La clave de firma llega en
  `SOCIOS_CLAVE_FIRMA` (DER PKCS#8 en base64 o PEM); en local hay una fija y pública, como `JWT_SECRETO`, y en Cloud
  Run sale de Secret Manager (`clave-firma-socios`) y no arranca con la local (W07-26).

## Operación

- Puerto local: `8001`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-auth .`

## Migraciones

PostgreSQL `auth` (este servicio es su único escritor). Alembic con SQL explícito, sin ORM; el esquema lo definen las migraciones.

- Aplicar: `alembic upgrade head` (necesita `DATABASE_URL`). En `docker compose` lo hace `migrar-auth` antes de arrancar el servicio; en staging, el Cloud Run Job `migrar-auth` con la misma imagen.
- Nueva migración: `alembic revision -m "texto" --rev-id 0002`, escribir `upgrade` **y** `downgrade`, y verificar con `bash ../../scripts/verificar-migraciones.sh auth` (una sola cabeza; subir, bajar a base y subir).
- Compatibles hacia atrás: agregar sí; renombrar o borrar en dos despliegues (ADR-07). Nunca edites una migración ya integrada a `main`.
- `0002_usuarios` crea la tabla de usuarios (HU-M02), `0004_consentimientos` la de consentimiento (append-only, T-W01-4) y `0005_consentimiento_titular_y_vencimiento` agrega `titular_id`, `tipo_dato`, `fuente_datos`, `canal` y `vence_en`. `0008_titular_y_documento` agrega a `usuarios` el `titular_id` (único) y el `documento_cifrado` del CLIENTE (W03-27); los usuarios anteriores quedan sin ellos y los de prueba se recrean. `0009_cuentas_onboarding` (HU-M01) amplía los estados con `PENDIENTE_VERIFICACION` y `RECHAZADA` y agrega `correo_hmac` (login por correo). `0010_socios` (HU-W07) crea `socios`, `credenciales_socio`, `presupuestos_socio` y `consumos_socio` (append-only), y suma `SOCIO` a `ck_consentimientos_actor_rol`.
