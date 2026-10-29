# Convenciones del repo

## Ramas y flujo (git-flow, ADR-08)

```
feature (feat|fix|chore/KAN-N-…) ──squash──► develop ──► release/sprint-N ──merge──► main ──► tag sprint-N
                                                ▲                                      │
                                                └────────── merge (reintegrar) ◄───────┘
                                       hotfix/KAN-N-… ──merge──► main  (y main ──► develop)
```

| Rama | Rol | Vive | Entra por | Método de merge |
|---|---|---|---|---|
| `main` | Lo liberado. Cada commit es un release o un hotfix, etiquetado `sprint-N` en los releases. **Despliega a staging** | siempre | PR desde `release/sprint-N` o `hotfix/KAN-N-…` | **merge commit** |
| `develop` | Integración: lo próximo a liberar. No despliega | siempre | PR desde una rama de trabajo; PR desde `main` para reintegrar | **squash** (ramas de trabajo) / **merge commit** (reintegración desde `main`) |
| `feat|fix|chore/KAN-N-descripcion` | Una historia o habilitadora | horas o pocos días | sale de `develop` y vuelve a `develop` | squash |
| `release/sprint-N` | Estabilización del cierre del sprint: solo correcciones | pocos días | sale de `develop` | merge commit hacia `main` |
| `hotfix/KAN-N-descripcion` | Corrección urgente sobre lo liberado | horas | sale de `main` | merge commit hacia `main` |

**Rutina diaria**

1. `git switch develop && git pull`, luego `git switch -c feat/KAN-24-consentimiento`.
2. PR **hacia `develop`**; título `feat(auth): KAN-24 registra consentimiento append-only`; squash al aprobarse.
3. Al integrarse, CI corre sobre `develop`. No se despliega: staging recibe lo que llega a `main` con el cierre de sprint. GitHub borra la rama de trabajo.

**Cierre de sprint**

1. `git switch -c release/sprint-N develop` y subirla. Desde ahí `develop` sigue recibiendo trabajo del sprint siguiente; el release solo recibe PRs `fix|chore/KAN-N-…` hacia `release/sprint-N`.
2. PR `release/sprint-N` → `main` (merge commit, 1 aprobación, checks en verde). Se pone el tag `sprint-N` sobre el commit de `main`.
3. PR `main` → `develop` (merge commit) para reintegrar las correcciones hechas en el release. **No se omite**: sin él, `develop` pierde esas correcciones.
4. Se borra `release/sprint-N`.

**Hotfix:** `hotfix/KAN-N-…` desde `main` → PR a `main` (merge commit) → PR `main` → `develop`.

**Qué hace cumplir cada pieza**

- `main` y `develop` tienen ruleset (`.github/rulesets/main.json`, `develop.json`): PR obligatorio, 1 aprobación (se descarta si hay push nuevo), hilos resueltos, checks `CI OK`, `Título del PR` y `Nombre de rama` en verde, sin force-push ni borrado y sin excepciones (ni para administradores). Solo `develop` admite squash; `main` solo merge commit, porque el squash de un release perdería el vínculo con `develop` y haría que cada release reintegrado genere conflictos. Ninguna de las dos exige historial lineal.
- El check `Nombre de rama` (`pr-rama.yml`) valida el nombre **y la pareja origen→destino** (p. ej. una `feat/…` hacia `main` se rechaza). Bloquea el merge, no la creación de la rama (GitHub no aplica reglas de nombre en este plan). Las de Dependabot (`dependabot/**`) están exentas y apuntan a `develop`.
- El CI se dispara en cada PR y en cada push a `main` y `develop`.
- Staging despliega **solo desde `main`**, es decir, con cada release o hotfix (`deploy-staging.yml`, tras el CI del merge); `develop` integra y no despliega. Producción no existe todavía.
- Un tag por sprint al cerrarlo, sobre `main`: `sprint-1`, `sprint-2`, `sprint-3` (el mismo nombre en `solventa-frontend`). Los `sprint-*` están protegidos: no se pueden mover ni borrar (`.github/rulesets/tags.json`).
- Cada merge a `develop` debe dejar el repo desplegable a staging.
- **Dependabot:** los PRs patch y minor hacia `develop` se aprueban y se integran solos cuando los checks pasan (`dependabot-auto-merge.yml`); los major los revisa una persona.
- **El repo es público**, que es lo que hace gratuitos los rulesets. Por eso no se versionan secretos, datos personales ni identificadores de la nube; *secret scanning* y *push protection* están activos.
- Los rulesets son código. Si hay que recrearlos: `gh api -X POST repos/solventa-app/solventa-backend/rulesets --input .github/rulesets/main.json` (igual con `develop.json` y `tags.json`). Un cambio a esos archivos solo surte efecto al aplicarlo.

