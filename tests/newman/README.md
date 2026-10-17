# tests/newman/

Pruebas de integración del **BFF GraphQL** (HU-W01 y HU-M02, KAN-24; HU-W05, KAN-28) con [Newman](https://github.com/postmanlabs/newman),
el ejecutor de línea de comandos de Postman. Sirven para verificar el contrato visible por el frontend contra el
stack real de `docker compose`, sin escribir código.

| Archivo | Contenido |
|---|---|
| `solventa-bff.postman_collection.json` | Colección: 57 peticiones y 130 aserciones, en 10 carpetas |
| `solventa-local.postman_environment.json` | Entorno local: `bff_url`, `auth_url`, `acl_url`, `payments_url`, `stub_open_finance_url`, `stub_pasarela_url` y las credenciales **solo locales** del BACKOFFICE (`usuario_backoffice`, `clave_backoffice`) |

## Qué cubre

| Carpeta | Qué verifica | Criterio / escenario |
|---|---|---|
| 0. Salud | `/health` y `/metrics` del BFF | — |
| 1. Autenticación y usuarios | Login del BACKOFFICE, `yo`, alta de ASESOR y de CLIENTE con nombre (alias únicos por corrida; un ASESOR con nombre se rechaza), alias repetido o con forma de correo, clave errónea y login de cada rol; guarda los tokens | HU-M02 |
| 2. Consentimiento | `registrarConsentimiento` como ASESOR, y en AUTH que el actor es ese ASESOR | CA-W01-02, CA-W01-11 |
| 3. Oferta con proveedores sanos | Oferta COMPLETA en los canales web y móvil, 5 fuentes, prima puntual, dinero como texto; `oferta(id)` devuelve `null` | CA-W01-03, CA-W01-06 |
| 4. Degradación | Con Open Finance lento: oferta PRELIMINAR, rango de prima, fuentes en CACHE, sin error; con un documento sin caché, el historial crediticio no llega y sale `ErrorOferta` con reintento; luego restaura el stub y espera a que cierren los circuitos | CA-W01-04, CA-W01-05, EC009 / EC010 |
| 5. Errores controlados | Consentimiento inexistente, valor inválido, plazo inválido y consulta inválida: siempre HTTP 200 o 4xx, nunca 5xx | CA-W01-01, CA-W01-05 |
| 6. Contacto y revocación | `contactarAsesor`, `revocarConsentimiento`, y que la oferta se rechace después de revocar | CA-W01-07, CA-W01-08 |
| 7. Autorización por rol | CLIENTE no lista usuarios ni cobros ni crea cobros, BACKOFFICE no cotiza, ASESOR no crea usuarios, token alterado y sin token | HU-M02 |
| 8. Suspensión | El BACKOFFICE suspende al CLIENTE y su login se rechaza; suspender un id inexistente | HU-M02 |
| 9. Cobros y conciliación | Pasarela lenta: `crearCobro` en el BFF deja el cobro `pendiente` en PAYMENTS aunque la pasarela sí cobró; `cobros(polizaId)` lo muestra; `reintentarCobro` concilia y queda `COBRADO` con **la misma referencia de la pasarela** (sin doble cobro); doble clic sin efecto; los cobros llevan `clienteId` y la tabla muestra el nombre del cliente; reenviar el mismo `idIntentoCobro` devuelve el mismo cobro y con otro monto da `ErrorCobro`; un rechazo con su motivo y que no se puede reintentar; `cobros` sin token exige sesión | CA-W05-01, CA-W05-02 |

**Autenticación:** la colección usa por defecto el token del ASESOR (`Bearer {{token_asesor}}`, en la autenticación de
la colección). Las peticiones que prueban otro rol lo indican en su propia autenticación, y las que no son del BFF
(salud, stub, ACL, AUTH) van sin token.

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

- **El orden importa.** Las carpetas comparten variables de colección (tokens, ids y alias de la carpeta 1,
  `consentimiento_id`, `oferta_id`) y la carpeta 4 necesita que la 3 haya dejado datos en caché.
- **Requiere el BACKOFFICE local.** La carpeta 1 inicia sesión con el usuario que crea la tarea `crear-backoffice` del
  compose; sin él, nada de lo demás tiene token. Corre la colección completa, no peticiones sueltas.
- **Es repetible.** Los alias del ASESOR y del CLIENTE cambian en cada corrida (solo letras: un alias con 6 o más
  dígitos seguidos se rechaza por parecer un documento). La carpeta 4 restaura el stub a `sano` y espera 10 s a que los circuitos del ACL Worker
  cierren, así que se puede correr varias veces seguidas.
- **Solo contra local.** La carpeta 4 manipula el stub de Open Finance, la 9 manipula el stub de la pasarela y lee
  PAYMENTS directamente, y la 2 consulta AUTH directamente; en staging nada de eso es accesible. Para staging, omite esas peticiones y cambia `bff_url` y las credenciales del
  BACKOFFICE en el entorno (nunca las subas al repo).
- **Datos ficticios.** El documento `CC-NEWMAN-100001` no es una persona real. No pongas datos reales en la colección.
- **Los umbrales de tiempo son un piso**, medidos en un portátil con proveedores simulados (regla 9 de
  `CLAUDE.md`). No reemplazan las pruebas de carga con k6 de EC003 / EC004 (`tests/k6/`).
