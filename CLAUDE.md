# Solventa — Backend (contexto de trabajo)

Backend de Solventa (aseguradora digital sobre Open Finance y Open Data), Proyecto Final 2 de MISW4501. Este repo
construye el **producto**; la evidencia de los experimentos de arquitectura vive en `../solventa-arquitectura`
(congelado, no se le agrega código de producto) y la web/móvil en `solventa-frontend` (ver
[ADR-06](docs/adr/ADR-06-estrategia-de-repositorios.md)).

Mapa del repo: [`README.md`](README.md). Alcance del sprint: [`docs/sprint-1.md`](docs/sprint-1.md).
Arquitectura: [`docs/arquitectura-backend.md`](docs/arquitectura-backend.md). Convenciones: [`docs/convenciones.md`](docs/convenciones.md).

## Estado (2026-10-14)

- **Código real en `develop`:** `acl-worker` (puertos, Circuit Breaker, sonda), `consolidador-fuentes`, `risk`, `rating` (oferta con Cache-Aside y lectura causal), `payments` (cobros idempotentes y reintento) y los stubs de proveedores. El **BFF** implementa el contrato GraphQL de HU-W01 (`solicitarOferta`, consentimiento) y orquesta AUTH → ACL Worker → RISK → RATING (PR #76; ver [`docs/bff-implementacion.md`](docs/bff-implementacion.md)). **AUTH** solo tiene un **mock en memoria** del consentimiento.
- **Pendiente conocido:** consentimiento persistente en Postgres (T-W01-4), cobros en el BFF y listado/reintento en PAYMENTS (KAN-28; hoy el BFF responde «no disponible»), token de identidad de PAYMENTS y del consolidador hacia el ACL, cifrado de campo (SEG-002), k6 de EC003/EC004 (`tests/k6/` solo tiene su README) y todo el despliegue a GCP.
- **Pruebas:** `python -m pytest` por servicio (desde su carpeta), `python scripts/humo_bff.py` contra el compose y la colección Newman de [`tests/newman/`](tests/newman/README.md).
- **CI/CD y migraciones armados (ADR-07, 2026-10-04):** migraciones (Alembic en `auth`/`payments`, runner de MongoDB en `risk`), CI con migraciones contra almacén real + humo del stack + gitleaks + actionlint, `deploy-staging.yml` y `rollback-staging.yml` (apagados hasta definir el proyecto GCP) y Terraform de WIF/Cloud Run detrás de banderas. Probado en local; **lo de GCP no se ha ejecutado nunca** (CI, rulesets y checks de PR ya corren en GitHub).
- Sprint 1 (tentativo): 12–25 de octubre de 2026 — HU-W01 (KAN-24, 20 pts) y HU-W05 (KAN-28, 6 pts) con 7 habilitadoras. Holgura de solo 3 h: no se sobre-construye.
- **Decisiones pendientes de confirmar en el Planning** (recomendación del plan entre paréntesis; no las trates como cerradas): D-01 base de velocidad (recálculo 27/27/44), D-02 read-your-writes (sesión causal `afterClusterTime`), D-04 cifrado (campo + Cloud KMS), D-05 proveedores (stubs por contrato), D-06 adelantar HU-W05 al Sprint 1. D-03 (cobro antes o después de emitir) se cierra en el Sprint 2.
- GitHub: organización `solventa-app` (`origin` = https://github.com/solventa-app/solventa-backend.git, rama predeterminada `develop`, rulesets aplicados en `main` y `develop`, flujo git-flow de ADR-08). Sin proyecto GCP definido todavía: el pipeline de deploy existe pero está apagado (ver `infra/README.md`); no hay nada desplegado.

## Stack y decisiones ya tomadas (ADR-01 a ADR-05 del documento de arquitectura)

- Microservicios por dominio con un BFF GraphQL único y una ruta por canal (`/graphql/web`, `/graphql/app`); comunicación híbrida: **síncrona solo en el camino crítico del usuario**, todo lo demás por eventos (Pub/Sub).
- Python 3.12 + FastAPI (como los experimentos). BFF GraphQL con Strawberry (confirmado al construirlo).
- Persistencia políglota: **PostgreSQL** (auth, payments, policy), **MongoDB** (risk, claims; réplica de lectura para rating), **Redis** (caché derivada y desechable).
- GCP como única nube, Cloud Run por defecto. Una sola región (`us-central1`).

## Reglas de arquitectura (no las reinterpretes)

1. **Escritor único por almacén.** AUTH escribe identidad, RISK escribe riesgo, PAYMENTS escribe cobros. **RATING nunca escribe en Riesgo**: lee de la secundaria.
2. **El ACL Worker es el único punto de contacto con proveedores externos.** Se estructura como puertos y adaptadores (hexagonal) con Circuit Breaker (`purgatory`) y timeout duro de **700 ms** (EC009/EC010). Los servicios de negocio **no** llaman a un proveedor directamente.
3. **Hexagonal solo donde el diseño lo pide** (`acl-worker`). Los stubs y el resto de servicios son capas simples: no metas puertos y adaptadores por reflejo.
4. **Nunca un 5xx crudo al cliente** por una falla de proveedor: se degrada a oferta preliminar, cobro pendiente o error con reintento.
5. **La sonda de recuperación del circuito corre fuera del camino síncrono** (lección del Experimento 1: si la hace una petición real, el usuario paga el timeout).
6. **Sin datos personales en logs ni en etiquetas de métricas.** Datos personales y financieros cifrados en reposo (campo + KMS) y en tránsito. Cero datos de tarjeta: solo el token del proveedor PCI-DSS.
7. **Sin consentimiento vigente no se consulta ninguna fuente.** El consentimiento se registra (append-only) **antes** de consultar.
8. **Contratos primero.** Un cambio de comportamiento que cruza servicios o llega al frontend se refleja primero en `contracts/` (solo cambios aditivos, ver `contracts/README.md`) y se anota en `contracts/CHANGELOG.md`.
9. **Los umbrales de los escenarios son un piso, no un valor de producción:** se miden en staging con proveedores simulados.

## CI/CD y migraciones (ADR-07)

- **Mismo camino en local, CI y staging:** construir la imagen una vez (etiqueta = SHA) → **migrar** → arrancar. Nada de migrar al arrancar la app.
- **Cada servicio migra solo su almacén** (escritor único): `auth`, `payments`, `risk`. RATING no migra.
- **Migraciones compatibles hacia atrás** (expandir/contraer): corren mientras la revisión anterior aún atiende. Postgres con `downgrade` probado en CI; MongoDB idempotentes. Hacia adelante en staging, nunca bajar el esquema.
- **`.github/servicios.json` es la fuente única de servicios.** Un servicio nuevo se agrega ahí y `python scripts/validar_servicios.py` dice qué más falta.
- **Terraform no se aplica desde el pipeline** y el despliegue sigue apagado hasta que existan las variables de `infra/README.md` (incluida `GCP_PROTECCION_GASTO`).
- Una migración nueva se verifica con `bash scripts/verificar-migraciones.sh <servicio>` antes de abrir el PR.

## Agentes de este repo (`.claude/agents/`)

- **`backend-builder`** — construir servicios, adaptadores y stubs de una historia del sprint.
- **`contract-keeper`** — dueño de `contracts/`: esquema GraphQL, eventos y su bitácora.
- **`quality-engineer`** — pruebas (pytest, contrato, k6), verificación de escenarios de calidad y evidencia.
- **`infra-engineer`** — Terraform, GitHub Actions, Docker y control de costos en GCP.

## Reglas de trabajo

- **No sobre-construir.** Cada tarea se traza a una historia, una habilitadora o un escenario de `docs/sprint-1.md`. Si no se traza, no se construye.
- **Verifica en vivo antes de reportar éxito:** corre las pruebas, levanta el compose, muestra números reales.
- **Reutiliza los experimentos copiando, no enlazando.** Anota en el README del servicio el commit de origen (`solventa-arquitectura@11e4be6`). Sin submódulos.
- **Costos:** nunca ejecutes `terraform apply`/`destroy` ni crees recursos facturables sin confirmación explícita del usuario. La protección de gasto de `../solventa-arquitectura/proteccion-costos/` debe estar activa en el proyecto antes de desplegar.
- **Commits y PR:** ver `docs/convenciones.md` (Conventional Commits con la clave de Jira, git-flow `main → develop → feature` según ADR-08, 1 revisión). Las ramas de trabajo salen de `develop` y vuelven a `develop`; nunca abras un PR de trabajo hacia `main`.
- **`main` y `develop` están protegidas:** nada llega por push directo; todo pasa por PR con revisión y checks (los rulesets no tienen excepciones). No hagas `git push` a `develop` ni a `main`, ni fuerces, ni integres PRs (`gh pr merge`): integrar es decisión de una persona.
- **Reintegrar `main` en `develop` es un paso manual.** Nunca lo intentes: ni `git merge main`, ni `git pull origin main`, ni abrir o integrar el PR `main` → `develop`. Si el flujo lo requiere (tras un release o hotfix), avisa a la persona y detente: debe integrarse con *merge commit* y revisión humana (ADR-08), y un *squash* rompe la ascendencia. Los permisos de `.claude/settings.json` (`deny`) lo refuerzan, pero la regla vale aunque un patrón no lo atrape.
- **Español** en documentación, comentarios y mensajes de commit; los identificadores de código pueden ir en español (dominio) o inglés (técnico), pero consistentes dentro de un servicio.
