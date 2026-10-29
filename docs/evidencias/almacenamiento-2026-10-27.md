# Evidencia — Almacenamiento de archivos (KAN-250), 2026-10-27

**Corrida local, no de staging.** PostgreSQL 16 real (`docker compose`) y `fake-gcs-server` 1.52.2 como bucket. El
emulador **no** revisa la firma V4, ni `x-goog-content-length-range`, ni emite `OBJECT_FINALIZE`: esas tres cosas solo
se prueban en staging con `crear_almacenamiento = true` (ver "Falta" abajo).

## Recorrido real (`curl` contra el servicio en `:8009`)

Actor `servicio:onboarding` (el tipo `onboarding-rostro` solo lo sube ese servicio), con consentimiento.

```
== 1. solicitar subida (onboarding-rostro, 2004 bytes)
estado PENDIENTE | metodo PUT | encabezados ['Content-Type', 'x-goog-content-length-range', 'x-goog-if-generation-match']
url http://localhost:4443/solventa-archivos-local/onboarding-rostro/2026/10/aa75a309-6e0e-4699-b56c-42df5589a7a2 ?X-Goog-Algorithm=...
== 2. confirmar antes del PUT
{"detail":"OBJETO_NO_SUBIDO"}
== 3. PUT directo a la URL firmada
HTTP 200
== 4. confirmar
{'estado': 'DISPONIBLE', 'tamano_bytes': 2004, 'crc32c': 'H/d/nw=='}
== 5. descarga firmada y bytes iguales
iguales (2004 bytes)
== 6. un CLIENTE no puede leerlo
HTTP 404
== 7. contenido que no es JPEG -> RECHAZADO
{'estado': 'RECHAZADO', 'motivo': 'CONTENIDO_NO_COINCIDE'}
== 8. sin consentimiento
{"detail":"CONSENTIMIENTO_REQUERIDO"}
== 9. accesos auditados y append-only
DESCARGA|servicio:onboarding|1
SUBIDA|servicio:onboarding|2
ERROR:  accesos_archivo es append-only: no se modifica ni se borra
```

Lo que muestra: el `PUT` va directo al bucket (los bytes no pasan por el servicio), confirmar antes de subir no cambia
nada, el contenido se valida por sus primeros bytes (un archivo que dice ser JPEG y no lo es queda `RECHAZADO`), la
descarga devuelve los mismos bytes, un CLIENTE no ve un archivo de onboarding (404, no 403: no revela que existe),
cada URL emitida queda en `accesos_archivo` y la base rechaza borrar esa tabla.

## Migraciones

`alembic downgrade base → upgrade head → downgrade -1 → upgrade head → current` = `0001 (head)` contra Postgres real.
(`scripts/verificar-migraciones.sh almacenamiento` no pudo construir la imagen en este entorno por el proxy de `pip`; se
corrieron los mismos pasos con la imagen ya construida.)

## Pruebas

- `services/almacenamiento`: 62 pruebas en verde, `ruff` limpio.
- `services/bff`: suite completa en verde, incluida `test_esquema_contrato.py` (esquema = contrato v0.17) y
  `tests/test_archivos.py` (4 pruebas nuevas).
- `scripts/validar_servicios.py`, `scripts/validar_contratos.py` y `scripts/sincronizar_tipos_archivo.py --verificar`: OK.
- `terraform fmt`: OK. `terraform validate` **no** se pudo correr aquí (el registro de Terraform está bloqueado en este
  entorno); lo corre el CI.

## Falta (necesita staging)

- Firma V4 real con IAM `signBlob` y el rango de tamaño firmado (un `PUT` mayor que lo declarado debe dar 400).
- Aviso `OBJECT_FINALIZE` → Pub/Sub → `/eventos/gcs` con OIDC (hoy se confirma por API).
- CMEK del bucket con la llave `archivos` y la regla de ciclo de vida por `customTime`.
- CORS desde el origen real del frontend.
