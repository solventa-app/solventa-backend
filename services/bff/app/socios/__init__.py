"""API REST `/v1` de los socios de distribución (HU-W07, KAN-30).

Contrato: `contracts/openapi/socios-v1.json` (el BFF sirve su copia `app/socios-v1.json`). El
socio nunca ve GraphQL: estas rutas reutilizan los servicios de W01 y W03 con el actor `SOCIO`, que
hace el
papel del asesor (W07-20).

Plan: `docs/plan-completar-hu-w07.md`."""
