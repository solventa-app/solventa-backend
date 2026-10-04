# payments

PAYMENTS: cobros con pasarela tokenizada.

## Responsabilidad

PAYMENTS procesa el cobro de primas e indemnizaciones con **pasarela tokenizada** (HU-W05). Ningún dato de tarjeta toca servidores propios (EC031).

- Estados de cobro: cobrando, cobrado, rechazado, pendiente (PostgreSQL).
- **Idempotencia:** el mismo intento recibido dos veces se ejecuta una sola vez (CA-W05-04). El reintento reutiliza la clave.
- La pasarela se alcanza solo por el `acl-worker` (Circuit Breaker + Retry, 700 ms). Si abre el circuito el cobro queda `pendiente` y se reintenta con Cloud Tasks (CA-W05-05).
- Las órdenes de pago de indemnización llegan en el Sprint 3 (HU-W04, HU-M04).

## Operación

- Puerto local: `8004`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-payments .`

## Migraciones

PostgreSQL `payments` (este servicio es su único escritor). Alembic con SQL explícito, sin ORM; el esquema lo definen las migraciones.

- Aplicar: `alembic upgrade head` (necesita `DATABASE_URL`). En `docker compose` lo hace `migrar-payments` antes de arrancar el servicio; en staging, el Cloud Run Job `migrar-payments` con la misma imagen.
- Nueva migración: `alembic revision -m "texto" --rev-id 0002`, escribir `upgrade` **y** `downgrade`, y verificar con `bash ../../scripts/verificar-migraciones.sh payments` (una sola cabeza; subir, bajar a base y subir).
- Compatibles hacia atrás: agregar sí; renombrar o borrar en dos despliegues (ADR-07). Nunca edites una migración ya integrada a `main`.
- Estados de cobro, idempotencia y balances (T-W05-3) van en la migración `0002`; hoy solo existe la base `0001`.
