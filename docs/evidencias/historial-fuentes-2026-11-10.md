# Evidencia: historial de fuentes consultadas del asesor (Q-08, KAN-24), 2026-11-10

**Corrida local, no de staging.** Compose con `migrar-risk`, `risk` y `bff` reconstruidos desde la rama
`feat/KAN-24-historial-fuentes-consultadas`; el resto del stack sin cambios. Gateway Envoy en `:8080`.

| Qué | Resultado |
|---|---|
| Migración `0006_consultas_fuentes` | Aplicada por `migrar-risk`; índice `{actor_id: 1, creada_en: -1}` en `risk.consultas` |
| Newman `historial-fuentes-w01` | 20 peticiones, 45 aserciones, 0 fallas (2,3 s; respuesta media 33 ms) |
| pytest RISK | 101 pruebas en verde (9 nuevas) |
| pytest BFF | 625 pruebas en verde (12 nuevas), incluida la paridad del esquema con `contracts/graphql/schema.graphql` |

Lo que cubre la colección:

- El asesor A cotiza dos titulares y ve **2 consultas**, la más reciente primero, con el documento enmascarado, el tipo de
  oferta, `VIDA_DEUDOR` y las 5 fuentes con su estado; el cuerpo de la respuesta no contiene ningún documento completo.
- Detalle propio por `consultaFuentes(id)`; paginación de 1 en 1 (la página 2 trae la primera cotización).
- El asesor B ve el historial **vacío** y el detalle de una consulta de A responde **`null`** (igual que una inexistente).
- El CLIENTE (lista y detalle), el BACKOFFICE y quien no tiene sesión reciben error, nunca un 5xx.

Un documento real de `risk.consultas` tras la corrida: `actor_id` (id opaco de AUTH), `titular_id` (HMAC), `documento_enmascarado`
(`83••••0935`), `consentimiento_id`, `oferta_id`, `tipo_oferta`, `producto`, `creada_en` y `fuentes` con `id`, `nombre`, `origen`,
`estado` y `capturado_en`. **Sin datos de las fuentes y sin el documento ni el nombre.**

**Sin probar:** staging (Atlas M0, el índice y la latencia de EC003 con la llamada extra a RISK, que va en el camino de la
cotización pero con su falla controlada), y la web (W01-04 como historial).
