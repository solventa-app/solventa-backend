# acl-worker

ACL Worker: capa anticorrupción hexagonal.

## Responsabilidad

Único punto de contacto con proveedores externos (Open Finance, Open Data y pasarela de pagos). **Arquitectura hexagonal** (HA-MOD-001):

- `app/domain/puertos.py`: puertos estables `PuertoFuenteFinanciera`, `PuertoFuenteAbierta` y `PuertoPasarelaPagos`.
- `app/adapters/`: un adaptador stub por puerto (apuntan a `stubs/`); los reales se agregan sin tocar el dominio.
- `app/application/`: casos de uso envueltos en Circuit Breaker (`purgatory`) + Retry, timeout duro de **700 ms**, degradación con caché o valor por defecto (HA-LAT-002, EC009/EC010).
- La sonda de recuperación del circuito corre **fuera del camino síncrono** (Consolidador, aprendizaje del Experimento 1).

Base reutilizable: `acl-worker` y `consolidador-kyc` del Experimento 1 (`solventa-arquitectura@11e4be6`). KYC y firma electrónica se agregan en el Sprint 2.

## Operación

- Puerto local: `8005`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-acl-worker .`