## Commits y PR

- **Conventional Commits** con alcance = servicio o carpeta y la clave de Jira: `feat(auth): KAN-24 registra consentimiento append-only`.
  Tipos: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `infra`. Alcances: `bff`, `auth`, `risk`, `rating`, `payments`, `policy`, `under`, `acl`, `stubs`, `almacenamiento`, `onboarding`, `avisos`, `claims`, `contracts`, `infra`, `tests`.
- El título del PR sigue el mismo formato y **lo valida el CI** (`Título del PR`; Dependabot queda exento). El PR usa la plantilla de `.github/pull_request_template.md`.
- Las ramas de trabajo se integran con **squash** en `develop`: el título del PR es el commit en `develop`. Los PRs de release, hotfix y reintegración usan merge commit; el de release o hotfix a `main` es el que despliega a staging, comparado contra su primer padre (todo lo liberado); su título sigue el mismo formato (p. ej. `chore(infra): KAN-175 release sprint-1`).
- Un PR cuenta una sola historia o habilitadora. Si cruza servicios de backend, sigue siendo un solo PR atómico.
- Un cambio de contrato (`contracts/`) se avisa explícitamente para abrir el PR gemelo en `solventa-frontend`.

## Definición de hecho (propuesta del equipo)

- Código integrado a `develop` por PR con revisión de al menos otro integrante.
- Pruebas unitarias del código nuevo y pruebas de contrato de cada adaptador, en verde en el pipeline.
- Si cambia un esquema: migración versionada en el servicio dueño del almacén, compatible hacia atrás, con `downgrade` (Postgres) o idempotente (MongoDB), y `scripts/verificar-migraciones.sh` en verde (ver ADR-07).
- Quality gate de SonarQube aprobado y Dependabot sin vulnerabilidades críticas abiertas.
- Flujo E2E de la historia en verde en staging (Cypress en el frontend).
- Escenarios de calidad medidos con k6 y evidencia guardada con nombre, fecha, escenario y vínculo a la historia (`docs/evidencias/`).
- Métricas visibles en el panel de observabilidad y **sin datos personales en logs**.
- Documentación técnica y tablero de Jira actualizados; historia demostrada en la revisión del sprint.

## Seguridad en CI/CD (Bloque 6, decisión 7 y 19)

Además de `gitleaks`, `Dependabot` y `actionlint` (obligatorios), `.github/workflows/ci.yml` ejecuta escaneos de seguridad sobre cada cambio:

- **Bandit** (SAST sobre `app/` en cada servicio Python).
- **pip-audit** (SCA sobre `requirements.txt` de cada servicio).
- **Trivy** (escaneo de vulnerabilidades `HIGH`/`CRITICAL` en la imagen Docker construida de cada servicio).
- **OWASP ZAP baseline** (DAST contra el gateway/BFF levantado en el job `humo`).
- **SonarCloud** (job `sonar`, activo al configurar los secretos `SONAR_TOKEN` y `SONAR_HOST_URL=https://sonarcloud.io`).

**Política de transición:** Bandit, pip-audit, Trivy y OWASP ZAP corren inicialmente en modo **informativo** (`continue-on-error: true`, fuera del gate `CI OK`) y publican sus reportes como artefactos del workflow (resumidos en `docs/evidencias/seguridad-2026-10-18.md`). Una vez saneados los hallazgos base de imágenes o dependencias transitivas, se les retira `continue-on-error` y pasan a ser obligatorios dentro de `CI OK`.

## Migraciones de base de datos

