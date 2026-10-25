# Guía de comandos para la demo en staging

Referencia rápida para mostrar los flujos de HU-W01 y HU-W05 en Grafana: generar cobros, inyectar fallas en los
proveedores y revisar el estado de staging. **Datos ficticios; nada de esto va con claves reales en el repo.**

Variables que se usan en todos los bloques (cópialas a tu terminal):

```bash
export PROYECTO=solventa-511815
export REGION=us-central1
export GATEWAY_URL=https://solventa-gateway-btxoyawl.uc.gateway.dev
export STUB_PASARELA_URL=https://stub-pasarela-f5egaxcn6q-uc.a.run.app
export STUB_OPEN_FINANCE_URL=https://stub-open-finance-f5egaxcn6q-uc.a.run.app
export STUB_OPEN_DATA_URL=https://stub-open-data-f5egaxcn6q-uc.a.run.app
```

> El gateway sale de `terraform output -raw gateway_url` o de
> `gcloud api-gateway gateways describe solventa-gateway --location $REGION --project $PROYECTO --format='value(defaultHostname)'`.
> Los stubs son **privados** en staging: cada llamada lleva un token de identidad. Con tu cuenta de usuario usa
> `gcloud auth print-identity-token` **sin** `--audiences` (esa opción solo vale para cuentas de servicio y falla con
> `Invalid account type for --audiences`). Pasa `TOKEN_PASARELA` siempre que corras la semilla o k6: el respaldo del
> script usa `--audiences` y no sirve con una cuenta de usuario.
> Al escribir comandos de varias líneas con `\`, no dejes espacios después de la barra o el shell corta el comando.

## 0. Antes de empezar

```bash
gcloud auth login
gcloud auth application-default login      # Grafana lo usa para leer de GCP
gcloud config set project $PROYECTO
```

Staging debe estar **encendido** (Cloud SQL, Redis, servicios con mínimo 1). Si no, en GitHub:
**Actions → Costos staging → Run workflow → `encender`** (tarda 10–15 min). El apagado automático corre a las
2:00 a.m. hora de Colombia.

```bash
# Estado rápido
gcloud sql instances list --project $PROYECTO --format="table(name,state,settings.activationPolicy)"
gcloud redis instances list --region $REGION --project $PROYECTO --format="table(name,state)"
gcloud run services list --region $REGION --project $PROYECTO --format="table(metadata.name,status.conditions[0].status)"
curl -s -o /dev/null -w "gateway /health -> %{http_code}\n" $GATEWAY_URL/health
```

## 1. Abrir Grafana

```bash
docker compose --profile gcp up -d grafana-gcp     # http://localhost:3200 (sin login, solo tu máquina)
docker compose --profile gcp logs -f grafana-gcp   # si algo falla
docker compose --profile gcp stop grafana-gcp      # al terminar
```

Si los paneles salen vacíos o con 401/403: `gcloud auth application-default login` y reinicia el contenedor.

Tableros (carpeta de staging):

| Tablero | Para qué sirve en la demo |
|---|---|
| **Staging — HU-W05 Cobros y Conciliación** | «Cobros por estado» (PENDIENTE → COBRADO por el reintento), 5xx en 0 %, latencia con el corte de 700 ms |
| **Staging — HU-W01** | Oferta completa / degradada, caché, consentimiento revocado |
| **Staging — Flujos (trazas)** | Recorrido de una petición entre servicios (BFF → PAYMENTS → ACL → pasarela) y sus logs |
| Servicios / Datos / Logs | Instancias, CPU, memoria, bases y logs |

Las métricas basadas en logs tardan **1–2 minutos** en aparecer. Pon el rango en «últimos 30 min».

## 2. Generar cobros (HU-W05)

### 2.1 Semilla de cobros en cuatro estados

Crea 5 clientes y un cobro por cada uno: 2 `COBRADO`, 1 `RECHAZADO` (reintentable hasta 3 veces) y 2 `PENDIENTE`
(el reintento automático los cobra solo al volver la pasarela a `sano`). Es idempotente: correrlo dos veces no duplica.

```bash
BFF_URL=$GATEWAY_URL \
STUB_PASARELA_URL=$STUB_PASARELA_URL \
USUARIO_BACKOFFICE=<operador-backoffice> CLAVE_BACKOFFICE=<clave> \
TOKEN_PASARELA=$(gcloud auth print-identity-token) \
python3 scripts/sembrar_cobros.py
```

- Para sembrar cobros **nuevos** (repetir la demo del reintento) agrega `SUFIJO=ronda2` (otro texto cada vez; requiere el PR de la semilla mergeado).
- Las credenciales del BACKOFFICE de staging están en Secret Manager (ver `scripts/crear_usuarios.py`).
- Si no pasas `TOKEN_PASARELA`, el script lo pide a `gcloud` solo.
- El script añade `/graphql/web` a `BFF_URL`.

### 2.2 Prueba k6 del cobro que se reintenta (FC-10 / CA-W05-05)

Fase 1 (2 min): pasarela `lento`, 2 cobros por segundo → quedan `PENDIENTE`, sin 5xx. Fase 2: pasarela `sano` → el
reintento (Cloud Tasks) los pasa a `COBRADO`. Umbral: al menos 95 % recuperados.

```bash
STAGING_URL=$GATEWAY_URL \
STUB_PASARELA_URL=$STUB_PASARELA_URL \
TOKEN_PASARELA=$(gcloud auth print-identity-token) \
USUARIO_BACKOFFICE=<operador-backoffice> CLAVE_BACKOFFICE=<clave> \
./scripts/correr_k6.sh cobros-pasarela-lenta.js
```

El resumen queda en `docs/evidencias/` y los crudos en `tests/k6/resultados-crudos/` (no se versionan).

Otros guiones de cobros (`tests/k6/`):

| Guion | Qué demuestra |
|---|---|
| `cobros-pasarela-lenta.js` | Cobro pendiente por pasarela lenta y recuperación automática |
| `cobros-idempotencia.js` | 20 usuarios con el mismo `idIntentoCobro` → exactamente 1 cobro |

### 2.3 Un cobro a mano (para una demo en vivo)

1. Pon la pasarela en `lento` (sección 3).
2. Crea el cobro desde la pantalla de conciliación o con `crearCobro` por GraphQL: queda `PENDIENTE`.
3. Mira el panel «Cobros por estado» y la traza del cobro en «Flujos».
4. Vuelve la pasarela a `sano`: en segundos el reintento lo deja `COBRADO` con **la misma referencia de la pasarela**
   (sin doble cobro).
5. Para un rechazo: pasarela en `rechaza`; `reintentarCobro` se permite hasta 3 veces y al cuarto pide otro medio de pago.

## 3. Inyección de fallas en los proveedores

Los tres stubs (Open Finance, Open Data y la pasarela de pagos) tienen un endpoint de control para cambiar su
comportamiento **en caliente**:

| Modo | Efecto | Stubs |
|---|---|---|
| `sano` | Responde normal | todos |
| `lento` | Responde, pero tarda más que el corte de 700 ms | todos |
| `error` | Responde con error de servidor | todos |
| `caido` | No responde (espera hasta cortar) | todos |
| `rechaza` | La pasarela responde, pero rechaza el cobro (con motivo) | solo pasarela |

Consultar y cambiar el modo (cambia `stub-pasarela` por `stub-open-finance` o `stub-open-data` y su URL):

```bash
TOKEN=$(gcloud auth print-identity-token)

