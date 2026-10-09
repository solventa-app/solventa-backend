# ADR-08 — Git-flow: main → develop → feature

- **Estado:** propuesto; los rulesets y workflows están en el repo pero **no se han aplicado en GitHub** (ver "Para activarlo")
- **Fecha:** 2026-10-08
- **Reemplaza:** la regla "trunk-based" de ADR-06 (punto 7), la de ADR-07 ("cada merge a `main` deja el repo desplegable a staging") y la rama física `staging` que introdujo el PR #71 (KAN-175). El resto de ambos ADR sigue vigente.

## Contexto

ADR-06 eligió trunk-based: ramas cortas desde `main`, cada merge a `main` desplegaba a staging. El equipo pidió un esquema
git-flow completo con `main → develop → feature`, y el PR #71 (KAN-175, integrado a `main` el 2026-10-08) acababa de
añadir una rama física `staging` con su ruleset, que despliega desde ella. Se necesita separar "lo integrado" de "lo liberado" para poder
estabilizar el cierre de cada sprint mientras el siguiente ya avanza.

## Decisión

1. **`main`** guarda lo liberado; **`develop`** integra y **despliega a staging**; el trabajo vive en ramas
   `feat|fix|chore/KAN-N-descripcion` que salen de y vuelven a `develop`.
2. Cierre de sprint con **`release/sprint-N`** (solo correcciones) → `main` con tag `sprint-N` → reintegración `main` → `develop`.
   Correcciones urgentes con **`hotfix/KAN-N-descripcion`** desde `main`.
3. **Squash** para ramas de trabajo hacia `develop` (1 PR = 1 historia = 1 commit); **merge commit** para `release`, `hotfix`
   y reintegración, porque squashear ramas de larga vida rompe el vínculo con `develop` y provoca conflictos repetidos.
4. El check `Nombre de rama` valida también la pareja origen→destino. `main` y `develop` tienen ruleset propio.
5. **No hay rama `staging`.** Staging es un entorno, no una rama; se despliega desde `develop`. Esto revierte lo integrado en
   el PR #71: se elimina `.github/rulesets/staging.json` y CI y deploy vuelven a disparar desde `develop`.

## Consecuencias

- Staging siempre refleja `develop`, que puede contener trabajo del sprint siguiente mientras `release/sprint-N` se estabiliza.
  Las correcciones del release no pasan por staging hasta reintegrarlas a `develop`; el workflow de despliegue exige
  `develop`, así que probar el release en staging antes de reintegrar no es posible. A decidir al primer release.
- Más PRs por sprint (release y reintegración) y más ramas largas que mantener sincronizadas. A cambio, `main` queda siempre
  etiquetable y desplegable.
- El frontend toma el contrato (`contracts/`) de **`main`** (ADR-06 punto 2): el contrato visible para el frontend cambia
  solo al liberar. Si el frontend necesita un contrato aún no liberado, debe leerlo de `develop` — decisión a confirmar con ese equipo.
- Cypress contra staging prueba `develop`, no lo liberado.
- Dependabot apunta a `develop` (`target-branch`), de modo que sus PRs no llegan a `main` fuera de un release.

## Para activarlo (no hecho)

1. Aplicar `.github/rulesets/main.json` (actualizar el existente) y `develop.json` (nuevo) con `gh api`; la rama `develop`
   ya existe en el remoto.
2. Dejar `develop` como rama predeterminada del repo (los PRs nuevos la proponen como destino) — Settings → Branches.
3. Entorno `staging` de GitHub: limitar a la rama `develop`.
4. Si el ruleset `staging protegida` ya se aplicó en GitHub, borrarlo (Settings → Rules) y luego borrar la rama remota `staging`,
   que queda sin uso. Avisar antes a quien integró el PR #71.

## Alternativas descartadas

- **`main → staging → develop → feature`** (conservar lo del PR #71): cuatro niveles y un PR de promoción adicional por cambio con 4 personas y
  holgura casi nula.
- **Mantener trunk-based:** contradice lo pedido por el equipo.
- **Squash en todos los PRs:** simple, pero reintegrar `main → develop` tras un release squasheado deja conflictos artificiales.
