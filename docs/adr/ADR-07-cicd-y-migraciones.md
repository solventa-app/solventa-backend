# ADR-07 — CI/CD y migraciones de base de datos

- **Estado:** propuesto; el flujo local y de CI está probado en vivo, el de despliegue a GCP **no** (no existe aún el proyecto GCP)
- **Fecha:** 2026-10-04
- **Nota (2026-10-08):** donde dice `main` como rama que despliega a staging, ahora es `develop` ([ADR-08](ADR-08-git-flow.md)).
- **Continúa:** ADR-06 (trunk-based, pipelines filtrados por ruta, "cada merge a `main` deja el repo desplegable a staging")

## Contexto

Hay 9 piezas desplegables, tres almacenes con esquema propio (PostgreSQL `auth` y `payments`, MongoDB `risk`) y un
equipo de 4 con holgura casi nula. Sin un camino único de la base de datos al despliegue pasan cosas conocidas:
"en mi máquina sí corre", esquemas distintos entre local y staging, una migración que tumba la versión que aún
atiende, o una imagen en staging que no es la que pasó las pruebas.

## Decisión

Un solo camino, con los mismos pasos y comandos en local, CI y staging:

```
 PR ──► CI (lint, pruebas, migraciones, humo, secretos) ──► merge a main ──► construir ──► migrar ──► desplegar sin tráfico ──► humo ──► promover
          docker compose = CI                                     imagen = SHA     Cloud Run Job     revisión nueva          /health    100 % del tráfico
```

### Principios

1. **Construir una vez, promover lo mismo.** La imagen se etiqueta con el SHA del commit (nunca `latest`) y es la que pasó
   CI; si ya existe para ese SHA no se reconstruye. La misma imagen corre el servicio **y** su job de migración.
2. **Migrar y arrancar, en ese orden, en todas partes.** En `docker-compose` (`migrar-<servicio>` → `<servicio>`), en el CI
   (`scripts/verificar-migraciones.sh`) y en staging (Cloud Run Job `migrar-<servicio>` → revisión nueva). Las migraciones
   **no** corren al arrancar el contenedor: con varias instancias y escala a cero habría carreras, arranques lentos y la
   aplicación necesitaría permisos de DDL.
3. **Escritor único también para el esquema.** Cada servicio migra solo su almacén: `auth` → PostgreSQL `auth`,
   `payments` → PostgreSQL `payments`, `risk` → MongoDB. `rating` y el resto **no** migran (RATING solo lee).
4. **Las migraciones son código versionado, con SQL explícito.** PostgreSQL con Alembic (revisiones secuenciales
   `0001`, `0002`…, sin ORM ni autogenerate); MongoDB con un runner mínimo propio (`services/risk/migrations`). Una sola
   cabeza: si dos ramas crean la misma revisión, el CI falla y se renumera al rebasear.
5. **Expandir y contraer.** La migración se aplica **antes** de que la revisión nueva reciba tráfico, mientras la anterior
   sigue atendiendo. Por eso toda migración debe ser compatible con el código anterior: agregar columnas anulables o con
   valor por defecto, índices y colecciones nuevas; **nunca** renombrar o borrar en el mismo paso en que el código deja de
   usarlo (se hace en un despliegue posterior). Las de MongoDB deben además ser **idempotentes**.
6. **Reversibles en CI, hacia adelante en staging.** Toda migración de Alembic trae `downgrade`, y el CI prueba
   `upgrade → downgrade base → upgrade` contra Postgres real. En staging **no** se baja el esquema: se corrige hacia
   adelante con otra migración (bajar puede perder datos).
7. **Despliegue progresivo con salida rápida.** La revisión nueva nace sin tráfico, con etiqueta, y se prueba (`/health` con
   token de identidad) antes de promoverla. Si algo falla antes de promover, staging sigue sirviendo la revisión anterior.
   El rollback es redirigir el tráfico a la revisión anterior (`rollback-staging.yml`), en segundos y sin tocar la base.
8. **Despliegue selectivo y serializado.** Solo se despliega lo que cambió (`scripts/seleccionar_servicios.py`), un
   despliegue a la vez (`concurrency`, sin cancelar a medias). Como los contratos son solo aditivos, el orden entre
   servicios no importa.
9. **El pipeline despliega aplicaciones; Terraform define la infraestructura, y no se aplica solo.** Terraform decide
   *qué es* cada servicio (cuenta de servicio, variables, secretos, permisos entre servicios); el pipeline solo cambia la
   imagen y el tráfico. `terraform apply` es siempre manual y con confirmación (costos).
