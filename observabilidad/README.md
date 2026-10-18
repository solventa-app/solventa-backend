# observabilidad/

Prometheus + Grafana locales (perfil `observabilidad` del `docker-compose.yml`).

```bash
docker compose --profile servicios --profile observabilidad up -d --build
# Prometheus: http://localhost:9190   Grafana: http://localhost:3100
```

- Todos los servicios exponen `/metrics` desde el esqueleto (`prometheus-fastapi-instrumentator`).
- `grafana/dashboards/` está vacío a propósito: los tableros de medición de staging viven en `gcp/dashboards/` y leen directamente de Google Cloud Monitoring, Logging y Trace (decisión 3 y 5 de `docs/plan-completar-hu-w01.md`).
- Regla del repo: **las métricas no llevan datos personales** como etiquetas (ni documento, ni correo, ni `cliente_id` en claro).

## Grafana contra Google Cloud (lo que pasa en staging)

El Prometheus de arriba solo ve el compose local. Para ver lo que hacen los servicios **desplegados** hay un Grafana aparte
(`grafana-gcp`, perfil `gcp`, puerto **3200**) que lee directamente de Google Cloud con tus credenciales de `gcloud`: no hay
servidores nuevos en la nube ni claves en el repo.

```bash
gcloud auth application-default login           # una vez
docker compose --profile gcp up -d grafana-gcp  # http://localhost:3200
```

En Windows (PowerShell), antes del `docker compose`, apunta al archivo de credenciales:
`$env:GCLOUD_ADC = "$env:APPDATA\gcloud\application_default_credentials.json"`. El proyecto por defecto es
`solventa-511815` (variable `GCP_PROYECTO`). Tu cuenta necesita lectura de Monitoring, Logging y Trace en ese proyecto.

| Fuente de datos | Conector | Qué trae |
|---|---|---|
| GCP Monitoring | `stackdriver` (incluido en Grafana) | Métricas de Cloud Run, Cloud SQL y Memorystore (Redis) |
| GCP Logging | `googlecloud-logging-datasource` (plugin, se instala al arrancar) | Logs de los servicios, con el id de traza cuando existe |
| GCP Trace | `googlecloud-trace-datasource` (plugin, se instala al arrancar) | Trazas distribuidas, con salto de cada tramo a sus logs |

Tableros (carpeta `gcp/dashboards/`, aprovisionados solos):

- **Staging — Servicios (Cloud Run) (`servicios.json`):** peticiones, errores 5xx, latencia p50/p95/p99 (con los umbrales de EC003/EC004),
  instancias activas, arranque en frío, CPU y memoria de los 10 servicios. Los arranques en frío importan: con mínimo 0 instancias y el timeout
  duro de 700 ms del API Gateway (`HA-LAT-004`), la primera oferta tras un rato sin uso puede fallar si no se activa `calentar_camino_critico = true` durante las sesiones de medición.
- **Staging — HU-W01 Cotización y Perfilamiento (`hu-w01.json`):** los mismos paneles de Cloud Run filtrados exclusivamente al camino de HU-W01 (`bff`, `auth`, `acl-worker`, `risk`, `rating`, `consolidador-fuentes`, `stub-open-finance` y `stub-open-data`), con líneas de umbral para EC003 (400 ms), EC004 (800 ms), EC001/EC010 (500 ms) y EC011 (150 ms).
- **Staging — HU-W05 Cobros y Conciliación (`hu-w05.json`):** paneles filtrados a los servicios de HU-W05 (`bff`, `payments`, `acl-worker`, `auth` y `stub-pasarela`), con el umbral de corte de 700 ms y 0 % de errores 5xx.
- **Staging — Datos (`datos.json`):** Cloud SQL (CPU, memoria, disco, conexiones, transacciones) y Redis (memoria, clientes, aciertos de
  caché, comandos). MongoDB Atlas M0 no publica métricas a GCP: se ve en su consola.
- **Staging — Logs de los servicios (`logs.json`):** advertencias y errores, y todos los logs, con filtro por servicio.
- **Staging — Flujos (trazas) (`flujos.json`):** lista de trazas recientes; al hacer clic en un id se ve el flujo completo (qué servicio
  llamó a cuál y cuánto tardó cada tramo, incluidas las consultas a Postgres, MongoDB y Redis y el paso desde el API Gateway al BFF) y los logs de esa traza.
  Los tramos de los servicios llegan cuando están desplegados con las trazas activas (`TRAZAS_GCP=true`); antes solo
  aparecen las trazas que abre Cloud Run por su cuenta.

Con el apagado de costos activo (*Costos staging → apagar*), Cloud SQL y Redis aparecen sin datos: es lo esperado.

**Notas.** El botón *Save & test* de las fuentes de GCP puede mostrar un error aunque las consultas funcionen (la prueba de
Monitoring devuelve 400 y la de Trace falla con «no entries» mientras no haya trazas); lo que cuenta son los tableros. Las
consultas a Google Cloud usan el proyecto de cuota de tus credenciales (`quota_project_id`), no el de staging. Si cambias un
tablero en la interfaz, exporta el JSON y guárdalo en `gcp/dashboards/`: lo que no está en el repo se pierde al recrear el
contenedor.
