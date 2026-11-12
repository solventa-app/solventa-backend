# claims

CLAIMS: escritor único de la mesa de siniestros (HU-W04, KAN-27) y del catálogo maestro de prestadores de la
asistencia en sitio (HU-M06, KAN-263). Guarda los siniestros, los peritos, las evidencias
(referencias a archivos del almacenamiento), las verificaciones, las asignaciones a perito y las resoluciones. Plan:
[`docs/plan-completar-hu-w04.md`](../../docs/plan-completar-hu-w04.md).

- **Puerto:** 8012 (el 8011 es de `avisos`). Privado: solo lo llama el BFF, que exige el rol BACKOFFICE antes de llamar (W04-11).
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
| `prestadores` | Catálogo maestro de clínicas y talleres (HU-M06), sin datos personales | Baja = `activo: false`, nunca se borra |
| `asistencias` | Asistencia en sitio que pide el cliente (HU-M06): `_id` = `id_solicitud` de la app, copia del prestador, estado, intentos; ubicación y descripción cifradas | Migración `0006`; solo se lee por `_id` en la primaria |
| `catalogos` | Control de versión de cada catálogo (`_id: "prestadores"`: `version` y `huella` del contenido) | Lo escribe solo la sincronización |

**Catálogo de prestadores (HU-M06).** La fuente es `migrations/datos/prestadores.json` (copiado de
`solventa-frontend-mobile/docs/hu-m06/prestadores-semilla.json`; desde ahora manda este archivo y la app copia de aquí).
No hay job de semilla: `python -m migrations` lo sincroniza **en cada ejecución** (`migrations/catalogos.py`), así que
llega a staging por el mismo camino que las migraciones. Upsert por `id`, nunca borra; si el contenido cambió sin subir
`version` (`AAAA-MM-DD.n`), la migración **falla** (la app no volvería a descargar), y nunca baja a una versión anterior.

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
| POST | `/siniestros/{id}/derivar` | `{perito_id, instrucciones, actor_usuario_id}`: deja el caso EN_REVISION con su perito y la fecha límite del informe (días hábiles, W04-7). Una sola derivación por caso |
| POST | `/siniestros/{id}/rechazar` | `{motivo, comentario, actor_usuario_id}`: RECHAZADO con la vía de recurso |
| POST | `/siniestros/{id}/aprobar` | `{actor_usuario_id}`: pide primero la orden de pago a PAYMENTS (`POST /indemnizaciones`, idempotente por siniestro) y luego aprueba. Indemnización = suma reclamada acotada a la cobertura de la póliza (W04-8, la pide a POLICY) |
| POST | `/siniestros/_aprobar-vencidos` | Una pasada del plazo (Cloud Scheduler `aprobar-siniestros-vencidos`, cada minuto; en local, el servicio `aprobar-vencidos-local` del compose): cada caso PENDIENTE vencido se aprueba por el mismo camino, con actor `SISTEMA` y `automatica = true`, sin mirar las verificaciones. Un caso derivado espera al perito. Devuelve `{aprobados, pendientes}`; un fallo sigue abierto para la próxima pasada |
| POST | `/siniestros` | HU-M05: el CLIENTE reporta desde la app (lo llama el BFF con su id y su nombre y documento de AUTH). Crea el caso PENDIENTE con radicado `SIN-AAAA-NNNN` (contador en `contadores`, desde 1000), plazo de 2 días hábiles y suma reclamada = cobertura. Idempotente por `id_reporte` (es el id del caso). Valida póliza suya, EMITIDA y vigente en la fecha, relato de 10 a 1000 y de 1 a 3 fotos `siniestro-foto` DISPONIBLE a nombre del caso (las consulta al almacenamiento). Publica `siniestro.reportado` |
| GET | `/clientes/{id}/siniestros` | HU-M05: los casos que reportó un cliente (`reportado_por`, índice de la migración `0004`) |
| GET | `/prestadores?version_local=` | HU-M06: catálogo de prestadores desde la secundaria. `version_local` igual a la vigente: `{version, vigente: true, prestadores: []}`; nula o distinta: la lista completa, con inactivos. 503 si no hay catálogo o la réplica no responde |
| POST | `/asistencias` | HU-M06 (KAN-265): `{id_solicitud, cliente_id, prestador_id, tipo_asistencia, descripcion?, direccion?, latitud?, longitud?}`; sin coordenadas la dirección es obligatoria y no hay `minutos_estimados` (CA-M06-02). Idempotente por `id_solicitud`: una CONFIRMADA o CANCELADA vuelve sin cambios; una NO_CONFIRMADA reintenta. Valida prestador activo y del tipo pedido. 409 con `{detail, codigo}` (`PRESTADOR_NO_DISPONIBLE`, `TIPO_NO_ATENDIDO`, `DATOS_INVALIDOS`) |
| POST | `/asistencias/{id}/cancelar` | HU-M06 (KAN-266): `{cliente_id}`. Idempotente; 404 `NO_ENCONTRADA` si no existe o es de otro cliente |
| POST | `/_demo/reiniciar` | Solo con `DEMO_REINICIAR=true` (local): vuelve a la semilla. No toca las órdenes de PAYMENTS |
| POST | `/_demo/siniestros/{id}/vencer?minutos=2` | Solo con `DEMO_REINICIAR=true` (local): el plazo del dictamen de un caso PENDIENTE vence en `minutos` (0 a 1440; 409 si no está pendiente). Sirve para ver el contador llegar a cero y la aprobación automática |