10. **Sin llaves ni secretos en el repo.** GitHub Actions entra a GCP por Workload Identity Federation, limitada a este
    repositorio y al entorno `staging`. Los valores de los secretos viven en Secret Manager y se cargan a mano; Terraform
    solo crea el contenedor (y una versión marcadora), así que el valor real nunca pasa por su estado.
11. **Una sola fuente de servicios.** `.github/servicios.json` alimenta el CI, el deploy y Terraform;
    `scripts/validar_servicios.py` falla si `ci.yml`, `dependabot.yml` o `docker-compose.yml` se desincronizan.
12. **Puertas de calidad.** El único check requerido de `main` es **CI OK** (resume los jobs dinámicos) más **Título del
    PR**. El CI incluye migraciones contra almacén real, humo del stack completo (`up --wait`), escaneo de secretos
    (gitleaks sobre todo el historial) y actionlint (los workflows no se pueden probar hasta correr en GitHub).
13. **Apagado hasta estar listo.** El deploy no hace nada hasta que existan `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`,
    `GCP_DEPLOY_SA` y `GCP_PROTECCION_GASTO = activa`: la última obliga a confirmar a mano que la protección de gasto
    (`../solventa-arquitectura/proteccion-costos/`) está instalada antes de desplegar.

## Alternativas descartadas

- **Migrar al arrancar la aplicación:** carreras entre instancias, el usuario paga el arranque y la app necesita permisos de
  DDL. Contradice la lección del Experimento 1 de sacar del camino síncrono lo que no es del usuario.
- **Terraform gestionando también la imagen de cada revisión:** cada despliegue sería un `apply` (permisos amplios y costo
  de riesgo en el pipeline) y el estado de Terraform se movería con cada commit.
- **SQL plano con runner propio también para PostgreSQL:** menos dependencias, pero se reimplementa el grafo de
  revisiones, la detección de cabezas múltiples y el `downgrade`. Alembic es el estándar y se usa sin ORM.
- **Un job de CI por servicio exigido por nombre en la protección de rama:** la matriz es dinámica y los jobs se omiten
  según lo que cambia; `CI OK` evita mantener esa lista.
- **Entorno de producción:** no existe en el alcance del curso; solo `staging`. Si aparece, es otro entorno de GitHub con
  su propia identidad en GCP y aprobación manual.

## Consecuencias

- (+) Lo que se prueba en local y en CI es lo que corre en staging: mismas imágenes, mismos comandos, mismo orden.
- (+) Un despliegue fallido no deja a staging en un estado a medias visible para el usuario; el rollback es inmediato.
- (+) Agregar un servicio es una entrada en `servicios.json` (más su carpeta, filtro de CI y Dependabot, que el validador exige).
- (−) La disciplina de "expandir y contraer" cuesta un despliegue extra al renombrar o borrar columnas.
- (−) Las imágenes de `auth`, `payments` y `risk` llevan Alembic/SQLAlchemy/psycopg (o el runner) aunque el servicio web no los use.
- (−) Los minutos de Actions son limitados: por eso el CI es selectivo y cada job tiene `timeout-minutes`.

## Límites declarados (qué NO está probado)

- **Probado en vivo (local, Docker 29 / Compose 2.40):** migraciones de los tres almacenes con la imagen del servicio,
  `upgrade → downgrade → upgrade` en Postgres, idempotencia en MongoDB, el stack completo con `up --build --wait`,
  actionlint, gitleaks, el validador de servicios, el selector de servicios y la lógica `jq` de las matrices del CI y de
  la selección de revisión del rollback.
- **Validado solo estáticamente:** `terraform fmt`/`validate` y la evaluación offline de los `for_each` (`terraform console`).
  No se hizo `plan` ni `apply`: no hay proyecto GCP.
- **No probado hasta que corra en GitHub/GCP:** `ci.yml` en un runner real, `deploy-staging.yml`, `rollback-staging.yml`,
  la identidad de Workload Identity, el token de identidad con *custom audience* contra una URL con etiqueta, y que
  `google_cloud_run_v2_service` ignore sin deriva lo que modifica `gcloud`. Esperar ajustes en la primera ejecución real.

## Pendiente

- Dónde vive PostgreSQL en staging (Cloud SQL cobra aunque esté detenido el uso; ver `infra/README.md`). Hoy el pipeline
  solo exige que exista el secreto `database-url-*`; no importa quién provea la base.
- Usuario de solo lectura de MongoDB para RATING (`mongo-uri-rating`): refuerza "RATING nunca escribe en Riesgo".
- Cobertura de pruebas en el CI (Sonar la espera comentada) y escaneo de imágenes (Trivy), si el tiempo lo permite.
