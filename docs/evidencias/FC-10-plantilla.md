# Evidencia FC-10 — Integración con la pasarela de pagos (HU-W05) — PLANTILLA SIN EJECUTAR

> Esta plantilla **no tiene resultados**. Se llena después de correr los comandos de abajo contra staging (o local), con
> las cifras reales y la fecha. Si un número no salió de una corrida, no va aquí. Copiar como
> `FC-10-<fecha>.md` al llenarla.

- **Fecha de la corrida:** _(pendiente)_
- **Entorno:** _(local o staging; URL del gateway y del stub; si hubo `calentar_camino_critico`)_
- **Historia / escenario:** HU-W05 (KAN-28) · FC-10 (contrato e idempotencia, confirmación transaccional, manejo de errores)
- **Límites (regla 9 de `CLAUDE.md`):** el piso, con la pasarela simulada (stub) y bases pequeñas; prueba reducida.

## 1. Preparación

```bash
# staging: Actions → Costos staging → encender (Cloud SQL y Redis tardan 10-15 min)
export BFF_URL=...                 # la del gateway
export USUARIO_BACKOFFICE=... CLAVE_BACKOFFICE=...
python3 scripts/sembrar_cobros.py
```

## 2. Contrato e idempotencia — Newman (carpeta 9)

```bash
newman run tests/newman/solventa-bff.postman_collection.json --folder "9" \
  -e <entorno> -r cli,htmlextra --reporter-htmlextra-export docs/evidencias/FC-10-newman.html
```

| Peticiones | Aserciones | Fallas |
|---|---|---|
| _(pendiente)_ | _(pendiente)_ | _(pendiente)_ |

## 3. Idempotencia bajo concurrencia — k6

```bash
k6 run -e BFF_URL=$BFF_URL -e CLAVE_BACKOFFICE=... tests/k6/cobros-idempotencia.js
```

| Qué | Meta | Medido |
|---|---|---|
| Cobros creados para un mismo `idIntentoCobro` con 20 usuarios a la vez | exactamente 1 | _(pendiente)_ |
| Respuestas 5xx | 0 | _(pendiente)_ |

## 4. Pasarela lenta y recuperación — k6

```bash
k6 run -e BFF_URL=$BFF_URL -e CLAVE_BACKOFFICE=... -e STUB_PASARELA_URL=... -e TOKEN_PASARELA=... \
  tests/k6/cobros-pasarela-lenta.js
```

| Qué | Meta | Medido |
|---|---|---|
| 5xx y `ErrorCobro` | 0 % | _(pendiente)_ |
| p99 de `crearCobro` con la pasarela lenta | < 1,5 s | _(pendiente)_ |
| Pendientes cobrados solos por Cloud Tasks | ≥ 95 % | _(pendiente)_ |

## 5. Trazabilidad

_(pendiente: captura del tablero «Staging — Servicios» y de una traza del flujo de un cobro en «Staging — Flujos»)_
