# auth

AUTH: consentimiento e identidad.

## Responsabilidad

AUTH es el **único escritor del almacén de identidad** (PostgreSQL, regla de escritor único). En el Sprint 1:

- Registro **append-only** de consentimientos (actor, finalidad, versión del texto, sello de tiempo) y de su revocación (HA-SEG-001, EC023).
- Validación de consentimiento vigente antes de consultar cualquier fuente (CA-W01-01/02).
- Al revocar, invalida la caché del cliente en ≤ 5 min (CA-W01-08).
- JWT con scopes y rotación llega en el Sprint 2 (HU-M02, HA-SEG-004/005).

Eventos que emite: `consentimiento.registrado`, `consentimiento.revocado` (ver `contracts/events/`).

## Operación

- Puerto local: `8001`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-auth .`

## Migraciones

PostgreSQL `auth` (este servicio es su único escritor). Alembic con SQL explícito, sin ORM; el esquema lo definen las migraciones.

- Aplicar: `alembic upgrade head` (necesita `DATABASE_URL`). En `docker compose` lo hace `migrar-auth` antes de arrancar el servicio; en staging, el Cloud Run Job `migrar-auth` con la misma imagen.
- Nueva migración: `alembic revision -m "texto" --rev-id 0002`, escribir `upgrade` **y** `downgrade`, y verificar con `bash ../../scripts/verificar-migraciones.sh auth` (una sola cabeza; subir, bajar a base y subir).
- Compatibles hacia atrás: agregar sí; renombrar o borrar en dos despliegues (ADR-07). Nunca edites una migración ya integrada a `main`.
- La tabla de consentimiento (append-only, T-W01-4) va en la migración `0002`; hoy solo existe la base `0001`.
