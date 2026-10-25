# Solventa — Backend (contexto de trabajo)

Backend de Solventa (aseguradora digital sobre Open Finance y Open Data), Proyecto Final 2 de MISW4501. Este repo
construye el **producto**; la evidencia de los experimentos de arquitectura vive en `../solventa-arquitectura`
(congelado, no se le agrega código de producto) y la web/móvil en `solventa-frontend` (ver
[ADR-06](docs/adr/ADR-06-estrategia-de-repositorios.md)).

Mapa del repo: [`README.md`](README.md). Alcance del sprint: [`docs/sprint-1.md`](docs/sprint-1.md).
Arquitectura: [`docs/arquitectura-backend.md`](docs/arquitectura-backend.md). Convenciones: [`docs/convenciones.md`](docs/convenciones.md).

## Estado (2026-10-18)

- **Sprint 1 completo en código (HU-W01 / KAN-24 y HU-W05 / KAN-28):** todos los servicios del Sprint 1 (`bff`, `auth`, `acl-worker`, `risk`, `rating`, `payments`, `consolidador-fuentes` y los 3 stubs) están construidos y probados, junto con los 10 bloques de [`docs/plan-completar-hu-w01.md`](docs/plan-completar-hu-w01.md). **Lo que falta es la medición contra staging**, no código (ver abajo).
  - **Bloque 1 (Medición):** guiones de k6 (`tests/k6/`, con `lib/preparar.js`), `scripts/correr_k6.sh`, tableros `hu-w01.json` y `hu-w05.json`, paneles del gateway y de métricas basadas en logs (`infra/metricas-logs.tf`, `crear_metricas_logs`), y `grafana-gcp` solo en `127.0.0.1:3200`. Hay corridas **locales reales** en `docs/evidencias/` (cada informe dice que no son de staging).
  - **Bloque 2 (Caché del score y EC010 ≤ 500 ms):** `ScoreCacheado` escrito en Redis desde RISK y leído en RATING; presupuesto con caché de 300 ms y `CACHE_SERVIR_EN_TTL` en el ACL Worker.
  - **Bloque 3 (Cifrado en reposo, D-04 / EC022):** cifrado AES-GCM por campo + sobre con Cloud KMS en `auth`, `risk` y `rating`. Verificado en local con bases reales; **Cloud KMS real y CMEK de Cloud SQL/Memorystore sin probar** (CMEK desactivado en `staging.tfvars`).
  - **Bloque 4 (Consentimiento con titular y vencimiento 30 días):** migración `0005` en AUTH, validación del titular en el BFF y `ErrorOferta.motivo`.
  - **Bloque 5 (Fuentes por configuración, EC031 / EC033):** `contracts/fuentes.json` es la fuente de verdad; cada imagen lleva su copia `app/fuentes.json` (`scripts/sincronizar_fuentes.py`, verificado en el CI).
  - **Bloque 6 (Seguridad en CI/CD):** Bandit, `pip-audit`, Trivy y ZAP integrados en `ci.yml` (informativos). **Aún sin resultados revisados.**
  - **Bloque 7 (Revocación de consentimiento, CA-W01-08):** `POST /fuentes/invalidar-cache` en el ACL al revocar desde el BFF. La invalidación activa quedó **sin verificar en vivo**.
  - **Bloque 8 (Pendientes de diseño):** `Oferta.perfilRiesgo` (contrato v0.8 del plan), Pub/Sub `perfil-actualizado` (RISK publica, RATING suscrito).
  - **Bloque 9 (Batch nocturno, EC017):** `batch_reproceso.py` con reparto entre tareas (`batch_tareas`). **Medido solo en local y a escala reducida (2026-10-24)**: ~2.600 perfiles/s por tarea y p95 de EC003 sin cambio (`docs/evidencias/EC017-2026-10-18.md`). **En staging no se ha medido y Atlas M0 admite solo 100 operaciones por segundo** (según su documentación): con M0 el batch no llegaría a EC017.
  - **Bloque 10 (API Gateway, HA-LAT-004):** Envoy `:8080` en Compose, `infra/gateway*`; el humo del CI pasa por el gateway.
