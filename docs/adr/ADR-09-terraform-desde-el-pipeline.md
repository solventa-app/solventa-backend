# ADR-09 — Terraform se aplica desde el pipeline

- **Estado:** propuesto. El workflow y el guardia están probados con planes reales y `actionlint`; el arranque manual de abajo se hizo el 2026-10-17 (114 recursos, `plan` final sin cambios), pero el `apply` desde GitHub **no** se ha ejecutado nunca.
- **Fecha:** 2026-10-16
- **Reemplaza:** la decisión 9 de [ADR-07](ADR-07-cicd-y-migraciones.md) ("`terraform apply` es siempre manual"). El resto de ADR-07 sigue vigente.

## Contexto

Con dos caminos (un merge despliega las aplicaciones; la infraestructura se aplica a mano) pasa lo conocido: un servicio nuevo
se despliega antes de que Terraform lo cree, o el código llega a staging y la infraestructura que necesita, no. El equipo
decidió que **un merge a `main` (un release o un hotfix) debe desplegarlo todo**, aplicaciones e infraestructura, y solo lo que falte. `develop` integra y no despliega ni aplica nada.

## Decisión

1. **Terraform es la primera etapa de `deploy-staging.yml`:** `infra → construir → migrar → desplegar`. Corre cuando el merge
   cambió `infra/` o `.github/servicios.json` (o a mano con la entrada `infra`). Va en el mismo workflow, no en otro, para que
   el orden sea estricto: un servicio existe antes de desplegarlo y no hay carrera entre Terraform y `gcloud run deploy`.
   Si `infra` falla, no se construye ni se despliega nada.
2. **Plan → guardia → apply del plan guardado.** `scripts/revisar_plan.py` resume el plan en el resumen del job y **bloquea**
   si destruye o reemplaza algo. Solo se permite a mano (`workflow_dispatch` con `permitir_destruir`) después de revisarlo. Un
   apply automático no debe borrar infraestructura por accidente.
3. **Identidad propia y más potente que la de despliegue:** la cuenta `gha-terraform-backend`, que solo asume el entorno de
   GitHub `infra-staging` de este repositorio (Workload Identity Federation, sin llaves). El entorno es donde se pueden
   exigir revisores antes de cada apply, si el equipo lo quiere.
4. **Permisos: propietario del proyecto de staging** (decisión del equipo). No lo es de la facturación ni de la organización.
   Además `billing.viewer` en la cuenta de facturación solo para que `plan` pueda leer el presupuesto; **cambiar el presupuesto
   sigue siendo manual**, de quien administra la facturación (esa cuenta es compartida con otros proyectos).
5. **La configuración es código:** `infra/staging.tfvars` versiona las banderas. Activar un recurso con costo por hora
   (Cloud SQL, Redis, Atlas) es mergear un cambio en ese archivo, revisado en un PR como cualquier gasto. `project_id` y
   `billing_account_id` llegan por variables de GitHub (`TF_VAR_*`), no por el repositorio.
6. **Sigue apagado por defecto:** el workflow no hace nada hasta que existan `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`,
   `GCP_TERRAFORM_SA`, `GCP_BILLING_ACCOUNT_ID` y `GCP_PROTECCION_GASTO=activa` (la protección de gasto debe estar instalada y
   funcionando antes de que algo se despliegue solo).

## Arranque (una sola vez, manual)

La cuenta de Terraform y la federación se crean con Terraform, así que el primer `apply` que las crea (`crear_pipeline`) lo
hace una persona desde su máquina, con el plan revisado. Después carga las variables de GitHub (`terraform output`), crea el
entorno `infra-staging` y, desde ahí, los cambios en `infra/` se aplican con el merge.

## Consecuencias y riesgos

- **Una cuenta de CI con permisos de propietario.** Un workflow comprometido, o un PR malicioso aprobado y mergeado, controla
  el proyecto. Mitigaciones: la federación solo acepta este repositorio y el entorno `infra-staging`; `main` (de donde se aplica) exige PR, una
  revisión y checks, sin excepciones ni para administradores; el guardia contra destrucciones; es un proyecto de staging con
  protección de gasto; y como Terraform ya administra el IAM del proyecto, un rol más acotado no cambiaría el riesgo de fondo
  (quien puede asignar roles puede asignarse propietario). **A revisar** si el proyecto pasa a manejar datos reales: exigir
  revisores en `infra-staging` o separar el IAM del resto.
- **Un merge puede costar dinero.** Se acota con la revisión del PR sobre `staging.tfvars` y la protección de gasto.
- **El estado vive en GCS y contiene los secretos que Terraform genera** (contraseñas de Cloud SQL y Atlas): el acceso al
  bucket equivale a acceso a ellos.
- **Un solo `apply` a la vez:** el workflow comparte el grupo de concurrencia `deploy-staging` (sin cancelar) y el estado de GCS
  se bloquea durante el apply.
- **Deshacer:** revertir el PR que cambió `infra/`. Si eso destruye algo, el guardia exige `permitir_destruir`.
- **Heredado de ADR-07:** la detección de cambios compara el commit con su padre (`SHA^..SHA`). En `main` los releases y hotfixes
  entran por merge commit, cuyo primer padre es `main` antes del merge, así que el diff es todo lo liberado; si algo entra de otra
  forma, hay que lanzar el workflow a mano desde `main`.
- **Los entornos no restringen la rama por sí solos:** en `workflow_run` GitHub toma como ref la rama por defecto (`develop`), así
  que `staging` e `infra-staging` aceptan `main` y `develop`. Que solo `main` despliegue lo garantizan el filtro `branches: [main]`
  del workflow y la condición de la ejecución manual. Lo que sí impiden los entornos es que una rama de trabajo o un PR asuma la
  identidad de Terraform.

## Alternativas descartadas

- **Solo `plan` en el pipeline y `apply` manual:** lo más seguro, pero no cumple "el merge despliega todo" y deja el desfase.
- **Un workflow aparte para Terraform:** los dos se disparan a la vez con el mismo CI y no se puede garantizar que la
  infraestructura exista antes de desplegar; la concurrencia de GitHub conserva un solo pendiente y descartaría ejecuciones.
- **Roles finos en lugar de propietario:** se rompen al agregar un tipo de recurso nuevo, y con el rol de administrador de IAM
  el riesgo real es casi el mismo.
