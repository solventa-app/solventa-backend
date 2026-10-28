# bff

BFF GraphQL de Solventa (web y móvil).

## Responsabilidad

BFF único de GraphQL para web y móvil (ADR-01): un solo Cloud Run con el esquema servido en dos rutas por canal
(`/graphql/web` y `/graphql/app`; el canal viaja en el contexto). **No tiene lógica de negocio**: orquesta AUTH,
ACL Worker, RISK y RATING y traduce al contrato de `contracts/graphql/schema.graphql`.

- Historias: HU-W01 (T-W01-7), HU-M02 (autenticación) y HU-W05 (KAN-28, cobros del back-office). La API REST B2B `/v1` llega con HU-W07 (Sprint 2).
- Regla: nunca devuelve un 5xx crudo al cliente; traduce la degradación a oferta preliminar o error con reintento (CA-W01-04/05).
  Los errores internos se enmascaran ("Unexpected error."); solo `ErrorPublico` muestra su mensaje.
- Idioma: con `Accept-Language: en` (lo manda la web según el selector del header) los mensajes para el usuario
  (`mensaje`, `resumen`, factores del score y errores públicos) salen en inglés; sin cabecera, en español. Los
  servicios siguen escribiendo en español y la traducción se hace en la frontera (`app/idioma.py`); un mensaje nuevo
  se agrega a su catálogo `EN`.

## Contrato

`app/esquema.py` (Strawberry) es espejo 1:1 del contrato. `tests/test_esquema_contrato.py` lo compara con
`contracts/graphql/schema.graphql` en ambos sentidos: si difieren, se corrige el código, no el contrato.

| Operación | Estado |
|---|---|
| `registrarConsentimiento` / `revocarConsentimiento` | AUTH (tabla append-only en PostgreSQL con `titular_id`, `vence_en` a 30 días y `estado`; al revocar, si tiene `titular_id` invalida además las fuentes cacheadas en el ACL Worker en segundos — CA-W01-08) |
| `solicitarOferta` | Completo (contrato v0.8): ver el flujo abajo |
| `oferta(id)` | La oferta que RATING guardó al calcularla (HU-W03, CA-W03-01), p. ej. para «ver fuentes». Misma validación de dueño que `aceptarOferta` (un CLIENTE solo ve la que consintió él; un ASESOR, la de su cliente). `null` si venció, no existe o no es suya; si RATING o AUTH no responden, error con reintento (no `null`) |
| `propuesta(id)` | Estado de la propuesta en POLICY. Si no avanza (`EN_REVISION`, `PENDIENTE_PAGO`, `PAGO_RECHAZADO` o `NO_EMITIDA`) trae `motivoEstado`, `mensajeEstado` y `accionSugerida` (CA-W03-06): categorías y textos para el cliente que arma `explicar_estado` en `servicio_suscripcion.py`. El código interno de UNDER o de la pasarela **nunca** sale del BFF. Los textos son una propuesta (W03-15) |
| `contactarAsesor` | **MOCK**: solo confirma (no hay servicio de asesores) |
| `cobros(polizaId, estado)` / `crearCobro` / `reintentarCobro` | Completas (KAN-28), sobre PAYMENTS: solo BACKOFFICE y **siempre con token**. `crearCobro` recibe `idIntentoCobro` del cliente (idempotencia) y devuelve `Cobro` o `ErrorCobro`. `Usuario.nombre` solo lo traen `iniciarSesion` y `yo` (el propio; nunca el JWT). Los cobros traen `idTransaccion` (referencia de la pasarela) y `cliente { id nombre }`: el BFF pide los nombres a AUTH en lotes de 200 y, si AUTH falla, los entrega sin nombre. Tras un reintento, el BFF le avisa a POLICY (`_reconciliar-cobro`, de mejor esfuerzo) por si es el primer cobro de una propuesta de W03: si ya se cobró, POLICY emite la póliza (W03-14); si POLICY no responde, el reintento no falla. El reintento concilia con la pasarela antes de cobrar (ver [`docs/cobros-conciliacion.md`](../../docs/cobros-conciliacion.md)) |
| `iniciarSesion`, `yo`, `usuarios`, `crearUsuario`, `cambiarRolUsuario`, `suspenderUsuario` | Completas (HU-M02), sobre AUTH |
| `actualizarContacto` / `crearUsuario` con `correo` y `telefono` | HU-W03, bloque 8 (contrato v0.16). El CLIENTE deja el correo y el teléfono a los que se le enviará el PDF de su póliza; solo él (`actualizarContacto`, siempre con token, el id sale del token) o un BACKOFFICE al crearlo. El BFF los reenvía a AUTH, que los guarda cifrados: no los guarda, no los registra y nunca los devuelve. `Usuario.tieneContacto` dice si hay, no cuál. Los errores de validación de AUTH salen como `ErrorUsuario`, en el idioma pedido |

