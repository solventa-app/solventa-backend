# stub-siniestros

**Dummy** de siniestros para el front de HU-W04 (Siniestros asistidos y peritaje, KAN-27): bandeja, detalle con
evidencia, derivar a perito, aprobar y rechazar.

## Responsabilidad

Sustituto temporal del servicio de siniestros, que todavía no existe. Es un microservicio **autónomo**: sin base de
datos, sin tokens y sin llamar a ningún otro servicio. Expone **su propio GraphQL** (no pasa por el BFF ni por el
gateway) y sirve las fotos de evidencia. Cuando exista el servicio real, el front solo cambia la URL a la que llama.

- Datos en `app/datos/`: `siniestros.json` (10 casos), `polizas.json` (con la forma de la tabla `polizas` de HU-W03,
  ver `contrato-poliza.json`), `peritos.json` y `medios/` (fotos y documentos). Créditos y licencias de las fotos en
  `app/datos/CREDITOS.md`. Personas, documentos, bancos, talleres y peritos son ficticios; los lugares son reales.
- Estado **en memoria**: aprobar, rechazar y derivar cambian el siniestro mientras el proceso esté vivo; reiniciar el
  contenedor o llamar `/control/reiniciar` vuelve a los datos del JSON.
- `venceDictamenEn` se calcula al cargar (`vence_dictamen_horas` del JSON), para que la cuenta regresiva tenga sentido
  en cualquier demo.
- **Local y staging**: está en `.github/servicios.json` con `"dummy": true`, que lo exime de "lo llama otro servicio".
  En staging es un Cloud Run **privado** como los demás: solo lo invoca el API Gateway (`infra/gateway.tf`), que expone
  `POST /graphql/siniestros` y `GET /graphql/siniestros/medios/{archivo}` (sin `/control/reiniciar`). Cada instancia
  arranca con los datos del JSON: el estado de las acciones se pierde cuando Cloud Run la escala a cero.

## Contrato

SDL en `contracts/graphql/siniestros.graphql` (la prueba `tests/test_esquema_contrato.py` exige que coincida con
`app/esquema.py`).

- `POST /graphql/siniestros` → GraphQL:
  - Consultas `siniestros(estado)`, `siniestro(id)` y `peritos(siniestroId)`; cada perito trae el vencimiento legal de
    su informe si se le deriva hoy (días hábiles, 5:00 p. m. hora Colombia; sin festivos).
  - Mutaciones `aprobarSiniestro(id)`, `rechazarSiniestro(id, motivo, comentario)` y
    `derivarSiniestro(id, peritoId, instrucciones)`, que devuelven `Siniestro` o `ErrorSiniestro { mensaje }`.
  - Reglas: solo se resuelven siniestros `PENDIENTE` o `EN_REVISION`; un siniestro no se deriva dos veces; el comentario
    y las instrucciones admiten hasta 500 caracteres. Derivar deja el siniestro `EN_REVISION` (no hay estado
    `DERIVADO`).
- `GET /graphql/siniestros/medios/{archivo}` → fotos y documentos de evidencia (las URL vienen en `Evidencia.url`).
- `POST /graphql/siniestros/control/reiniciar` → vuelve a los datos originales; responde `{"siniestros": 10}`.
- `GET /health` → `{"servicio": "stub-siniestros", "estado": "ok"}`; `GET /metrics` para Prometheus.

## Correr

```bash
docker compose --profile servicios up stub-siniestros --build --wait   # http://localhost:8107
cd stubs/siniestros && python -m pytest
```

El front lo consume en `/graphql/siniestros`: en local, por el proxy de Vite (`VITE_SINIESTROS_URL`, por defecto
`http://localhost:8107`) o por el gateway Envoy (`:8080`); en staging, por el rewrite de Vercel `/graphql/:path*` al API
Gateway.
