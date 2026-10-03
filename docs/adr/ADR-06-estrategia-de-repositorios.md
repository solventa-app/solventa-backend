# ADR-06 — Estrategia de repositorios

- **Estado:** propuesto; organización y nombres de repos confirmados (GitHub `solventa-app`: `solventa-backend`, `solventa-frontend`)
- **Fecha:** 2026-10-03
- **Continúa:** ADR-01 a ADR-05 del Documento de Arquitectura refinado

## Contexto

Proyecto Final 2 dura 7 semanas (sprints de 2, 2 y 3), con 4 integrantes a 12 h/semana: 189 h netas y una holgura de 10 h (5 %).
El sistema tiene al menos 9 piezas desplegables de backend (BFF, AUTH, RISK, RATING, PAYMENTS, ACL Worker, UNDER, POLICY, CLAIMS),
una SPA Vue 3, una app Flutter, Terraform, k6 y observabilidad. Ya existe un repo, `solventa-arquitectura`, con la evidencia
de los dos experimentos de arquitectura.

## Decisión

Tres repositorios, dos activos:

| Repo | Contenido | Estado |
|---|---|---|
| `solventa-backend` | `services/`, `stubs/`, `contracts/`, `infra/`, `tests/k6/`, `observabilidad/`, `docs/adr/` | Activo |
| `solventa-frontend` | `apps/web` (Vue 3 + Vite), `apps/mobile` (Flutter, desde el Sprint 2), `e2e/` (Cypress) | Activo |
| `solventa-arquitectura` | Experimentos 1 y 2 y su análisis | Congelado: evidencia, no se le agrega producto |

Dentro de `solventa-backend` es un monorepo: **un directorio por servicio, pipelines filtrados por ruta**, de modo que
"microservicios" no implica "un repo por servicio".

## Reglas del contrato entre repos

1. `solventa-backend` es **dueño del contrato**: `contracts/graphql/schema.graphql` y `contracts/events/`.
2. `solventa-frontend` toma una **copia versionada** del esquema (codegen) y su CI falla si difiere del `main` de este repo.
3. Cambios **solo aditivos**; retirar = deprecar un sprint antes (ver `contracts/README.md`).
4. El frontend arranca con **mocks** (MSW en web, mock del BFF en Flutter) hasta que el BFF real esté desplegado.
5. Cypress corre contra **staging**, que se despliega desde el `main` de este repo.
6. La infraestructura de hosting de la SPA vive en el Terraform de este repo; el pipeline del frontend publica el build usando los outputs.
7. Mismas reglas de flujo en ambos repos: trunk-based, 1 revisión, clave de Jira en el título, tag `sprint-N`.

## Alternativas descartadas

- **Un solo monorepo para todo:** viable y algo más barato de operar, pero junta dos toolchains sin relación (Vue/Vite y Flutter) en un pipeline con el backend y desdibuja el corte natural de roles del equipo (web y móvil / backend y líder técnico).
- **Un repo por servicio:** con 9 o más servicios multiplica pipelines, Sonar, Dependabot, secretos y protecciones de rama; los cambios que cruzan servicios (p. ej. D-02 entre RISK y RATING) pasan a ser PRs coordinados. No cabe en las 189 h.
- **Un repo por canal (web, móvil, backend):** el móvil arranca en el Sprint 2 y tiene pipeline propio de todas formas; agruparlo con la web no cuesta nada y ahorra un repo.

## Consecuencias

- (+) El backend es continuación directa de los experimentos (Python, Terraform, k6).
- (+) Cambios entre servicios de backend son un único PR atómico.
- (−) Un cambio de contrato requiere dos PRs, uno en cada repo.
- (−) El esquema puede desincronizarse si no se respeta la regla 2; el chequeo del CI del frontend lo detecta.
- (−) Un pipeline, un SonarQube y un Dependabot más: unas pocas horas que salen de la holgura del Sprint 1.
- Revisar la decisión si el CI del móvil estorba al de la web, o si un servicio pasa a tener otro equipo.
