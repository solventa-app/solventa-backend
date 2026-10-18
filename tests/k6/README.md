# tests/k6/

Pruebas de carga y de regresión de los escenarios de calidad del backend contra staging (o el gateway local).
Los guiones ya están construidos y adaptados a partir de los experimentos de `solventa-arquitectura`:

| Guion | Escenario | Umbral | Modo reducido (`MODO_REDUCIDO=true`) |
|---|---|---|---|
| `perfilamiento-baseline.js` | EC003 / EC004 (HA-LAT-001) | p95 ≤ 400 ms (normal), p99 ≤ 800 ms (pico), 0 % 5xx | 10 min a 5,6 req/s (~3.360 peticiones) + pico de 2 min a 3× (16,8 req/s) |
| `falla-proveedor.js` | EC009 / EC010 (HA-LAT-002) | 100 % > 700 ms cortadas; p99 ≤ 500 ms con caché; 0 % 5xx | 5 min con stub en `lento` (EC009) + 5 min con stub en `caido` y caché caliente (EC010) |
| `lectura-replica.js` | EC011 / EC012 (HA-LAT-003) | p95 ≤ 150 ms, p99 ≤ 300 ms; 0 ofertas con perfil obsoleto | 10 min a 5,6 req/s (se puede medir en la misma sesión de EC003) |
| `gateway-presupuesto.js` | EC001 / EC002 / EC008 (HA-LAT-004) | p99 ≤ 500 ms e2e por gateway; ≤ 120 ms por dependencia; corte duro a 700 ms | 10 min a 5,6 req/s contra `GATEWAY_URL` + 2 min con stub en `lento` |

## Cómo correrlos en modo reducido (sin gastar el presupuesto de staging)

El script `scripts/correr_k6.sh` ejecuta los cuatro guiones en modo reducido (`MODO_REDUCIDO=true` por defecto), guarda los JSON crudos en `tests/k6/resultados-crudos/` y deja el resumen en `docs/evidencias/`:

```bash
# Contra el API Gateway de staging (recomendado tras el bloque 10):
STAGING_URL="https://<gateway-url>" \
USUARIO_PRUEBA="asesor.demo" \
CLAVE_PRUEBA="<clave>" \
./scripts/correr_k6.sh

# O un solo guion puntual:
k6 run -e STAGING_URL="https://<gateway-url>" -e MODO_REDUCIDO=true tests/k6/perfilamiento-baseline.js
```

Para correr la duración completa original (60 min en lugar de 10 min), exporta `MODO_REDUCIDO=false`.

## Reglas

- Cada corrida deja su resumen en `docs/evidencias/<EC>-<fecha>.md` (comando, cifras, qué se midió y qué se proyectó, y límites). Los crudos grandes van a `tests/k6/resultados-crudos/` (ignorado por git).
- Son umbrales de un **piso**: staging con proveedores simulados (stubs en Cloud Run, D-05) y almacenes en nivel mínimo (`db-f1-micro`, Atlas M0, Redis BASIC 1 GB). Dilo siempre en cada informe.
- Antes de medir en staging, enciende Cloud SQL y Redis (*Actions → Costos staging → encender*) y activa `calentar_camino_critico = true` si quieres evitar arranques en frío durante la ventana; al terminar, vuelve a apagar.
- Los guiones se vuelven **regresión en cada sprint**: se vuelven a correr, no se descartan.
