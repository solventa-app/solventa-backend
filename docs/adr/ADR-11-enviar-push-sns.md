# ADR-11 — `enviar-push`: función dedicada para el envío por SNS

- **Estado:** propuesto. Verificado en local (42 pruebas, `SNS_MODO=stub`); en staging sin probar (Terraform del
  tema, el rol de AWS y la Platform Application quedan para una fase siguiente).
- **Fecha:** 2026-11-11
- **Historia:** KAN-259 (HU-M04, notificaciones push). Fase 2 del plan: la función que de verdad manda el push,
  después de la Fase 1 (AUTH guarda el dispositivo y la preferencia; D3/D4, sesión única).

## Contexto

AUTH ya sabe a qué dispositivo mandarle un push a cada cliente (Fase 1). Falta quien reciba el aviso de negocio
("liquidé esta indemnización", "cerré esta sesión en otro teléfono") y lo convierta en un push real contra AWS SNS
(FCM detrás). Poner ese salto a AWS dentro de RISK o CLAIMS mezclaría un contacto con un proveedor externo con la
lógica de negocio del servicio, y D7 ya exige una forma concreta (y poco común en el resto del repo) de llegar a AWS
sin guardar llaves.

## Decisión

1. **Un servicio nuevo, `enviar-push`** (D1), dedicado solo a esto. **Sin arquitectura hexagonal** (regla 3): no es
   el límite anticorrupción con proveedores externos (ese sigue siendo `acl-worker`), es el contacto simple y
   dedicado que el Planning aprobó para este caso.
2. **Dos puertas.** `POST /v1/push` valida y publica `notificacion.push-solicitada` en el tema real de Pub/Sub
   `notificaciones-push` (D2) para quien prefiere no hablar de Pub/Sub; D10 (solo cuentas de servicio internas) se
   exige por IAM de Cloud Run, no en el código. `POST /eventos` es donde entrega la suscripción push de Pub/Sub (y
   también acepta el evento directo, para pruebas o un productor que publica él mismo en el tema).
3. **Sin ARN de SNS guardado en ninguna base.** `sns:CreatePlatformEndpoint` es idempotente por token: AWS devuelve
   el mismo `EndpointArn` si ya existe con los mismos atributos. `enviar-push` lo resuelve en cada envío
   (`app/sns.py`) en vez de pedirle a AUTH que lo guarde por dispositivo — eso habría significado una migración
   nueva en AUTH y un endpoint interno más solo para esto.
4. **GCP → AWS por federación de identidad, sin llaves guardadas (D7).** La función pide a GCP un token de identidad
   para la audiencia `sts.amazonaws.com` y lo cambia por credenciales temporales con
   `sts:AssumeRoleWithWebIdentity` (`AWS_ROLE_ARN`). Nunca hay `aws_access_key_id`/`aws_secret_access_key` en el
   entorno ni en Secret Manager. Las credenciales se cachean 50 minutos.
5. **Un fallo de negocio nunca se reintenta** (regla 4, CA-M04-07): sin dispositivo activo, push apagado, plantilla
   desconocida o token inválido terminan el procesamiento del evento con un resultado (`SIN_DESTINO`,
   `PUSH_DESHABILITADO`, `PLANTILLA_DESCONOCIDA`, `TOKEN_INVALIDO`) y 200. Solo AUTH caído o SNS caído propagan un
   503 para que Pub/Sub reintente con su backoff.
6. **Las plantillas son un catálogo (`contracts/plantillas-push.json`), no texto libre del productor.** El evento
   solo manda la clave y las variables; el título y el cuerpo los decide este contrato, igual que
   `contracts/fuentes.json` o `contracts/tipos-archivo.json` para sus propios catálogos.
7. **`SNS_MODO=stub`** (local, CI, demo sin AWS real) registra en memoria lo "enviado"; `SNS_MODO=real` usa `boto3`.

## Consecuencias

- Un servicio más en el manifiesto, sin base de datos propia y sin migración.
- Ningún cambio en AUTH para esta fase (ni migración ni endpoint nuevo): solo usa los que ya expuso la Fase 1
  (`GET /interno/destino-push/{usuarioId}`, `POST /interno/dispositivos-push/{id}/token-invalido`).
- Queda pendiente de una fase siguiente (Fase 7 del plan): el Terraform del tema `notificaciones-push`, el rol de
  AWS y su política de confianza (OIDC de GCP), y el paso manual de la Platform Application de SNS para FCM.
- Nadie en producto publica `notificacion.push-solicitada` todavía (eso es RISK/CLAIMS en fases siguientes del
  plan); por ahora el contrato y el servicio quedan listos y probados con el evento construido a mano.