- Cada servicio migra **solo su almacén** (escritor único): `auth`, `payments` (Alembic) y `risk` (runner de MongoDB). RATING no migra.
- SQL explícito, revisiones secuenciales (`0001`, `0002`…) y **una sola cabeza**. Nunca edites una migración ya integrada a `develop`: agrega otra.
- **Expandir y contraer:** la migración corre *antes* de que el código nuevo reciba tráfico, con el código anterior aún atendiendo.
  Agregar es seguro; renombrar o borrar se hace en dos despliegues (primero el código deja de usarlo, luego se elimina).
- Sin datos personales ni secretos en migraciones ni en sus logs. Fixtures sin datos reales.

## Despliegue

- `develop` → staging automático **solo si CI pasó** y solo de lo que cambió; una imagen por commit (etiqueta = SHA). Ver `deploy-staging.yml`.
- Un despliegue fallido antes de "promover" no afecta a staging. Para volver atrás: *Rollback staging* (no revierte la base; se corrige hacia adelante).
- Terraform **sí** se aplica desde el pipeline (ADR-09), como primera etapa, al mergear cambios en `infra/` o `.github/servicios.json`; el PR es la aprobación y `infra/staging.tfvars` decide qué existe (activar un recurso de costo es un cambio revisado). Los servicios se declaran en `.github/servicios.json` y `python scripts/validar_servicios.py` debe pasar.

## Código

- Python 3.14, `ruff` (config en `pyproject.toml`), pruebas con `python -m pytest` desde la carpeta del servicio.
- Dependencias fijadas **con hash** (reproducibles y a prueba de un paquete cambiado aguas arriba): cada servicio tiene un
  `requirements.in` (solo las directas, es lo que se edita) y un `requirements.txt` generado con **todas** las
  dependencias, transitivas incluidas, y el hash de cada archivo. No se edita a mano: se cambia el `.in` y se corre
  `scripts/compilar-requirements.sh [servicio]` (compila en Linux/Python 3.14, la misma base que las imágenes).
  Dependabot actualiza el `.in` y recompila el `.txt` solo. Se instala con `pip install -r requirements.txt` y las
  herramientas de prueba aparte (`pip install -r ../../requirements-dev.txt`), nunca los dos archivos en un solo `pip`.
- Imágenes base por **digest** (`FROM python:3.14-slim@sha256:...`) y GitHub Actions por **SHA** de commit con el tag como
  comentario: Dependabot (docker y github-actions) mantiene ambos al día.
- Un servicio no importa código de otro: se comunican por HTTP o eventos, y comparten **contratos**, no librerías.
- Configuración por variables de entorno. Nunca secretos en el repo (`.env` ignorado; en nube, Secret Manager).
- Logs estructurados y **sin datos personales**: nada de documento de identidad, correo, nombre ni `cliente_id` en claro en logs o etiquetas de métricas.

## Pruebas (pirámide del equipo: 70 % unitarias e integración, 20 % E2E, 10 % exploratorias)

- Unitarias: dominio y reglas (RATING con `pytest`, sin red).
- Contrato: la **misma suite** corre contra el stub y contra el sandbox real del proveedor (EC031).
- Servicios **dummy** (`"dummy": true` en `.github/servicios.json`, p. ej. `stubs/siniestros` para HU-W04): sustitutos
  temporales de un servicio que aún no existe, para que el front avance. Son autónomos (sin base, sin tokens, sin
  llamar a nadie) y publican su contrato en `contracts/graphql/<nombre>.graphql`. En staging se despliegan **privados**
  y solo los invoca el API Gateway, con rutas propias en `infra/gateway/openapi.yaml` y `envoy.yaml`. Se reemplazan
  por el servicio real sin que el front cambie más que la URL.
  **`stubs/siniestros` está obsoleto desde 2026-10-28:** el servicio real `claims` lo reemplaza (HU-W04,
  `docs/plan-completar-hu-w04.md`). Se conserva sin cambios hasta que la web se conecte al BFF y el equipo decida
  retirarlo (W04-13); sus datos alimentan la semilla de `claims`.
- Carga y escenarios: k6 en `tests/k6/`. Los guiones de los experimentos pasan a ser pruebas de regresión en cada sprint.
- Fixtures sin datos personales reales.

## Documentos

- Decisiones de arquitectura: `docs/adr/ADR-NN-titulo.md` (la numeración sigue la de los ADR-01 a ADR-05 del documento de arquitectura).
- Evidencia de escenarios: `docs/evidencias/<EC>-<fecha>.md`.
- Idioma: español.
