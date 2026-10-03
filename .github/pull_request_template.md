## Qué cambia

<!-- 1–3 líneas. Título del PR: `feat(risk): KAN-24 registra el perfil con operationTime` -->

## Historia / criterios

- Jira: KAN-
- Criterios de aceptación cubiertos: CA-
- Escenarios de calidad afectados: EC

## Lista de verificación (definición de hecho)

- [ ] Pruebas unitarias y de contrato del código nuevo, en verde
- [ ] `ruff check` y `python -m pytest` en verde en los servicios tocados
- [ ] Sin datos personales en logs ni etiquetas de métricas; sin secretos en el diff
- [ ] Si cambia el contrato: `contracts/CHANGELOG.md` actualizado, cambio **aditivo**, y aviso para `solventa-frontend`
- [ ] Si el código cambia el diseño o el alcance: `docs/` actualizado en este mismo PR
- [ ] Si hay escenario de calidad medido: evidencia en `docs/evidencias/`

## Cómo se probó

<!-- Comandos y resultados reales, no "debería funcionar". -->

## Impacto en el frontend

<!-- Ninguno / describe qué debe cambiar solventa-frontend y con qué versión del esquema. -->