**Errores:** un error de negocio es `409 {"detail": mensaje}` (caso inexistente o ya resuelto, perito no disponible,
texto de más de 500 caracteres) y una dependencia caída (POLICY o PAYMENTS) es `503 {"detail": mensaje}`; en los dos
casos el mensaje es para el operador y el caso sigue abierto. La derivación y la resolución se guardan en una
transacción de MongoDB junto con el estado del caso, y los índices únicos impiden resolver dos veces aunque lleguen
peticiones simultáneas (verificado: 8 aprobaciones a la vez dejan una resolución y una orden).

## Variables de entorno

| Variable | Por defecto | Qué es |
|---|---|---|
| `MONGO_URI` | replica set local `localhost:27117,27118` | En Cloud Run, el secreto `mongo-uri-claims` (usuario `claims-service`, `readWrite` solo sobre `claims`) |
| `MONGO_DB` | `claims` | Base |
| `LECTURA_MAX_MS` | `500` | Tope de cada lectura en la réplica |
| `CIFRADO_CLAVE` / `KMS_KEY_NAME` / `CIFRADO_CLAVE_ENVUELTA` | clave local de desarrollo | Igual que RISK (D-04) |
| `ACL_URL` | `http://localhost:8005` | HU-M06 (v0.49): la red de prestadores está detrás del ACL Worker (`POST /prestadores/asistencias`); el desenlace simulado vive en `stubs/prestadores` (`PRESTADORES_SIMULACION`) |

## Correr

```bash
docker compose up -d --wait mongo1 mongo2 && docker compose run --rm mongo-init
bash scripts/verificar-migraciones.sh claims    # dos corridas: la segunda no aplica nada
docker compose --profile servicios up -d --build --wait claims
cd services/claims && python -m pytest
```

## Pendiente

- HU-M06: el desenlace de la asistencia lo simula `stubs/prestadores` detrás del ACL (v0.49) hasta que haya
  prestadores reales que acepten o rechacen; falta la verificación en staging.

- Los plazos en días hábiles no descuentan festivos (falta un calendario oficial).
- El radicado de los siniestros nuevos llega con HU-M04, cuando el móvil los cree; hoy los crea la semilla
  (`services/claims/semilla/`: JSON y fotos, con sus créditos y licencias en `CREDITOS.md`).