# ver el modo actual
curl -s -H "Authorization: Bearer $TOKEN" $STUB_PASARELA_URL/control/modo

# fijar el modo
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"modo":"lento"}' $STUB_PASARELA_URL/control/modo

# volver a la normalidad
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"modo":"sano"}' $STUB_PASARELA_URL/control/modo
```

Para Open Finance / Open Data:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"modo":"lento"}' $STUB_OPEN_FINANCE_URL/control/modo

TOKEN=$(gcloud auth print-identity-token)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"modo":"caido"}' $STUB_OPEN_DATA_URL/control/modo
```

Por llamada, sin tocar el modo global: query `?modo=lento` o cabecera `X-Modo-Simulado: lento`.

**Qué debe verse (regla 4: nunca un 5xx crudo al cliente):**

| Falla | Resultado esperado en la app / Grafana |
|---|---|
| Open Finance `lento` o `caido` | Oferta **PRELIMINAR** con rango de prima, fuentes marcadas `CACHE`; abre el circuito del ACL |
| Documento sin caché y proveedor caído | `ErrorOferta` con opción de reintento (no un 500) |
| Pasarela `lento` / `caido` / `error` | Cobro `PENDIENTE`, sin 5xx; se reintenta solo |
| Pasarela `rechaza` | Cobro `RECHAZADO` con motivo; reintento manual hasta 3 veces |

