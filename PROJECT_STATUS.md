# Estado del proyecto: Solventa Backend

Corte: **2026-10-31**, `develop` en `8054b6b`. Es un resumen para arrancar sesiones: el detalle vive en los documentos
enlazados. Reglas de arquitectura y de trabajo: [`CLAUDE.md`](CLAUDE.md) (no se repiten aquí). Si este archivo y el
código difieren, gana el código.

## Propósito y arquitectura

Aseguradora digital sobre Open Finance y Open Data (Proyecto Final 2, MISW4501). Este repo construye el producto; la
web y el móvil viven en `solventa-frontend` y los experimentos, congelados, en `../solventa-arquitectura`.

- **Entrada:** Envoy (`:8080`) → BFF GraphQL por canal (`/v1/graphql/{web,app,movil}`) + API REST `/v1` de socios.
- **Comunicación:** síncrona solo en el camino crítico; el resto por Pub/Sub. Los disparadores van por Cloud
  Tasks/Scheduler porque en Cloud Run no corren bucles de fondo.
- **Proveedores externos:** solo a través de `acl-worker` (hexagonal, Circuit Breaker, timeout de 700 ms).

| Servicio | Puerto | Almacén y papel |
|---|---|---|
| `bff` | 8000 | GraphQL y REST de socios |
| `auth` | 8001 | Postgres: identidad, consentimiento, socios (ES256/JWKS) |
| `risk` | 8002 | Mongo: perfil de riesgo, batch nocturno |
| `under` | 8008 | Suscripción |
| `almacenamiento` | 8009 | Postgres + GCS: URLs firmadas, catálogo de tipos |
| `onboarding` | 8010 | Postgres: verificación de identidad (Didit detrás del ACL) |
| `avisos` | 8011 | Tiempo real sobre Redis |
| `claims` | 8012 | Mongo: mesa de siniestros |
| `rating`, `payments`, `policy`, `acl-worker`, `consolidador-fuentes` | ver `.github/servicios.json` | Rating lee la secundaria de Riesgo y no migra; Payments y Policy usan Postgres |

Stubs: `firma`, `kyc`, `notificaciones`, `open-data`, `open-finance`, `pasarela`.
Inventario y puertos: `.github/servicios.json` (fuente única).

## Stack

Python 3.14 en imágenes (`python:3.14-slim`), FastAPI, Strawberry, Alembic. PostgreSQL, MongoDB (Atlas M0 en staging),
Redis, Pub/Sub. GCP `us-central1`, proyecto `solventa-511815`: Cloud Run, Cloud SQL, Memorystore, KMS, GCS, Tasks,
Scheduler. Terraform aplicado desde el pipeline, GitHub Actions, Docker Compose, k6, Newman, Grafana.

**Local:** las pruebas corren en un contenedor Python 3.14 (el equipo tiene 3.12). Si el puerto 5432 está ocupado:
`POSTGRES_PORT=55432`.

## Funcionalidad hecha

Todo esto está en código y verificado **en local**; nada tiene medición de staging.

| Historia | Alcance | Plan |
|---|---|---|
| HU-W01 / KAN-24 | Oferta, consentimiento, caché del score, cifrado, batch, gateway | [`plan-completar-hu-w01`](docs/plan-completar-hu-w01.md) |
| HU-W05 / KAN-28 | Cobros, reintentos, conciliación | [`plan-completar-hu-w05`](docs/plan-completar-hu-w05.md) |
| HU-W03 / KAN-26 | Suscripción, firma por enlace, pasarela simulada, cobro síncrono, emisión, PDF por correo | [`plan-completar-hu-w03`](docs/plan-completar-hu-w03.md) |
| HU-W04 / KAN-27 | Mesa de siniestros en `claims`; orden de indemnización sin dispersión | [`plan-completar-hu-w04`](docs/plan-completar-hu-w04.md) |
| HU-W07 / KAN-30 | API de socios, rol `SOCIO`, presupuesto, comisión, modo prueba, salud | [`plan-completar-hu-w07`](docs/plan-completar-hu-w07.md) |
| HU-M01 / KAN-31 | Onboarding móvil con verificación de identidad | [`services/onboarding/README.md`](services/onboarding/README.md) |
| KAN-250 | Almacenamiento de archivos | [ADR-10](docs/adr/ADR-10-almacenamiento-de-archivos.md) |

Contrato en **v0.22**; la siguiente libre es **v0.23**. Últimas migraciones: AUTH `0010`, POLICY `0008`, PAYMENTS `0010`, RISK `0004`.

## Estado actual

