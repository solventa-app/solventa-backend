# Opciones de verificación de identidad para el onboarding (HU-M01)

Elegido: **Didit**, APIs independientes v3 (síncronas, sin webhook): `id-verification`, `passive-liveness` y `face-match`.

| Criterio | Didit | Truora | Stub propio |
|---|---|---|---|
| Documento colombiano | Sí | Sí | Simulado |
| Prueba de vida pasiva + cotejo de rostro | Sí | Sí | Simulado |
| API síncrona sin flujo hospedado | Sí (standalone) | Flujo con webhook | Sí |
| Sandbox gratis | Sí (resultado estático, no cobra) | Bajo solicitud | — |
| Costo | Plan gratuito con cupo; luego por llamada | Contrato comercial | 0 |

Por qué Didit: se integra detrás del ACL con tres llamadas síncronas, tiene sandbox sin costo y no exige un SDK en el móvil
(las imágenes las sube la app con URLs firmadas de `almacenamiento`). El stub replica su forma para local y CI.

Riesgos: datos biométricos (requiere aceptación de tratamiento y DPA del equipo); Didit responde 200 incluso al rechazar, por
eso el estado sale del cuerpo; cobra por llamada, por eso hay caché por huella y no hay reintento.

Fuentes: documentación pública de Didit (docs.didit.me, APIs v3 standalone) y de Truora, consultadas el 2026-10-27.

---

## Flujo completo e integración con Almacenamiento y Móvil (HU-M01 / KAN-31)

1. **Validación de correo con OTP de 6 dígitos (`solicitarCodigoOnboarding` / `verificarCodigoOnboarding`):**
   - Antes o durante el onboarding, el BFF valida que el correo exista enviando un código de 6 dígitos vía `acl-worker`
     (`/notificaciones/enviar`, plantilla `codigo-verificacion`).
   - Al validar el código, `auth` crea la cuenta en estado `PENDIENTE_VERIFICACION` (sin conocer aún el nombre ni el
     documento) y emite un JWT con reclamo `verificacion-pendiente`.
2. **Captura en cámara y subida de soportes a Cloud Storage (`almacenamiento`):**
   - La app móvil captura 4 piezas en memoria (`ANVERSO`, `REVERSO`, `ROSTRO`, `VIDEO`) y pide URLs firmadas V4
     (`solicitarSubidaOnboarding` → `PUT` directo al bucket → `confirmarSubidaOnboarding`).
   - En el bucket de GCS los objetos se organizan sin PII en la ruta (Ley 1581):
     `{tipo}/{AAAA}/{MM}/{archivo_id}` (`onboarding-documento`, `onboarding-rostro`, `onboarding-video`).
   - La trazabilidad por cliente vive en base de datos:
     - En `almacenamiento` (tabla `archivos`): `propietario_tipo = "onboarding"`, `propietario_id = sesion_id` y
       `consentimiento_id`.
     - En `onboarding` (tabla `sesiones_onboarding`): `sesion_id` vincula `usuario_id`, `titular_id` (HMAC-SHA256 del
       documento) y los 4 UUID (`anverso_id`, `reverso_id`, `rostro_id`, `video_id`).
   - Al cerrar la verificación se fija `Custom-Time` para retención de **30 días**.
3. **Reconocimiento facial en vivo + OCR con Didit v3 (`acl-worker`):**
   - `onboarding` invoca `POST /identidad/verificar` en `acl-worker` (`services/acl-worker/app/adapters/identidad_didit.py`),
     que ejecuta:
     1. `/v3/id-verification/` (OCR del frente y reverso + autenticidad del documento).
     2. `/v3/passive-liveness/` (prueba de vida pasiva sobre el rostro en vivo).
     3. `/v3/face-match/` (comparación 1:1 del rostro en vivo contra la foto del frente de la cédula).
   - Si el rechazo es únicamente biométrico (`ROSTRO_NO_COINCIDE` o `SUPLANTACION`) y quedan intentos, la app móvil
     conserva en memoria las fotos del frente y reverso de la cédula y permite repetir solo la toma facial en vivo en
     `M01-05`.
   - Al aprobarse (`VERIFICADA`), `onboarding` cifra los datos del OCR con AES-GCM, publica `onboarding.verificado` y
     notifica a `auth` (`POST /usuarios/{id}/verificacion`) para activar la cuenta con el nombre y documento extraídos.
