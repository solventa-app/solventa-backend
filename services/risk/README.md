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