- **Mediciones reales locales (2026-10-18) y qué dicen:** EC003 ✅ (p95 305 ms), **EC004 ❌** (el pico de 3× satura el BFF de un solo proceso: 57 % de 504), EC009/EC010 ✅, EC011/EC012 ✅, EC001/EC002 ✅ y **EC008 ❌** (p95 por dependencia 126 ms vs 120 ms; el stub «sano» ya tarda hasta 120 ms). Hallazgos abiertos: el BFF no cancela el trabajo que el gateway ya cortó (cola de ~1 min tras el pico) y un `PoolTimeout` permanente del BFF hacia el ACL visto una vez. Detalle en `docs/evidencias/`.
- **Plan y decisiones cerradas de HU-W01 (2026-10-18):** [`docs/plan-completar-hu-w01.md`](docs/plan-completar-hu-w01.md) (backend; el del front está en `solventa-frontend-web/docs/plan-completar-hu-w01.md`). Léelo antes de tocar W01, cifrado, observabilidad o seguridad del CI. D-04 quedó cerrada ahí (campo + Secret Manager envuelto por KMS, CMEK salvo Atlas M0). Guardar la oferta pasa al Sprint 2 con HU-W03. En el Sprint 1 entran también el API Gateway (LAT-004, bloque 10) y el batch nocturno (ESC-004, bloque 9); ESC-001 queda en el Sprint 2.
- **Plan de HU-W05 (2026-10-18):** [`docs/plan-completar-hu-w05.md`](docs/plan-completar-hu-w05.md). Léelo antes de tocar cobros, PAYMENTS o la pasarela. Su bloque 0 (decidido): el reintento de cobros, la sonda del circuito y el consolidador se disparan con Cloud Tasks y Cloud Scheduler, porque en Cloud Run (`cpu_idle = true`) los bucles de fondo no corren.
- **HU-W03 (KAN-26) hecha en código y en local (2026-10-24):** [`docs/plan-completar-hu-w03.md`](docs/plan-completar-hu-w03.md) (léelo antes de tocar suscripción, firma, cobro, emisión, el PDF o los avisos). Bloques 0 a 8 hechos; de la fila 9 del bloque 7 solo falta lo que necesita staging. **Staging corre el Sprint 1** (`main` está muy atrás de `develop`): faltan una liberación, el `terraform plan` completo (pide la llave de MongoDB Atlas), Cloud Tasks real, EC005/EC007 allí y Cloud KMS real. **El aviso de póliza lleva el PDF por correo** (decisión W03-16): AUTH guarda el correo y el teléfono de los CLIENTE, cifrados y solo legibles por POLICY, y el proveedor de notificaciones recibe el PDF. **Hoy ningún cliente tiene contacto y su aviso se omite (`SIN_CONTACTO`, W03-18) hasta que Web construya la pantalla donde lo dejan (W03-17).**
- **Decisiones del Planning:** D-02, D-04, D-05 y D-06 **cerradas** (2026-10-18). Sigue abierta **D-01** (base de velocidad); D-03 y guardar la oferta pasan al Sprint 2 (HU-W03).
- GitHub: organización `solventa-app` (`origin` = https://github.com/solventa-app/solventa-backend.git). Proyecto GCP de staging: `solventa-511815` (ver `infra/README.md`).

## Stack y decisiones ya tomadas (ADR-01 a ADR-05 del documento de arquitectura)

- Microservicios por dominio con BFF por canal; comunicación híbrida: **síncrona solo en el camino crítico del usuario**, todo lo demás por eventos (Pub/Sub).
- Python 3.12 + FastAPI (como los experimentos). BFF GraphQL con Strawberry (propuesta de implementación, confirmar al construirlo).
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
- **Terraform se aplica desde el pipeline** (ADR-09): un merge a `main` (release u hotfix; `develop` no despliega) que cambia `infra/` o `.github/servicios.json` aplica antes de construir y desplegar, con un guardia que bloquea destrucciones (`scripts/revisar_plan.py`). Las banderas de costo viven en `infra/staging.tfvars`. El despliegue sigue apagado hasta que existan las variables de `infra/README.md` (incluida `GCP_PROTECCION_GASTO`). El agente no aplica a mano sin confirmación.
- Una migración nueva se verifica con `bash scripts/verificar-migraciones.sh <servicio>` antes de abrir el PR.

## Agentes de este repo (`.claude/agents/`)

- **`backend-builder`** — construir servicios, adaptadores y stubs de una historia del sprint.
- **`contract-keeper`** — dueño de `contracts/`: esquema GraphQL, eventos y su bitácora.
- **`quality-engineer`** — pruebas (pytest, contrato, k6), verificación de escenarios de calidad y evidencia.
- **`infra-engineer`** — Terraform, GitHub Actions, Docker y control de costos en GCP.

## Reglas de trabajo

- **No sobre-construir.** Cada tarea se traza a una historia, una habilitadora o un escenario de `docs/sprint-1.md`. Si no se traza, no se construye.
- **Al ejecutar un bloque de `docs/plan-completar-hu-w01.md`**, actualiza en el mismo PR los documentos que lista su tabla "Documentos que actualiza cada bloque". Si una decisión cerrada de ese plan choca con otro documento, gana el plan y corriges el documento.
- **Verifica en vivo antes de reportar éxito:** corre las pruebas, levanta el compose, muestra números reales.
- **Reutiliza los experimentos copiando, no enlazando.** Anota en el README del servicio el commit de origen (`solventa-arquitectura@11e4be6`). Sin submódulos.
- **Costos:** nunca ejecutes `terraform apply`/`destroy` ni crees recursos facturables sin confirmación explícita del usuario. La protección de gasto de `../solventa-arquitectura/proteccion-costos/` debe estar activa en el proyecto antes de desplegar. Lo que cobra por hora (Cloud SQL, Redis y su conector) lo apaga y enciende el equipo con el workflow `Costos staging` (`infra/README.md`, «Apagar y encender lo que cuesta»); no hace falta pedírselo al agente.
- **Commits y PR:** ver `docs/convenciones.md` (Conventional Commits con la clave de Jira, git-flow `main → develop → feature` según ADR-08, 1 revisión). Las ramas de trabajo salen de `develop` y vuelven a `develop`; nunca abras un PR de trabajo hacia `main`.
- **Español** en documentación, comentarios y mensajes de commit; los identificadores de código pueden ir en español (dominio) o inglés (técnico), pero consistentes dentro de un servicio.
