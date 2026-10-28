# claims

CLAIMS: escritor único de la mesa de siniestros (HU-W04, KAN-27). Guarda los siniestros, los peritos, las evidencias
(referencias a archivos del almacenamiento), las verificaciones, las asignaciones a perito y las resoluciones. Plan:
[`docs/plan-completar-hu-w04.md`](../../docs/plan-completar-hu-w04.md).

- **Puerto:** 8011. Privado: solo lo llama el BFF, que exige el rol BACKOFFICE antes de llamar (W04-11).
- **Almacén:** MongoDB, base `claims`, con colecciones referenciadas (W04-2). Escribe en la primaria; la bandeja y el
  detalle se leen de la secundaria (`secondaryPreferred` + `maxTimeMS`, HA-LAT-003). Tras una mutación se lee de la
  primaria.
- **Capas simples** (regla 3): `app/application/servicio_siniestros.py` (reglas) y `repositorio.py` (MongoDB). No es
  hexagonal.
- **Copiado de RISK** (sin código compartido entre servicios): `cifrado.py`, `identificadores.py`,
  `observabilidad.py` y el runner de `migrations/`.

## Modelo

| Colección | Qué guarda | Reglas |
|---|---|---|
| `siniestros` | El caso, con `poliza_id` hacia la tabla `polizas` de POLICY (otra base: sin llave foránea) | `radicado` único; índices por estado y fecha de notificación |
| `peritos` | Catálogo de peritos | `registro` único |
| `evidencias_siniestro` | Intermedia siniestro ↔ archivo del almacenamiento (`archivo_id`) | Única por (`siniestro_id`, `archivo_id`) |
| `verificaciones_siniestro` | Verificaciones regulatorias del caso | Única por (`siniestro_id`, `orden`) |
| `asignaciones_perito` | Intermedia siniestro ↔ perito | Una por caso; append-only |
| `resoluciones_siniestro` | Aprobación o rechazo | Una por caso; append-only |

Cada colección tiene validación `$jsonSchema` estricta (`migrations/versiones/0002_colecciones.py`). Los datos
personales (nombre y documento del asegurado, ubicación, descripción, comentario e instrucciones) se guardan cifrados
por campo con AES-GCM (regla 6) y nunca van a los logs.

## API

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Vida del contenedor |
| GET | `/siniestros?estado=` | Bandeja, del más reciente al más antiguo |
| GET | `/siniestros/{id}` | Expediente completo; 404 si no existe |
| GET | `/siniestros/{id}/peritos` | Peritos activos con la fecha límite del informe si se le deriva hoy |

## Variables de entorno

| Variable | Por defecto | Qué es |
|---|---|---|
| `MONGO_URI` | replica set local `localhost:27117,27118` | En Cloud Run, el secreto `mongo-uri-claims` (usuario `claims-service`, `readWrite` solo sobre `claims`) |
| `MONGO_DB` | `claims` | Base |
| `LECTURA_MAX_MS` | `500` | Tope de cada lectura en la réplica |
| `CIFRADO_CLAVE` / `KMS_KEY_NAME` / `CIFRADO_CLAVE_ENVUELTA` | clave local de desarrollo | Igual que RISK (D-04) |

## Correr

```bash
docker compose up -d --wait mongo1 mongo2 && docker compose run --rm mongo-init
bash scripts/verificar-migraciones.sh claims    # dos corridas: la segunda no aplica nada
docker compose --profile servicios up -d --build --wait claims
cd services/claims && python -m pytest
```

## Pendiente

- Los plazos en días hábiles no descuentan festivos (falta un calendario oficial), igual que el dummy.
- El radicado de los siniestros nuevos llega con HU-M04, cuando el móvil los cree; hoy los crea la semilla.
