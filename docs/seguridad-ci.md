# Pipeline de seguridad (`seguridad.yml`)

Reporte consolidado de cómo está el backend en seguridad. Usa el mismo estilo que el `seguridad.yml` del móvil, y la web
tiene el suyo. Es **informativo**: no bloquea merges. Los checks que sí bloquean siguen en `ci.yml` (secretos y
actionlint).

## Cuándo corre

| Evento | Qué corre |
|---|---|
| Cada PR | secretos, dependencias, SAST e IaC |
| Push a `develop`, cada lunes 7:00 (Bogotá) y a mano | todo lo anterior + imágenes de contenedor + DAST contra staging |

Para correrlo a mano: **Actions → Seguridad → Run workflow** (elige la rama).

## Qué revisa

| Escaneo | Herramienta | Sobre qué |
|---|---|---|
| Secretos | gitleaks | todo el historial de git |
| Dependencias (SCA) | osv-scanner | los `requirements.txt` de servicios y stubs, `requirements-dev.txt` y `uv.lock` |
| Código (SAST) | Bandit y Semgrep (`p/python`) | `services/` y `stubs/`, sin las pruebas |
| Infraestructura y Dockerfiles | Trivy config | `infra/*.tf` y los Dockerfile |
| Imágenes de contenedor | Trivy | una imagen por servicio de `.github/servicios.json`; las CVE de la imagen base se agrupan |
| Aplicación desplegada (DAST) | OWASP ZAP baseline (pasivo) | el gateway de staging (`/graphql/web` y `/v1/graphql/app`); si staging está apagado, se omite |

El gateway de staging se toma de la variable del repo `STAGING_GATEWAY_URL` o, si no existe, del valor fijo en el
workflow.

## El reporte

El job **Reporte consolidado** (`scripts/reporte_seguridad.py`) junta los resultados y deja:

- en el **resumen del run**: calificación, semáforo 🟢🟡🔴, tabla por herramienta (críticos, altos, medios, bajos) y los hallazgos
  principales de cada una;
- el artefacto **`reporte-seguridad`**, con `reporte-seguridad.pdf`, `.md` y `.json` (todos los hallazgos
  normalizados, para depurarlos).

**Calificación (como la de MobSF en el móvil):** un puntaje de 0 a 100 y una letra, en total y por escaneo. Parte de
100 y resta por severidad, con un tope para que una sola herramienta ruidosa no lo deje en cero:

| Severidad | Resta por hallazgo | Tope |
|---|---:|---:|
| Crítica | 15 | 40 |
| Alta | 4 | 30 |
| Media | 0,5 | 20 |
| Baja | 0,1 | 10 |

Letra: A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40 y F por debajo. Es orientativa: sirve para ver la tendencia entre runs, no
reemplaza revisar los hallazgos.

El semáforo queda en 🔴 si hay algún hallazgo crítico o alto, en 🟡 si solo hay medios y en 🟢 en otro caso. Los
resultados crudos de cada herramienta quedan en los artefactos `seguridad-*`.

Para generarlo en local con resultados ya descargados: `python3 scripts/reporte_seguridad.py consolidar <carpeta>`.

## Depurar hallazgos

- **Falso positivo de Trivy config:** se agrega a `.trivyignore` con su motivo, igual que `tool/mobsf-suprimir.txt` en
  el móvil.
- **Falso positivo de Bandit:** `# nosec BXXX` en la línea, con el motivo en el mismo comentario.
- **Dependencia vulnerable:** se sube la versión en el `requirements.in` del servicio y se recompila
  (`scripts/compilar-requirements.sh`); Dependabot ya abre muchos de estos PR.

Cuando los hallazgos reales estén resueltos, un escaneo puede volverse bloqueante: basta con hacer fallar su job cuando
haya hallazgos y exigirlo en el ruleset.
