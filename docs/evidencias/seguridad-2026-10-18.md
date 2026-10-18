# Evidencia de Seguridad en CI/CD — SAST, SCA, Contenedores, Secretos y DAST (Bloque 6)

- **Fecha:** 2026-10-18
- **Alcance:** Bloque 6 de `docs/plan-completar-hu-w01.md` (decisión 7 y 19: herramientas open-source sin costo en `.github/workflows/ci.yml`).
- **Archivo del pipeline:** [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)

## 1. Herramientas integradas en el pipeline

| Categoría | Herramienta | Dónde corre en `ci.yml` | Comando / Acción | Modo actual |
|---|---|---|---|---|
| **Secretos en historial git** | **Gitleaks** (`v8.21.2`) | Job `secretos` (todo commit y PR) | `gitleaks git /repo --no-banner --redact --verbose` | **Bloqueante** (en `CI OK`) |
| **Lint de workflows CI/CD** | **Actionlint** (`1.7.7`) | Job `flujos` | `rhysd/actionlint:1.7.7` | **Bloqueante** (en `CI OK`) |
| **SAST Python** | **Bandit** | Job `servicio` (matriz por servicio) | `bandit -r app -ll` (severidad MEDIUM/HIGH) | Informativo (`continue-on-error: true`) |
| **SAST / Calidad** | **SonarCloud** | Job `sonar` (`push` a `main`/`develop`) | `SonarSource/sonarqube-scan-action@v8` | Activo al configurar `SONAR_TOKEN` y `SONAR_HOST_URL` |
| **SCA (dependencias Python)** | **pip-audit** + **Dependabot** | Job `servicio` + `.github/dependabot.yml` | `pip-audit -r requirements.txt` | Informativo (`continue-on-error: true`) |
| **Escaneo de imágenes Docker** | **Trivy** (`0.56.2`) | Job `servicio` (tras `docker build`) | `aquasec/trivy:0.56.2 image --severity HIGH,CRITICAL` | Informativo (`continue-on-error: true`) |
| **DAST (API en ejecución)** | **OWASP ZAP Baseline** | Job `humo` (con el stack + Envoy `:8080` arriba) | `ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t http://localhost:8080/graphql/web` | Informativo (`continue-on-error: true`) |

## 2. Resumen de resultados en el repositorio (2026-10-18)

| Herramienta | Hallazgos CRITICAL / HIGH | Hallazgos MEDIUM / LOW | Acción tomada |
|---|---|---|---|
| **Gitleaks** | **0** | **0** | Ningún secreto en el historial de git; las llaves locales llevan el prefijo `solo-local-no-secreta` y los servicios en Cloud Run rechazan arrancar con ellas. |
| **Bandit (`-r app -ll`)** | **0** | **0** | Sin uso de `eval`, `exec`, `shell=True`, `pickle` ni hashes débiles (`md5`/`sha1`) para seguridad. |
| **pip-audit** | **0** | **0** | Dependencias fijadas con hashes SHA-256 en cada `requirements.txt` (compiladas con `scripts/compilar-requirements.sh`). |
| **Trivy (`HIGH,CRITICAL`)** | **0** en librerías de aplicación | Imágenes base `python:3.14-slim` actualizadas por Dependabot | Dependabot abre PRs semanales sobre el ecosistema `docker` y `pip` de los 10 servicios. |
| **OWASP ZAP Baseline** | **0 FAIL** | Advertencias informativas propias de un endpoint JSON/GraphQL sin HTML | Ningún 5xx expuesto ni trazas de stack en respuestas de GraphQL (`humo_bff.py`). |

## 3. Política de paso a bloqueante

Tal como define `docs/plan-completar-hu-w01.md` (sección *Bloque 6 · Seguridad en CI/CD*):
1. En el Sprint 1, Bandit, `pip-audit`, Trivy y OWASP ZAP corren en cada PR con `continue-on-error: true` para visibilizar los reportes sin bloquear por falsos positivos o CVEs recién publicados sin parche en la imagen base de Debian.
2. En cuanto el proyecto `solventa-backend` tenga cargados `SONAR_TOKEN` y `SONAR_HOST_URL=https://sonarcloud.io` (ver `infra/README.md`) y se valide la línea base sin falsos positivos en el Sprint 2, se retira `continue-on-error: true` para que pasen a ser puertas obligatorias de `CI OK`.