## Autenticación y permisos (HU-M02 y API Gateway)

`app/autenticacion_usuarios.py` verifica el JWT (`X-Forwarded-Authorization: Bearer <token>` cuando pasa por GCP API Gateway o `Authorization: Bearer <token>` directo) que emite AUTH, y cada resolutor de
`app/esquema.py` aplica su regla con `exigir_rol`. La matriz de permisos, las decisiones y el paso a paso con ejemplos
están en [`docs/autenticacion-usuarios.md`](../../docs/autenticacion-usuarios.md). Con `AUTENTICACION_USUARIOS=opcional`
(valor actual) las operaciones de antes siguen funcionando sin token; la gestión de usuarios y `yo` siempre lo exigen.

## Flujo de `solicitarOferta` (`app/servicio_oferta.py`)

1. AUTH: consentimiento vigente **antes** de consultar nada (regla #7). Si no existe, fue revocado o venció (`vence_en` > 30 días), o si su `titular_id` no coincide con el `cliente_id` derivado del `documentoIdentidad` de la solicitud → `ErrorOferta` sin reintento con `motivo: CONSENTIMIENTO_NO_VIGENTE`.
2. `cliente_id` opaco = HMAC-SHA256 del documento con `CLIENTE_ID_CLAVE` (`app/identificadores.py`).
3. ACL Worker: las fuentes definidas en `contracts/fuentes.json` (dentro de la imagen, su copia `app/fuentes.json`, idéntica: `scripts/sincronizar_fuentes.py`) en paralelo (enviando `consentimiento_id` y `finalidad`). El ACL ya degrada solo (caché o valor por defecto); si el ACL mismo no
   responde, la fuente va `degradado` y la oferta sale PRELIMINAR.
4. RISK escribe el perfil cifrado, guarda `ScoreCacheado` en Redis y devuelve `operation_time`; RATING calcula la oferta con lectura causal (D-02) e incluye `perfilRiesgo { score nivelRiesgo fechaVigencia }`.
5. Cualquier falla de RISK/RATING → `ErrorOferta` con reintento y `motivo: FUENTES_NO_DISPONIBLES`; `valorCredito` inválido → sin reintento (`motivo: DATOS_INSUFICIENTES`).
   Sin historial crediticio (RATING responde 422 `datos_insuficientes`, CA-W01-05) → `ErrorOferta` con reintento y `motivo: DATOS_INSUFICIENTES`.

Antes del paso 1 se valida la solicitud (mismos límites que el formulario W01-01): documento con al menos 6 dígitos
y máximo 20 caracteres, `valorCredito` decimal mayor que 0, `plazoMeses` de 1 a 60 y `direccionInmueble` no vacía
(máximo 200 caracteres). Si algo falla → `ErrorOferta` sin reintento, sin tocar AUTH ni las fuentes.

`plazoMeses` y `direccionInmueble` viajan a RATING, que los usa en la prima (ver `services/rating/README.md`).
Nunca se loguean (tampoco el documento).

## Variables de entorno

| Variable | Default local | Nota |
|---|---|---|
| `AUTH_URL`, `ACL_URL`, `RISK_URL`, `RATING_URL`, `PAYMENTS_URL`, `POLICY_URL` | `http://localhost:800x` | En Cloud Run las fija Terraform desde `llama` en `.github/servicios.json` |
| `ACL_TIMEOUT_SEGUNDOS` | `1.0` | El ACL corta a los 700 ms; el margen cubre la red |
| `SERVICIOS_TIMEOUT_SEGUNDOS` | `2.0` | AUTH, RISK y RATING |
| `CLIENTE_ID_CLAVE` | `solo-local-no-secreta` | En la nube, secreto `clave-cliente-id`; el BFF no arranca con el valor local ni con el marcador de Terraform |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run: token de identidad del servidor de metadatos, audience = nombre del servicio |
| `JWT_SECRETO` | `solo-local-no-secreta-jwt-solventa-0001` | El mismo que AUTH (HS256). En la nube, secreto `clave-jwt`; no arranca con el valor local, el marcador ni uno de menos de 32 bytes |
| `REDIS_URL` | `redis://localhost:6379/0` | Guarda 60 s el ticket del stream de avisos (`ticketAvisos`); debe ser la misma base que lee el servicio `avisos` (la de POLICY) |
| `AVISOS_URL` | `http://localhost:8011/avisos/stream` | URL pública del stream SSE (fuera del API Gateway) que devuelve `ticketAvisos` |
| `URL_FRONT` | `http://localhost:5173` | Origen del front: a `<URL_FRONT>/pago/retorno/<propuestaId>` regresa el titular desde la pasarela; debe estar en `URLS_RETORNO_PERMITIDAS` de la pasarela |
| `AUTENTICACION_USUARIOS` | `opcional` | `opcional` u `obligatoria` (ver `docs/autenticacion-usuarios.md`) |

## Operación

- Puerto local: `8000`. GraphQL: `POST /graphql/web` y `/graphql/app` (GraphiQL en el navegador). Salud: `GET /health`. Métricas: `GET /metrics`.
- **Firma y pago (HU-W03, v0.18):** `servicio_firma_pago.py` autoriza y traduce; POLICY decide. Las operaciones de la página del
  enlace (`firmaPorEnlace`, `solicitarSubidaFirma`, `firmarPorEnlace`, `iniciarPago`, `confirmarPago`) aceptan **el token del
  enlace** en la cabecera `X-Enlace-Firma` (sin sesión; con él se ignora `propuestaId`) **o la sesión de un CLIENTE** con
  `propuestaId`, y entonces el BFF toma el titular de AUTH (`GET /usuarios/{id}/identidad`) y se lo pasa a POLICY en
  `X-Titular-Id`: el cliente nunca elige de quién es la propuesta. Sin acceso a la propuesta se responde como si no existiera.
  El token no se loguea ni viaja en una URL (a POLICY va por cabecera o en el cuerpo de `/enlaces/resolver`).
  `propuestas` se filtra por el rol del token (CLIENTE por su titular, ASESOR por lo que aceptó, BACKOFFICE todo) y los datos
  personales (`nombreTitular`, `nombreAsesor`) salen solo para ASESOR y BACKOFFICE. `GET /propuestas/{id}/contrato.pdf` entrega
  el contrato a firmar con la misma autorización doble. La cédula de un CLIENTE (W03-34) sale de AUTH en memoria si no la manda,
  y si la manda debe ser la suya. Al revocar un consentimiento se avisa a POLICY (`/propuestas/consentimiento-revocado`) sin
  que una falla suya haga fallar la revocación.
- **PDF de la póliza (HU-W03):** `GET /polizas/{id}/documento` (REST, no GraphQL, porque entrega un archivo). Es el `documentoUrl` de
  `Poliza`. Es un documento con datos personales: exige sesión **siempre** (aunque la autenticación de GraphQL esté en `opcional`),
  solo para CLIENTE y ASESOR, y un CLIENTE solo descarga la suya. `401` sin token o con uno inválido, `403` para otro rol, `404` si
  no existe **o** es de otro (se ve igual), `503` con `Retry-After` si POLICY no responde. El nombre del asegurado lo pide a AUTH
  (por el firmante) solo para el documento y no se loguea; si AUTH falla, el PDF sale con la referencia opaca del titular. Como
  la descarga exige la cabecera `Authorization`, un enlace simple no sirve: el frontend la pide con `fetch` y muestra el
  archivo. Pasa por el API Gateway (`infra/gateway/`).
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta). Sin red: dobles en `tests/fakes.py`.
- Humo de punta a punta (el mismo que corre el CI):
  ```bash
  docker compose --profile servicios up -d --build --wait
  python scripts/humo_bff.py          # desde la raíz del repo
  ```
  Inicia sesión como BACKOFFICE, crea un ASESOR y, con su token, recorre consentimiento → oferta COMPLETA →
  PRELIMINAR (Open Finance lento) → `ErrorOferta` tras revocar; además comprueba el actor y un rechazo por rol.
- Imagen: `docker build -t solventa-bff .`
