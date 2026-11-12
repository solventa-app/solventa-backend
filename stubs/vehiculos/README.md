# stub-vehiculos

Proveedores simulados de los datos del vehículo para la póliza de auto (HU-W08, KAN-290,
[`docs/plan-poliza-auto.md`](../../docs/plan-poliza-auto.md), decisión A-4).

## Responsabilidad

Stub **por contrato** (D-05), como `stub-open-data`: tres fuentes en un solo servicio, una consulta síncrona por fuente y
un endpoint de control para cambiar de modo en caliente. Lo llama solo el ACL Worker (regla 2). Andamiaje de prueba: sin
hexagonal, sin persistencia, datos inventados. Las fuentes están declaradas en `contracts/fuentes-vehiculo.json`.

| Fuente | Imita | Devuelve |
|---|---|---|
| `runt-vehiculo` | RUNT | marca, línea, modelo (año), clase, servicio, código Fasecolda, estado (`ACTIVO`, `CON_LIMITACION`, `HURTADO`) y odómetro de la última revisión técnico-mecánica |
| `fasecolda-valor` | Guía de valores Fasecolda | valor comercial e índice de hurto de la referencia (`BAJO`, `MEDIO`, `ALTO`, `MUY_ALTO`) |
| `siniestralidad-placa` | Siniestralidad del sector | siniestros pagados a la placa en los últimos 3 años, con detalle |

## Contrato

- `GET /health` y `GET /metrics`.
- `GET|POST /control/modo` (`sano`, `lento`, `error`, `caido`), igual que los demás stubs; override por request con
  `?modo=` o `X-Modo-Simulado`.
- `GET /control/placas`: las placas de demo y el caso que cubre cada una.
- `POST /v1/fuentes/{fuente}/consulta` `{"placa": "ABC123"}` → `{"fuente", "datos", "capturado_en"}`. La placa se
  normaliza (sin espacios ni guiones, en mayúsculas); 422 si no es `ABC123` ni `ABC12D` (moto); 404 si la fuente no
  existe o la placa no está en RUNT.

## Placas de demo (`app/vehiculos.json`)

| Placa | Caso |
|---|---|
| `ABC123` | Nuevo, sin siniestros: la póliza de demo del cliente de siniestros |
| `DEF456` | Viejo (14 años), índice de hurto alto, 1 siniestro |
| `GHI789` | Camioneta con índice de hurto muy alto |
| `JKL012` | 3 siniestros en 3 años: queda en revisión |
| `MNO345` | Con limitación a la propiedad: no asegurable |
| `PQR678` | Más de 15 años: no asegurable en todo riesgo |
| `STU901` | Hurtado: no asegurable |
| `VWX23D` | Moto de uso comercial, hurto muy alto |
| `YZA456` | Fasecolda falla (503): la oferta sale preliminar |
| `NOX000` | No existe en RUNT (404) |

Cualquier otra placa válida da un vehículo sintético **determinista** (la misma placa, el mismo vehículo), para que QA
pueda inventar placas.

## Operación

- Puerto local: `8107`. En el compose el ACL Worker lo recibe en `VEHICULOS_URL`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest`.
