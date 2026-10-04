# Convenciones del repo

## Ramas y flujo

- **Trunk-based.** `main` está protegida (PR obligatorio, 1 revisión, CI en verde). No hay GitFlow.
- Ramas cortas desde `main`: `KAN-24-consentimiento`, `KAN-28-idempotencia-cobro`. Se integran en horas o pocos días.
- Un tag por sprint al cerrarlo: `sprint-1`, `sprint-2`, `sprint-3` (el mismo nombre en `solventa-frontend`).
- Cada merge a `main` debe dejar el repo desplegable a staging.

## Commits y PR

- **Conventional Commits** con alcance = servicio o carpeta y la clave de Jira: `feat(auth): KAN-24 registra consentimiento append-only`.
  Tipos: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `infra`. Alcances: `bff`, `auth`, `risk`, `rating`, `payments`, `acl`, `stubs`, `contracts`, `infra`, `tests`.
- El título del PR sigue el mismo formato y **lo valida el CI** (`Título del PR`; Dependabot queda exento). El PR usa la plantilla de `.github/pull_request_template.md`.
- Se integra con **squash**: el título del PR es el commit en `main`, y el despliegue a staging compara contra su padre.
- Un PR cuenta una sola historia o habilitadora. Si cruza servicios de backend, sigue siendo un solo PR atómico.
- Un cambio de contrato (`contracts/`) se avisa explícitamente para abrir el PR gemelo en `solventa-frontend`.

## Definición de hecho (propuesta del equipo)

- Código integrado a `main` por PR con revisión de al menos otro integrante.
- Pruebas unitarias del código nuevo y pruebas de contrato de cada adaptador, en verde en el pipeline.
- Si cambia un esquema: migración versionada en el servicio dueño del almacén, compatible hacia atrás, con `downgrade` (Postgres) o idempotente (MongoDB), y `scripts/verificar-migraciones.sh` en verde (ver ADR-07).
- Quality gate de SonarQube aprobado y Dependabot sin vulnerabilidades críticas abiertas.
- Flujo E2E de la historia en verde en staging (Cypress en el frontend).
- Escenarios de calidad medidos con k6 y evidencia guardada con nombre, fecha, escenario y vínculo a la historia (`docs/evidencias/`).
- Métricas visibles en el panel de observabilidad y **sin datos personales en logs**.
- Documentación técnica y tablero de Jira actualizados; historia demostrada en la revisión del sprint.

## Migraciones de base de datos

- Cada servicio migra **solo su almacén** (escritor único): `auth`, `payments` (Alembic) y `risk` (runner de MongoDB). RATING no migra.
- SQL explícito, revisiones secuenciales (`0001`, `0002`…) y **una sola cabeza**. Nunca edites una migración ya integrada a `main`: agrega otra.
- **Expandir y contraer:** la migración corre *antes* de que el código nuevo reciba tráfico, con el código anterior aún atendiendo.
  Agregar es seguro; renombrar o borrar se hace en dos despliegues (primero el código deja de usarlo, luego se elimina).
- Sin datos personales ni secretos en migraciones ni en sus logs. Fixtures sin datos reales.

## Despliegue

- `main` → staging automático **solo si CI pasó** y solo de lo que cambió; una imagen por commit (etiqueta = SHA). Ver `deploy-staging.yml`.
- Un despliegue fallido antes de "promover" no afecta a staging. Para volver atrás: *Rollback staging* (no revierte la base; se corrige hacia adelante).
- Terraform **no** se aplica desde el pipeline. Los servicios se declaran en `.github/servicios.json` y `python scripts/validar_servicios.py` debe pasar.

## Código

- Python 3.12, `ruff` (config en `pyproject.toml`), pruebas con `python -m pytest` desde la carpeta del servicio.
- Dependencias con versión fija en `requirements.txt` por servicio; Dependabot las actualiza.
- Un servicio no importa código de otro: se comunican por HTTP o eventos, y comparten **contratos**, no librerías.
- Configuración por variables de entorno. Nunca secretos en el repo (`.env` ignorado; en nube, Secret Manager).
- Logs estructurados y **sin datos personales**: nada de documento de identidad, correo, nombre ni `cliente_id` en claro en logs o etiquetas de métricas.

## Pruebas (pirámide del equipo: 70 % unitarias e integración, 20 % E2E, 10 % exploratorias)

- Unitarias: dominio y reglas (RATING con `pytest`, sin red).
- Contrato: la **misma suite** corre contra el stub y contra el sandbox real del proveedor (EC031).
- Carga y escenarios: k6 en `tests/k6/`. Los guiones de los experimentos pasan a ser pruebas de regresión en cada sprint.
- Fixtures sin datos personales reales.

## Documentos

- Decisiones de arquitectura: `docs/adr/ADR-NN-titulo.md` (la numeración sigue la de los ADR-01 a ADR-05 del documento de arquitectura).
- Evidencia de escenarios: `docs/evidencias/<EC>-<fecha>.md`.
- Idioma: español.