**Siempre deja los stubs en `sano` al terminar.** Los circuitos del ACL tardan en cerrarse tras restaurar el stub
(la sonda corre fuera del camino síncrono).

### Guiones k6 de falla de proveedor

```bash
STAGING_URL=$GATEWAY_URL ./scripts/correr_k6.sh falla-proveedor.js          # EC009 / EC010
STAGING_URL=$GATEWAY_URL ./scripts/correr_k6.sh gateway-presupuesto.js      # EC001 / EC002 / EC008
STAGING_URL=$GATEWAY_URL ./scripts/correr_k6.sh perfilamiento-baseline.js   # EC003 / EC004
STAGING_URL=$GATEWAY_URL ./scripts/correr_k6.sh lectura-replica.js          # EC011 / EC012
MODO_REDUCIDO=true STAGING_URL=$GATEWAY_URL ./scripts/correr_k6.sh          # todos, versión corta
```

## 4. Flujo de oferta (HU-W01) por GraphQL

Humo oficial y recorrido completo (oferta completa, degradada, timeout duro, consentimiento revocado):

```bash
python3 scripts/humo_bff.py                       # contra el stack local
docker compose --profile servicios up -d --build --wait   # levantar el stack local
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json     # 61 peticiones, 141 aserciones (local)
```

Detalle en `docs/evidencias/flujo-prueba-local-hu-w01.md` y `tests/newman/README.md`.

## 5. Verificar en GCP lo que Grafana muestra

```bash
# Logs del BFF y de PAYMENTS (errores)
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=payments AND severity>=ERROR' \
  --project $PROYECTO --limit 20 --freshness 1h --format="value(timestamp,textPayload)"

# Colas de reintento y jobs programados
gcloud tasks queues list --location $REGION --project $PROYECTO
gcloud scheduler jobs list --location $REGION --project $PROYECTO

# Revisiones y etiquetas acumuladas (si el deploy falla por cuota de CPU)
gcloud run services describe bff --region $REGION --project $PROYECTO --format="yaml(status.traffic)"
```

## 6. Costos: apagar al terminar

- Staging cuesta aunque nadie lo use (~USD 4 al día en instancias fijas más ~USD 0,12 por hora de Redis y el conector,
  más Cloud SQL).
- **Actions → Costos staging → Run workflow → `apagar`** (o espera al apagado de las 2:00 a.m.).
- Si el apagado falla por el plan (acceso público del BFF pendiente de aplicar), ver la memoria del proyecto y aplicar
  antes **Deploy staging** con `infra=true` y `permitir_destruir=true`.

## Pendiente de verificar

- Los comandos con `curl` a los stubs y la semilla contra staging se escribieron a partir del código; no los corrí
  contra staging en esta sesión.
- El tablero «Cobros por estado» depende de la métrica `logging.googleapis.com/user/solventa_cobros_por_estado`; si
  está vacío, genera tráfico (2.1 o 2.2) y espera 1–2 minutos.
