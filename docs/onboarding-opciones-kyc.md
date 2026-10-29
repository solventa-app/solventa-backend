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
