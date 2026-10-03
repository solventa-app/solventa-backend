# rating

RATING: cálculo de oferta sobre réplica de lectura.

## Responsabilidad

RATING calcula la oferta de vida hipotecario. **Nunca escribe en Riesgo.**

- Lee el perfil de la **secundaria** de MongoDB con sesión causal (D-02, HA-LAT-003: ≤ 10 ms extra, 0 ofertas con perfil obsoleto).
- Cache-Aside de scores en Redis con TTL y versión de reglas (HA-LAT-001, EC003/EC004: p95 ≤ 400 ms, p99 ≤ 800 ms).
- Si una fuente supera 700 ms o falla: oferta preliminar con rango de prima, con el último valor en caché o uno por defecto (CA-W01-04).
- Reglas actuariales aisladas como contexto delimitado (HA-MOD-002, Sprint 2).

Base reutilizable: `lector-rating` y `ryw.py` del Experimento 2 (`solventa-arquitectura@11e4be6`).

## Operación

- Puerto local: `8003`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-rating .`
