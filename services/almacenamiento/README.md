# almacenamiento

ALMACENAMIENTO: archivos en Cloud Storage con URLs firmadas (KAN-250, [ADR-10](../../docs/adr/ADR-10-almacenamiento-de-archivos.md)).
Cómo lo usa una historia: [`docs/almacenamiento.md`](../../docs/almacenamiento.md).

## Responsabilidad

- Escritor único de la base `almacenamiento` (metadatos) y del bucket `<proyecto>-archivos` (bytes).
- Emite URLs firmadas V4 de subida (10 min) y descarga (5 min); los bytes nunca pasan por aquí.
- Valida cada objeto (tamaño, tipo y firma del formato) antes de marcarlo `DISPONIBLE`, y publica
  `archivo.disponible` / `archivo.rechazado`.
- Aplica el catálogo `app/tipos-archivo.json` (copia de `contracts/tipos-archivo.json`): quién sube, quién lee,
  consentimiento y retención.
- Audita cada URL emitida en `accesos_archivo` (append-only por trigger).

## API

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/health` | Sano aunque falte el bucket |
| `POST` | `/archivos/subidas` | Registra `PENDIENTE` y devuelve URL, método y encabezados firmados |
| `POST` | `/archivos/{id}/confirmar` | Valida el objeto; idempotente |
| `GET` | `/archivos/{id}` | Metadatos (404 si no existe o no puede leerlo) |
| `GET` | `/archivos/{id}/descarga` | URL firmada de lectura |
| `GET` | `/archivos?propietario_tipo&propietario_id` | Los que el actor puede leer |
| `POST` | `/archivos/{id}/retencion` | Empieza a correr la retención (`customTime`) |
| `POST` | `/eventos/gcs` | Push de Pub/Sub con los avisos del bucket; siempre 204 |
| `POST` | `/archivos/_purgar-vencidos` | Respaldo de la regla de ciclo de vida |

El actor llega en `X-Actor-Rol` (`CLIENTE`, `ASESOR`, `BACKOFFICE`, `SERVICIO`) y `X-Actor-Id`. El servicio es privado
(IAM de Cloud Run): solo lo invocan quienes lo declaran en `llama` del manifiesto.

## Configuración

| Variable | Local | Cloud Run |
|---|---|---|
| `DATABASE_URL` | compose | Secret Manager `database-url-almacenamiento` |
| `ALMACEN_BUCKET` | `solventa-archivos-local` | Terraform; vacío = 503 `ALMACEN_NO_CONFIGURADO` |
| `FIRMANTE` | `local` (llave de prueba en memoria) | `iam` (obligatorio en la nube) |
| `ALMACEN_URL_PUBLICA` | `http://localhost:4443` | — |
| `PUBSUB_TEMA_ARCHIVO_DISPONIBLE` / `_RECHAZADO` | vacío (eventos al log) | Terraform |
| `URL_SUBIDA_VIGENCIA_SEGUNDOS` / `URL_DESCARGA_VIGENCIA_SEGUNDOS` | 600 / 300 | 600 / 300 |

## Pruebas

```bash
pip install -r requirements.txt pytest && python -m pytest
bash ../../scripts/verificar-migraciones.sh almacenamiento
```

Código nuevo (no se copió de `solventa-arquitectura`); `app/observabilidad.py` es copia del de los demás servicios.
