# Solventa — Backend (contexto de trabajo)

Backend de Solventa (aseguradora digital sobre Open Finance y Open Data), Proyecto Final 2 de MISW4501. Este repo
construye el **producto**; la evidencia de los experimentos de arquitectura vive en `../solventa-arquitectura`
(congelado, no se le agrega código de producto) y la web/móvil en `solventa-frontend` (ver
[ADR-06](docs/adr/ADR-06-estrategia-de-repositorios.md)).

Mapa del repo: [`README.md`](README.md). Alcance del sprint: [`docs/sprint-1.md`](docs/sprint-1.md).
Arquitectura: [`docs/arquitectura-backend.md`](docs/arquitectura-backend.md). Convenciones: [`docs/convenciones.md`](docs/convenciones.md).

## Estado (2026-10-03)

- Repo **recién estructurado**: esqueletos de servicios y stubs (solo `/health` y `/metrics`), contratos v0, entorno local con Docker Compose, CI base y agentes. **Aún no hay lógica de negocio.**
- Sprint 1 (tentativo): 12–25 de octubre de 2026 — HU-W01 (KAN-24, 20 pts) y HU-W05 (KAN-28, 6 pts) con 7 habilitadoras. Holgura de solo 3 h: no se sobre-construye.
- **Decisiones pendientes de confirmar en el Planning** (recomendación del plan entre paréntesis; no las trates como cerradas): D-01 base de velocidad (recálculo 27/27/44), D-02 read-your-writes (sesión causal `afterClusterTime`), D-04 cifrado (campo + Cloud KMS), D-05 proveedores (stubs por contrato), D-06 adelantar HU-W05 al Sprint 1. D-03 (cobro antes o después de emitir) se cierra en el Sprint 2.
- GitHub: organización `solventa-app` (`origin` = https://github.com/solventa-app/solventa-backend.git, repo vacío; aún sin commit ni push). Sin proyecto GCP definido todavía: no hay despliegue ni pipeline de deploy.

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
- **Commits y PR:** ver `docs/convenciones.md` (Conventional Commits con la clave de Jira, trunk-based, 1 revisión).
- **Español** en documentación, comentarios y mensajes de commit; los identificadores de código pueden ir en español (dominio) o inglés (técnico), pero consistentes dentro de un servicio.
