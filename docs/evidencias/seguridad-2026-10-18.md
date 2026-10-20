# Seguridad en CI/CD — herramientas integradas (bloque 6)

- **Fecha:** 2026-10-18
- **Alcance:** bloque 6 de `docs/plan-completar-hu-w01.md` (decisiones 7 y 19): herramientas de código abierto, sin costo, en [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

> **Este informe describe lo que está INTEGRADO, no lo que encontraron los escáneres.** Una versión anterior traía un cuadro de
> «0 hallazgos» para Bandit, pip-audit, Trivy y ZAP que nadie había ejecutado y se retiró. Los resultados reales son los
> **artefactos y los logs de cada corrida del CI**; cuando haya una corrida con resultados, se resumen en la sección 3.

## 1. Herramientas y dónde corren

| Categoría | Herramienta | Dónde (`ci.yml`) | Modo |
|---|---|---|---|
| Secretos en el historial | Gitleaks | Job `secretos` | **Bloqueante** (`CI OK`) |
| Lint de workflows | Actionlint | Job `flujos` | **Bloqueante** |
| SAST de Python | Bandit (`-r app -ll`) | Job `servicio`, por servicio | Informativo (`continue-on-error`) |
| Dependencias | pip-audit y Dependabot | Job `servicio` y `dependabot.yml` | Informativo / PRs automáticos |
| Imágenes Docker | Trivy (HIGH, CRITICAL) | Job `servicio`, tras construir la imagen | Informativo |
| DAST | OWASP ZAP baseline contra el gateway local `:8080` | Job `humo` | Informativo |
| Calidad / SAST | SonarQube Cloud | Job `sonar` | Se activa con `SONAR_TOKEN` y `SONAR_HOST_URL` |

## 2. Política

Al principio los escaneos informativos dejan su reporte en cada PR sin bloquear (por falsos positivos o CVEs sin parche en
la imagen base). Pasan a bloqueantes cuando se revise una línea base sin falsos positivos (Sprint 2).

## 3. Resultados

**Sin corrida revisada todavía.** Para llenarlo: abrir la última ejecución del CI de `develop`, bajar los artefactos de Bandit,
Trivy y ZAP y resumir aquí por herramienta cuántos hallazgos CRITICAL/HIGH hubo y qué se hizo con ellos.
