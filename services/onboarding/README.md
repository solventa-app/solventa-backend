# onboarding

Recorrido de verificación de identidad del cliente nuevo en el móvil (HU-M01 / KAN-31). FastAPI en capas simples,
PostgreSQL + Alembic, puerto **8010**. Plan: `plan-almacenamiento-y-hu-m01.md` (bloque 3).

## Responsabilidad

- Único escritor de su base (regla 1): `sesiones_onboarding`, `datos_extraidos` (cifrados por campo),
  `verificaciones` y `eventos_onboarding` (append-only en la base).
- Pide al servicio `almacenamiento` una URL firmada por pieza (anverso, reverso, rostro, video), a nombre de
  `SERVICIO onboarding` y con el consentimiento `onboarding-biometrico`. Los bytes van directo de la app al bucket.
- Manda las imágenes al ACL Worker (`POST /identidad/verificar`, Didit detrás) y aplica las reglas de aprobación.
- Publica `onboarding.verificado` y `onboarding.rechazado` (ids opacos y códigos; esquemas en
  `contracts/events/onboarding.*.schema.json`). Sin tema de Pub/Sub, se loguean.
- Lleva el resultado a la cuenta de AUTH (`POST /usuarios/{id}/verificacion`): VERIFICADA la activa con el nombre y el
  documento leídos (AUTH fija el `titular_id`); RECHAZADA sin intentos la rechaza. Un rechazo con intentos no toca la
  cuenta. Se hace al cerrar, al ligar la cuenta (si ya cerró) y cuando la app consulta `miVerificacion`, hasta que
  `cuenta_notificada_en` queda puesta (migración `0002`). Si AUTH responde que el documento ya es de otra cuenta (p. ej.
  una que creó un asesor), la sesión pasa a RECHAZADA `DOCUMENTO_YA_REGISTRADO` y la cuenta a RECHAZADA.

## Reglas de aprobación

| Resultado | Estado | Motivo |
|---|---|---|
| Documento rechazado por el proveedor | RECHAZADA | `ILEGIBLE` |
| Prueba de vida fallida | RECHAZADA | `SUPLANTACION` |
| El rostro no coincide | RECHAZADA | `ROSTRO_NO_COINCIDE` |
| Sin número o sin fecha de nacimiento | RECHAZADA | `ILEGIBLE` |
| Edad menor que `EDAD_MINIMA` | RECHAZADA | `MENOR_DE_EDAD` |
| Otra sesión VERIFICADA con el mismo documento | RECHAZADA (sin reintento) | `DOCUMENTO_YA_REGISTRADO` |
| Proveedor caído o circuito abierto | EN_REVISION | `PROVEEDOR_NO_DISPONIBLE` |
| Todo bien | VERIFICADA | — |

Hasta `MAX_INTENTOS` (3) por sesión. El `titular_id` es el HMAC del número de documento con `CLIENTE_ID_CLAVE`, el
mismo algoritmo y clave de AUTH y el BFF: así se ligan las pólizas previas. Su índice único parcial
(`estado = 'VERIFICADA'`) cierra la carrera entre dos sesiones con el mismo documento. Los datos leídos del documento se
guardan cifrados (AES-GCM, sesión como dato asociado) y solo de las sesiones VERIFICADAS.

## API

Las operaciones de la app llevan `X-Onboarding-Token` (30 min, solo se guarda su hash); el BFF lo reenvía.

| Método y ruta | Qué hace |
|---|---|
| `POST /onboarding` `{jurisdiccion: CO, tipo_documento: CC\|CE}` | Crea la sesión; devuelve `onboarding_id` y `token` |
| `GET /onboarding/{id}` | `{estado, motivo, intento, intentos_restantes}` desde la base (sirve con el proveedor caído, EC010) |
| `POST /onboarding/{id}/consentimiento` `{consentimiento_id}` | Guarda el consentimiento ya registrado en AUTH |
| `POST /onboarding/{id}/subidas` `{paso, tipo_contenido, tamano_bytes}` | URL firmada de una pieza (`ANVERSO`, `REVERSO`, `ROSTRO`, `VIDEO`) |
| `POST /onboarding/{id}/subidas/{archivo_id}/confirmar` | Respaldo de la notificación del bucket (y el camino en local) |
| `POST /onboarding/{id}/enviar` | Con las cuatro piezas DISPONIBLE, agenda la verificación |
| `POST /onboarding/{id}/reintentar` | Nuevo intento tras un rechazo |
| `POST /onboarding/{id}/cuenta` `{usuario_id}` | Liga la cuenta creada en AUTH (M01-05b) |
| `GET /usuarios/{usuario_id}/onboarding` | Sesión más reciente de una cuenta (`miVerificacion`) |
| `POST /tareas/verificar/{id}` | La dispara Cloud Tasks (o un hilo en local). 503 = reintentar |

## Variables

`DATABASE_URL`, `ALMACENAMIENTO_URL`, `ACL_URL`, `AUTH_URL`, `AUTENTICACION_SERVICIOS` (`ninguna` | `id_token`), `CLIENTE_ID_CLAVE`
(secreto `clave-cliente-id`), `CIFRADO_CLAVE` (secreto `clave-cifrado`; o `KMS_KEY_NAME` + `CIFRADO_CLAVE_ENVUELTA`),
`EDAD_MINIMA` (18), `MAX_INTENTOS` (3), `TOKEN_VIGENCIA_SEGUNDOS` (1800), `TIMEOUT_SEGUNDOS` (20), `DISPARADOR`
(`bucle` por omisión; Terraform pone `externo` con `crear_tareas`, junto con `GCP_PROYECTO`, `TAREAS_CUENTA`, `URL_PROPIA`, `TAREAS_COLA` =
`verificacion-onboarding`), `PUBSUB_TEMA_ONBOARDING_VERIFICADO` y `PUBSUB_TEMA_ONBOARDING_RECHAZADO` (vacíos = log).

## Desde la app

La app no llama a este servicio: va por el BFF (contrato v0.20, `iniciarOnboarding`, `registrarConsentimientoOnboarding`,
`solicitarSubidaOnboarding`, `confirmarSubidaOnboarding`, `crearCuentaOnboarding`, `enviarVerificacion`,
`reintentarOnboarding`, `onboarding`, `miVerificacion` y `actualizarSesion`).

## Correr

```bash
docker compose up onboarding        # 8010, con almacenamiento, acl-worker y stub-kyc
cd services/onboarding && python -m pytest
```
