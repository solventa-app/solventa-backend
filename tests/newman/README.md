# tests/newman/

Pruebas de integración del **BFF GraphQL** (HU-W01, KAN-24) con [Newman](https://github.com/postmanlabs/newman),
el ejecutor de línea de comandos de Postman. Sirven para verificar el contrato visible por el frontend contra el
stack real de `docker compose`, sin escribir código.

| Archivo | Contenido |
|---|---|
| `solventa-bff.postman_collection.json` | Colección: 18 peticiones y 49 aserciones, en 6 carpetas |
| `solventa-local.postman_environment.json` | Entorno local: `bff_url`, `acl_url`, `stub_open_finance_url` |

## Qué cubre

| Carpeta | Qué verifica | Criterio / escenario |
|---|---|---|
| 0. Salud | `/health` y `/metrics` del BFF | — |
| 1. Consentimiento | `registrarConsentimiento` y guarda el id para el resto | CA-W01-02 |
| 2. Oferta con proveedores sanos | Oferta COMPLETA en los canales web y móvil, 5 fuentes, prima puntual, dinero como texto; `oferta(id)` devuelve `null` | CA-W01-03, CA-W01-06 |
| 3. Degradación | Con Open Finance lento: oferta PRELIMINAR, rango de prima, fuentes en CACHE, sin error; luego restaura el stub y espera a que cierren los circuitos | CA-W01-04, EC009 / EC010 |
| 4. Errores controlados | Consentimiento inexistente, valor inválido, cobros no disponibles y consulta inválida: siempre HTTP 200 o 4xx, nunca 5xx | CA-W01-01, CA-W01-05 |
| 5. Contacto y revocación | `contactarAsesor`, `revocarConsentimiento`, y que la oferta se rechace después de revocar | CA-W01-07, CA-W01-08 |

## Cómo correrlas

Requisitos: Docker y Node.js 18 o superior. Newman no se instala en el repo.

```bash
# 1. Levantar el stack (si 5432 o 6379 están ocupados, antepón POSTGRES_PORT=5433 REDIS_PORT=6380)
docker compose --profile servicios up -d --build --wait

# 2. Correr la colección (versión fijada: 6.2.2)
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json

# 3. Apagar
docker compose --profile servicios down -v
```

Para dejar un reporte (no se versiona; crea la carpeta `resultados/` y no la subas al repo):

```bash
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json \
  -r cli,json --reporter-json-export tests/newman/resultados/bff.json
```

## Notas de uso

- **El orden importa.** Las carpetas comparten variables de colección (`consentimiento_id`, `oferta_id`) y la
  carpeta 3 necesita que la 2 haya dejado datos en caché. Corre la colección completa, no peticiones sueltas.
- **Es repetible.** La carpeta 3 restaura el stub a `sano` y espera 10 s a que los circuitos del ACL Worker
  cierren, así que se puede correr varias veces seguidas.
- **Solo contra local.** La carpeta 3 manipula el stub de Open Finance; en staging no existe ese control. Para
  staging, corre únicamente las carpetas 0, 1, 2 (sin la 3), 4 y 5 cambiando `bff_url` en el entorno.
- **Datos ficticios.** El documento `CC-NEWMAN-0001` no es una persona real. No pongas datos reales en la colección.
- **Los umbrales de tiempo son un piso**, medidos en un portátil con proveedores simulados (regla 9 de
  `CLAUDE.md`). No reemplazan las pruebas de carga con k6 de EC003 / EC004 (`tests/k6/`).
