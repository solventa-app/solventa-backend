# observabilidad/

Prometheus + Grafana locales (perfil `observabilidad` del `docker-compose.yml`).

```bash
docker compose --profile servicios --profile observabilidad up -d --build
# Prometheus: http://localhost:9190   Grafana: http://localhost:3100
```

- Todos los servicios exponen `/metrics` desde el esqueleto (`prometheus-fastapi-instrumentator`).
- `grafana/dashboards/` está vacío a propósito: los dashboards se agregan a medida que se miden los escenarios (EC003/EC004, EC009/EC010, EC011/EC012). Base de referencia: los dos dashboards de `solventa-arquitectura/observabilidad/`.
- Regla del repo: **las métricas no llevan datos personales** como etiquetas (ni documento, ni correo, ni `cliente_id` en claro).
