# stub-eventos-parametricos

Proveedores simulados de las dos fuentes paramétricas externas de HU-M04 (KAN-259, Fase 3 del plan
de push), que el ACL Worker consulta para originar el siniestro paramétrico (fases siguientes,
fuera de este stub): `services/acl-worker/app/adapters/clima.py` y `.../adapters/vuelos.py`.

## Responsabilidad

Stub **por contrato** (D-05), como `stub-vehiculos` y `stub-open-data`: dos proveedores en un solo
servicio, en las rutas EXACTAS de sus APIs reales, para que el adaptador del ACL Worker use el
mismo código contra este stub o contra el proveedor real (solo cambia `CLIMA_URL`/`VUELOS_URL`).
Lo llama solo el ACL Worker (regla 2). Andamiaje de prueba: sin hexagonal, sin persistencia, datos
inventados.

| Ruta | Imita | Devuelve |
|---|---|---|
| `GET /v1/forecast` | Open-Meteo (`api.open-meteo.com/v1/forecast`) | una serie horaria sintética de 24 valores para `precipitation`, `wind_speed_10m` o `temperature_2m`, determinista por celda (lat/lon redondeadas) |
| `GET /v1/flights` | AviationStack (`api.aviationstack.com/v1/flights`) | el retraso (`departure.delay`, minutos) y el estado (`flight_status`) de un vuelo+fecha, determinista por `flight_iata` |

## Contrato

- `GET /health` y `GET /metrics`.
- `GET|POST /control/modo` (`sano`, `lento`, `error`, `caido`), igual que los demás stubs; override por request con
  `?modo=` o `X-Modo-Simulado`.
- `GET|POST /control/umbral` (`automatico`, `sobre_umbral`, `bajo_umbral`): fuerza que la próxima celda o vuelo
  **sintético** (cualquiera, no uno de la lista de demo) caiga muy por encima o muy por debajo de cualquier umbral
  razonable, sin tener que inventar una celda o un vuelo concretos.
- `GET /v1/forecast?latitude=&longitude=&hourly=<precipitation|wind_speed_10m|temperature_2m>&forecast_days=1` →
  `{"latitude", "longitude", "hourly": {"time": [...], "<parametro>": [...24 valores...]}}`. 422 si `hourly` trae una
  variable desconocida.
- `GET /v1/flights?flight_iata=&flight_date=&access_key=` → `{"data": [{"flight_date", "flight_status",
  "departure": {"delay"}, "flight": {"iata"}}]}`, o `{"data": []}` si el vuelo no existe (como responde AviationStack
  de verdad: no es un error, es un dato).

## Vuelos de demo

| `flight_iata` | Caso |
|---|---|
| `AV9340` | Retraso de 145 min: sobre el umbral típico (120 min) de una cobertura de vuelo |
| `AV1234` | Retraso de 10 min: bajo el umbral |
| `LA5678` | Cancelado (`flight_status = cancelled`, retraso 0): paga aunque el retraso sea 0 |
| `NOOP00` | AviationStack no tiene este vuelo (`data` vacío) |

Cualquier otro `flight_iata` da un retraso **determinista** (0 a 59 min), para que QA pueda inventar vuelos; con
`/control/umbral` se puede forzar que ese mismo vuelo sintético caiga sobre o bajo el umbral.

## Operación

- Puerto local: `8108`. En el compose el ACL Worker lo recibe en `CLIMA_URL` y `VUELOS_URL` (mismo host, rutas
  distintas).
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest`.
