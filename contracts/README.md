# contracts/ — contratos del backend

Aquí vive todo lo que otro componente (o el repo `solventa-frontend`) necesita conocer de este backend.
**Se comparten contratos, nunca código** entre servicios.

| Ruta | Qué es | Quién lo consume |
|---|---|---|
| `graphql/schema.graphql` | Contrato del BFF (web y móvil) | `solventa-frontend` (codegen) y `services/bff` |
| `events/*.schema.json` | Eventos del bus (Pub/Sub), JSON Schema 2020-12 | Servicios productores y consumidores |
| `CHANGELOG.md` | Bitácora de cambios de contrato | Todos |

## Reglas

1. **Solo cambios aditivos.** Se agregan campos opcionales, tipos u operaciones. No se renombra ni se cambia el tipo de nada existente.
2. **Retirar algo = deprecar primero** (`@deprecated(reason: "...")`) y borrarlo al menos un sprint después.
3. **Todo cambio** se anota en `CHANGELOG.md` con fecha, la historia de Jira (KAN-nn) y si afecta al frontend.
4. Los eventos llevan un sobre fijo (`eventId`, `tipo`, `version`, `ocurridoEn`, `datos`). Un cambio incompatible crea una `version` nueva, no edita la anterior.
5. El frontend toma una copia versionada del esquema y su CI falla si difiere del `main` de este repo (lo liberado; ver ADR-08) (ver `docs/adr/ADR-06-estrategia-de-repositorios.md`).
6. El dinero se expresa como texto decimal más la moneda. Nunca `float`.

## Validación

```bash
pip install -r ../requirements-dev.txt
python ../scripts/validar_contratos.py
```

El CI corre el mismo script cuando cambia algo en `contracts/`.
