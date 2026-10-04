# risk

RISK: único escritor del perfil de riesgo.

## Responsabilidad

RISK es el **único escritor del perfil de riesgo** (MongoDB primaria, réplica de lectura para RATING).

- Recibe los datos de las fuentes (vía `acl-worker`) y escribe el perfil con `w=1` explícito, como en el Experimento 2.
- **D-02 (read-your-writes):** devuelve en cada escritura el `operationTime` para que RATING lea con sesión causal (`afterClusterTime`).
- Cifrado de campo de los datos personales con Cloud KMS antes de persistir (HA-SEG-002, D-04). Logs sin datos personales.
- Emite `perfil.actualizado` por el bus de eventos.

Base reutilizable: `escritor-risk` del Experimento 2 (`solventa-arquitectura@11e4be6`).

## Operación

- Puerto local: `8002`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-risk .`

## Migraciones

MongoDB `risk` (este servicio es su único escritor; RATING solo lee y **no** migra). Runner propio mínimo: cada migración es un módulo
`migrations/versiones/NNNN_descripcion.py` con `aplicar(db)`, registrado en la colección `_migraciones`.

- Aplicar: `python -m migrations` (necesita `MONGO_URI`; `MONGO_DB` por defecto `risk`). En `docker compose` lo hace `migrar-risk`; en staging, el Cloud Run Job `migrar-risk`.
- **Idempotentes** (crear un índice que ya existe no falla) y compatibles hacia atrás: se aplican mientras la revisión anterior aún atiende (ADR-07).
- Verificar: `bash ../../scripts/verificar-migraciones.sh risk` (aplica dos veces y revisa el registro).
- Los índices y la forma del perfil (T-W01-7) irán en la migración `0002`; hoy solo existe la base `0001`.