- **Ramas:** `main` y `develop` están casi iguales. `develop` solo lleva 2 bumps de Dependabot encima de
  `release/sprint-4` (#397, 2026-10-29).
- **Staging, bloqueado:** el último `Deploy staging` (2026-10-30, run `41135427073`) falló en `migrar (almacenamiento)`;
  las otras migraciones se cancelaron y `desplegar` se omitió. **Lo nuevo no se desplegó.** El último exitoso es del
  2026-10-29 12:58 (`40877773342`).
- **Causa (diagnosticada 2026-10-31):** Cloud SQL estaba detenida. `Costos staging` > `encender` había fallado
  (run `41130261877`) porque su guardia bloqueó la destrucción pendiente de `stub-siniestros`. Ese despliegue sí aplicó
  Terraform (24 creados, 5 cambiados, 6 destruidos), así que `encender` ya no debería chocar con eso (sin probar).
  Los logs se leen con `gh api .../actions/jobs/<id>/logs`.
- **PR abiertos:** solo los de Dependabot, que suben las imágenes a Python 3.15 (#402 en adelante). No tocarlos.

## Pendiente

**Staging** (validación con infraestructura aplazada a una fase posterior; por ahora solo código)
- Medir EC003/EC004/EC005/EC007/EC008-EC012/EC017 y FC-10 (los guiones k6 de FC-10 están sin correr).
- Verificar Cloud KMS y Cloud Tasks reales; firma IAM, avisos del bucket, CMEK y CORS del almacenamiento; invalidación
  activa de caché al revocar (CA-W01-08).
- Revisar los resultados de Bandit, `pip-audit`, Trivy y ZAP (informativos en CI).
- W07: `crear_gateway_socios`, `jwks_socios_url`, Job `crear_socio`.
- `usar_cmek_sql` y `usar_cmek_redis` siguen en `false`.

**Producto**
- Integrar W07 con el portal de Daniel y pasar al plan del front el contrato v0.22, el rol SOCIO y el modo prueba.
- Pantalla web para que el cliente deje su contacto (W03-17). Hasta entonces, avisos omitidos con `SIN_CONTACTO`.
- Renovar en la web la URL firmada vencida de las evidencias; geoetiqueta desde EXIF (con HU-M04).
- ESC-001 (Sprint 2 según el plan; confirmar si sigue abierto). Tableros de W05.

**Decisiones abiertas**
- D-01 (base de velocidad).
- Visto bueno de Frans sobre el cambio W05-3 (indemnización sin dispersión).
- Resolución de propuestas en `PAGO_RECHAZADO` o `PENDIENTE_PAGO` agotado: hoy nadie las reabre.
- Origen real de `tokenMedioPago` cuando la prima va con la cuota del crédito.
- W03-7: Pub/Sub sin ejecutar.
- Cambios en Jira de W07.

## Bugs y deuda técnica

- **EC004 ❌:** el pico de 3× satura el BFF de un solo proceso (57 % de 504).
- **EC008 ❌:** p95 de 126 ms contra 120 ms; el stub sano ya tarda hasta 120 ms.
- El BFF no cancela el trabajo que el gateway ya cortó (cola de ~1 min tras el pico).
- `PoolTimeout` permanente del BFF hacia el ACL, visto una vez y sin causa.
- Atlas M0 admite ~100 operaciones/s: el batch no llegaría a EC017 en staging.
- Los circuitos viven en la memoria de cada instancia, lo que limita la sonda por Scheduler.
- Cobros atascados: falta `FOR UPDATE SKIP LOCKED`; no hay doble cobro, pero pueden contarse dos intentos.
- EC031: falta correr el stack con `PROVEEDOR_PASARELA=alterna`.
- `ruff` apunta a `py312` pero las imágenes usan 3.14.
- `cobros.poliza_id` guarda el id de la **propuesta**: la tabla no muestra el número de póliza y el balance por póliza real no ve esos cobros.
- `contactarAsesor` (CA-W01-07) sigue siendo un mock que solo confirma.
- Memorias locales viejas: almacenamiento y M01 («borrador») y W07 («sin PR»).

## Decisiones importantes

- Escritor único por almacén; cada servicio migra solo lo suyo, con migraciones compatibles hacia atrás.
- Construir → migrar → arrancar, igual en local, CI y staging ([ADR-07](docs/adr/ADR-07-cicd-y-migraciones.md)).
- Git-flow: las ramas salen de `develop` y vuelven a `develop`; solo release y hotfix van a `main`, que es lo único que
  despliega ([ADR-08](docs/adr/ADR-08-git-flow.md), [ADR-09](docs/adr/ADR-09-terraform-desde-el-pipeline.md)).
- Contratos primero y solo aditivos (`contracts/CHANGELOG.md`).
- Cifrado por campo + sobre con KMS (D-04); CMEK salvo Atlas M0.
- W03: cobro síncrono, solo el correo va asíncrono; solo tarjeta (sin PSE); la pasarela es un checkout alojado externo.
- W03-16: AUTH guarda el contacto cifrado del CLIENTE, legible solo por POLICY; el aviso lleva el PDF.
- W07-20: el socio tiene rol propio `SOCIO`; se da de alta solo por CLI o semilla.
- M01: el usuario nace `PENDIENTE_VERIFICACION`; biometría borrada 30 días después de cerrar la verificación.
- Sin coautoría de Claude en commits ni PR. `terraform apply` y recursos facturables solo con confirmación explícita.

## Próximos pasos exactos

Punto de retoma (2026-10-31): los ajustes de W01 y W05 están integrados en `develop` y verificados en vivo con el compose
(evidencias del 2026-10-31). `develop` backend y web llevan más cambios del equipo (M01, Sentry). **Nada de esto se ha
probado en staging.**

1. **Liberar y desplegar**, cuando se decida. Confirmar antes `clave-firma-socios` (sin ella AUTH no arranca) y encender
   con `Costos staging` > `encender`: Cloud SQL detenida frenó el último despliegue. PAYMENTS usa `redis-url-w03`.
2. **Revisar en staging** que RISK ahora escribe caché en `redis-url-rating`, y medir con k6 (EC003, EC004, EC005, EC007, EC010).
3. **Atacar EC004 y EC008**, y encender el gateway de socios con el portal de Daniel.

**Stub de la pasarela:** `4000 0000 0000 0341` se aprueba en la página y se rechaza en los cobros con token.

**Cypress contra el stack real:** `solventa-frontend-web/e2e/cypress/integracion/README.md`.

**Acuerdos de trabajo:** por ahora solo validación por código, sin lanzar infraestructura. Commit, push y PR sin pedir aprobación, sin coautoría; el título del PR debe cumplir `tipo(alcance): KAN-N descripción` (`gh pr edit` falla por Projects classic: usar `gh api -X PATCH`). Antes de un PR de la web, correr `npm run formato:verificar`. Jira lo actualiza Felipe (CA-W01-08 ampliado y criterio nuevo de KAN-28).
