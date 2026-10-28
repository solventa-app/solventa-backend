# ADR-10 — Almacenamiento de archivos con un servicio propio y URLs firmadas

- **Estado:** propuesto. Verificado en local con PostgreSQL real y `fake-gcs-server`; en staging sin probar
  (`crear_almacenamiento = false`). Evidencia: [`docs/evidencias/almacenamiento-2026-10-27.md`](../evidencias/almacenamiento-2026-10-27.md).
- **Fecha:** 2026-10-27
- **Historia:** KAN-250 (habilitadora). La usan HU-M01 (onboarding con documento y prueba de vida) y otras historias
  que necesiten guardar archivos (p. ej. el PDF de la póliza, hoy inactivo en el catálogo).

## Contexto

Varias historias necesitan guardar archivos: fotos del documento y video de la prueba de vida (HU-M01), y más adelante
documentos de pólizas y siniestros. Son datos personales sensibles (biométricos), pesados y con retención legal. Si
cada servicio los guardara a su manera, se repetirían el cifrado, la retención, la auditoría y los permisos, y se rompería
la regla de escritor único.

## Decisión

1. **Un servicio, ALMACENAMIENTO**, escritor único de la base `almacenamiento` (Cloud SQL, metadatos) y del bucket
   privado `<proyecto>-archivos` (los bytes). Capas simples, sin hexagonal (regla 3).
2. **Los bytes nunca pasan por los servicios ni por el BFF.** El servicio emite una URL firmada V4 de vida corta
   (subida 10 min, descarga 5 min) y el cliente hace `PUT`/`GET` directo al bucket. La firma lleva el tipo de contenido,
   el tamaño máximo (`x-goog-content-length-range`) y `x-goog-if-generation-match: 0` (no se sobrescribe un objeto).
   En Cloud Run firma con IAM `signBlob` sobre su propia cuenta: no hay llaves descargadas.
3. **Un catálogo de tipos en `contracts/tipos-archivo.json`** dice por tipo: formatos, tamaño máximo, quién sube,
   quién lee, a quién pertenece, si exige consentimiento y cuántos días se retiene. Agregar un tipo es una fila (y
   `scripts/sincronizar_tipos_archivo.py`), no código.
4. **Un archivo se valida antes de servir:** `PENDIENTE → DISPONIBLE | RECHAZADO`, comparando tamaño, tipo y los
   primeros bytes (firma del formato). La confirmación llega por API o por el aviso `OBJECT_FINALIZE` del bucket
   (Pub/Sub push con OIDC a `/eventos/gcs`), lo que pase primero; es idempotente.
5. **Eventos `archivo.disponible` y `archivo.rechazado` por Pub/Sub real** (`contracts/events/`). Amplía la decisión
   W05-8 (eventos por log) solo para este servicio: la historia que pidió el archivo necesita enterarse sin preguntar.
6. **Seguridad:** bucket con acceso uniforme, prevención de acceso público, CMEK con la llave propia `archivos`
   (kms.tf) y borrado suave de 7 días. Cada URL emitida queda en `accesos_archivo`, append-only en la base. Sin nombres
   originales ni datos personales en metadatos ni logs (regla 6).
7. **Retención:** cuando el trámite cierra, el dueño pide `POST /archivos/{id}/retencion`; el servicio fija
   `customTime` y la regla de ciclo de vida del bucket borra el objeto N días después (30 para onboarding). El aviso
   `OBJECT_DELETE` marca la fila `ELIMINADO`. Las subidas nunca confirmadas (`PENDIENTE`) las borra a los 90 días la
   purga del servicio (`POST /archivos/_purgar-vencidos`, Cloud Scheduler diario), **no** una regla `age` del bucket:
   esa regla no distingue estados y borraría también lo `DISPONIBLE` sin retención, como la firma de una póliza emitida
   (HU-W03, 2026-10-27).

## Consecuencias

- Un servicio más en el manifiesto y una base más en Cloud SQL; el bucket cuesta por GB (centavos en staging).
- El frontend sube con `PUT` directo: el bucket necesita CORS con los orígenes reales (`almacenamiento_origenes_cors`).
- Las historias no tocan Cloud Storage: piden la URL al servicio (o por GraphQL al BFF) y escuchan los eventos.
- `fake-gcs-server` no revisa firma, tamaño ni emite avisos: esas garantías solo se prueban en staging.
