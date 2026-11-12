# enviar-push

Servicio genérico y reutilizable de push (KAN-259, HU-M04, D1): punto de contacto dedicado con AWS SNS, aprobado por
el Planning como alternativa a meter ese salto dentro de un servicio de negocio. Diseño nuevo de este sprint, sin
origen en un experimento de `solventa-arquitectura`.

## Responsabilidad

Recibe `notificacion.push-solicitada` (por Pub/Sub o HTTP directo), resuelve la plantilla y el destino del usuario en
AUTH, y envía por AWS SNS (FCM/GCM detrás). **Escritor único**: no toca la base de AUTH, solo la consulta y le avisa
cuando un token quedó inválido.

## Dos puertas

- **`POST /v1/push`**: valida la solicitud y publica el evento en el tema de Pub/Sub `notificaciones-push` (202,
  `{"solicitudId"}`). Para quien prefiere no hablar de Pub/Sub directamente. **D10: solo cuentas de servicio internas**
  (Cloud Run IAM; sin chequeo en código, es de infra).
- **`POST /eventos`**: la suscripción push de Pub/Sub entrega aquí cada evento. Acepta el sobre de Pub/Sub
  (`{"message": {"data": "<base64>"}}`) o el evento directo (mismo patrón que `rating/app/main.py`), así que también
  sirve para pruebas o para un productor que publica directo en el tema con este mismo payload.
  - 400 = evento inválido (esquema o sobre malformado): no se reintenta.
  - 503 = AUTH o SNS no respondieron: Pub/Sub reintenta con su backoff.
  - 200 = el evento se resolvió, sea que se envió el push o que se confirmó sin enviar (regla de arquitectura #4:
    sin dispositivo, push apagado, plantilla desconocida o token inválido nunca quedan como algo para reintentar;
    CA-M04-07).

Sin arquitectura hexagonal (regla de arquitectura #3): esta función no es el límite anticorrupción con proveedores
externos (ese es `acl-worker`), solo el contacto dedicado y simple que D1 aprobó.

## Flujo (`POST /eventos`)

1. Decodifica el sobre de Pub/Sub o toma el evento directo.
2. Valida contra `app/contrato/notificacion.push-solicitada.schema.json` (copia sincronizada de `contracts/`, ver
   `scripts/sincronizar_contrato_push.py`).
3. Resuelve el texto de la plantilla (`app/contrato/plantillas-push.json`) con las variables del evento.
4. Pide a AUTH el destino del push: `GET /interno/destino-push/{usuarioId}` → `{dispositivo_id, token, plataforma}`.
   - 404 → `SIN_DESTINO` (sin dispositivo activo).
   - 409 → `PUSH_DESHABILITADO` (el cliente apagó la preferencia).
5. Envía por SNS. Si SNS dice que el token está inválido o deshabilitado, avisa a AUTH
   (`POST /interno/dispositivos-push/{id}/token-invalido`, idempotente) y confirma como `TOKEN_INVALIDO`.
6. Si todo salió bien, `ENVIADO`.

Solo un fallo transitorio (AUTH caído, SNS caído) se propaga como 503 para que Pub/Sub reintente.

## AWS SNS sin ARN guardado (D1/D2/D7)

`create_platform_endpoint` es idempotente por token: AWS devuelve el mismo `EndpointArn` si ya existe con los mismos
atributos. Por eso `enviar-push` **no guarda el ARN en ninguna base**: lo resuelve en cada envío
(`app/sns.py:EnviadorSnsBoto3`). Esto evita una migración nueva en AUTH para esta fase.

**D7 (GCP → AWS sin llaves guardadas)**: la función pide a GCP un token de identidad para la audiencia
`sts.amazonaws.com` y lo cambia por credenciales temporales con `sts:AssumeRoleWithWebIdentity` (`AWS_ROLE_ARN`).
Nada de `aws_access_key_id`/`aws_secret_access_key` en el entorno ni en Secret Manager. Las credenciales se cachean
50 minutos.

`SNS_MODO=stub` (local, CI, demo sin AWS real) usa `EnviadorStub`, que solo registra en memoria lo "enviado".
`SNS_MODO=real` usa `boto3` + SNS de verdad.

## Variables de entorno

| Variable | Default local | Nota |
|---|---|---|
| `AUTH_URL` | `http://localhost:8001` | Privada (OIDC) |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en la nube |
| `SNS_MODO` | `stub` | `stub` o `real` |
| `SNS_REGION` | `us-east-1` | Región de SNS |
| `SNS_PLATFORM_APPLICATION_ARN` | (vacío) | Platform Application de SNS para FCM, ya configurada a mano (paso manual del plan) |
| `AWS_ROLE_ARN` | (vacío) | Rol de AWS a asumir por federación (D7); vacío en local/CI |
| `PUBSUB_TEMA_NOTIFICACIONES_PUSH` | (vacío) | Si está vacío, `POST /v1/push` solo deja el evento en el log (`BusEventosLog`), igual que en RISK/CLAIMS |

## Operación

- Puerto local: `8013`. Salud: `GET /health`. Métricas: `GET /metrics`.
- `disparador_externo` (la suscripción push de Pub/Sub entrega en `/eventos`); no es llamado síncronamente por otro
  servicio de negocio, igual que `consolidador-fuentes`.

## Pruebas

`python -m pytest` desde esta carpeta. Dobles en memoria para AUTH, SNS y el bus de eventos (`tests/fakes.py`), mismo
patrón que el resto de servicios.
