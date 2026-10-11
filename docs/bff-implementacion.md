# Implementación del BFF GraphQL (KAN-24, HU-W01)

Resumen de lo que se construyó para el BFF en la rama `feat/KAN-24-bff-graphql`, las decisiones que se tomaron
y cómo verificarlo. Cubre la tarea **T-W01-7** del Sprint 1 y deja listo el camino para HU-W05 (KAN-28).

Documentos relacionados: [README del BFF](../services/bff/README.md) · [arquitectura](arquitectura-backend.md) ·
[contrato GraphQL](../contracts/graphql/schema.graphql) · [pruebas Newman](../tests/newman/README.md).

## 1. Por qué

El BFF es la única puerta pública del backend y lo único que consume `solventa-frontend`, pero solo respondía
`/health` y `/metrics`. El contrato GraphQL ya existía y ningún servicio lo implementaba, así que web y móvil
seguían trabajando con mocks. El objetivo: que el frontend pueda recorrer **consentimiento → oferta** de punta a
punta contra el stack local y, más adelante, contra staging.

## 2. Decisiones tomadas

| Decisión | Resultado | Por qué |
|---|---|---|
| ¿Quién consulta las 5 fuentes? | **El BFF llama al ACL Worker**; RISK no lo llama | El BFF ya secuencia el flujo y RISK recibe los datos resueltos. Mantiene a RISK como escritor único sin dependencias externas |
| Consentimiento | **Mock en memoria dentro de `services/auth`** con los endpoints definitivos | AUTH solo tenía `/health`. Al ser HTTP real, cuando llegue T-W01-4 (Postgres) solo cambia el almacén, no el BFF |
| `cliente_id` | HMAC-SHA256 del documento, con clave | El contrato recibe el documento pero los servicios esperan un id opaco. Marcado con **REWIRE** para reemplazarlo cuando AUTH tenga la tabla de identidad |
| GraphQL | **Strawberry** (code-first), en `/graphql/web` y `/graphql/app` | Propuesta de ADR-01, confirmada al construirlo. Un solo esquema, el canal viaja en el contexto |
| Alcance | **Solo HU-W01**; cobros quedan "no disponible" | Una historia por PR (`docs/convenciones.md`); los cobros llegan con KAN-28 |

## 3. Flujo de `solicitarOferta`

```
Cliente ─► BFF ──1─► AUTH      ¿consentimiento vigente? (sin él no se consulta nada, regla #7)
              ├─2─► ACL ×5     las 5 fuentes en paralelo (timeout duro de 700 ms, degrada a caché)
              ├─3─► RISK       escribe el perfil y devuelve operation_time (D-02)
              └─4─► RATING     calcula la oferta leyendo el perfil con sesión causal
        ◄──────────────────── Oferta COMPLETA o PRELIMINAR, o ErrorOferta
```

| Situación | Resultado para el cliente |
|---|---|
| Todo sano | `Oferta` COMPLETA con prima puntual |
| Un proveedor lento o caído | `Oferta` PRELIMINAR con rango de prima; las fuentes afectadas vienen en `CACHE` con su fecha de captura |
| El ACL Worker mismo no responde | Esa fuente va como degradada y la oferta sale PRELIMINAR |
| Sin consentimiento vigente | `ErrorOferta`, sin reintento, y **ninguna fuente se consulta** |
| AUTH, RISK o RATING caídos | `ErrorOferta` con reintento |
| Valor del crédito inválido | `ErrorOferta` sin reintento |
| Error inesperado en el servidor | Mensaje genérico "Unexpected error." sin detalles internos |

En ningún caso el cliente recibe un 5xx crudo (regla #4); los errores de GraphQL viajan con HTTP 200.

## 4. Qué se construyó

### 4.1 AUTH — mock de consentimiento (`services/auth`)

| Ruta | Función |
|---|---|
| `POST /consentimientos` | Registra y devuelve el consentimiento vigente |
| `GET /consentimientos/{id}` | Consulta; `404` si no existe |
| `POST /consentimientos/{id}/revocar` | Marca `revocado_en` (append-only: no borra); idempotente |

Limitaciones del mock: el estado se pierde al reiniciar, no se comparte entre instancias de Cloud Run y no emite
eventos. Está documentado en el README de AUTH y en el docstring de `app/consentimientos.py`.

### 4.2 BFF (`services/bff/app/`)

| Archivo | Responsabilidad |
|---|---|
| `esquema.py` | Tipos y resolutores de Strawberry, espejo 1:1 del contrato |
| `servicio_oferta.py` | Orquestación de `solicitarOferta` |
| `clientes.py` | Clientes HTTP hacia AUTH, ACL, RISK y RATING, con `Protocol` para pruebas |
| `identificadores.py` | `id_opaco` para logs y `cliente_id_desde_documento` (punto **REWIRE**) |
| `autenticacion_servicios.py` | Token de identidad hacia servicios privados de Cloud Run |
| `configuracion.py` | Variables de entorno; se niega a arrancar en la nube con la clave local |
| `main.py` | `create_app`, las dos rutas por canal, `/health` y `/metrics` |

Operaciones del contrato y su estado:

| Operación | Estado |
|---|---|
| `registrarConsentimiento`, `revocarConsentimiento` | Completas (sobre el mock de AUTH) |
| `solicitarOferta` | Completa |
| `oferta(id)` | Devuelve `null`: RATING todavía no guarda ofertas |
| `contactarAsesor` | **Mock**: solo confirma |
| `cobros`, `reintentarCobro` | Error "no disponible: KAN-28" (HTTP 200) |

### 4.3 Infraestructura

- **`.github/servicios.json`:** el BFF llama a `acl-worker` y recibe `AUTENTICACION_SERVICIOS=id_token` y el
  secreto `CLIENTE_ID_CLAVE` (`clave-cliente-id`). RISK ya no tiene permiso sobre el ACL Worker, que nunca usaba.
  Terraform deriva variables, permisos y secreto del manifiesto, así que **no hubo cambios en `.tf`**.
- **`docker-compose.yml`:** clave solo local (el repo es público) y `depends_on` sano de AUTH, ACL, RISK y RATING.
- **Token entre servicios:** el BFF pide el token de identidad al servidor de metadatos de Cloud Run con `httpx`
  (sin `google-auth`, que arrastraría `requests`), lo cachea 50 minutos y usa el nombre del servicio como audiencia.
- **Secreto:** la carga de `clave-cliente-id` está documentada en `infra/README.md`. La clave **no debe rotarse**
  mientras haya datos en RISK, porque dejaría huérfanos los perfiles existentes.
- **Dependencia nueva:** `strawberry-graphql[fastapi]==0.327.7` (la última con más de dos semanas al momento de fijarla).

## 5. Pruebas

| Nivel | Qué | Resultado |
|---|---|---|
| Unitarias del BFF | `pytest` con dobles; sin red | **38 pasan** |
| Unitarias de AUTH | `pytest` | **8 pasan** |
| Contrato | El esquema de Strawberry contra `contracts/graphql/schema.graphql`, en ambos sentidos | Sin diferencias |
| Humo | `scripts/humo_bff.py`; también corre en el job `humo` del CI | En verde, 3 corridas seguidas |
| Newman | `tests/newman/`: 18 peticiones, 49 aserciones | **49 de 49**, 2 corridas seguidas |
| Lint | `ruff check` y `ruff format --check` en BFF, AUTH y scripts | Limpio |
| Terraform | `fmt -check` y `validate` | Sin errores |

Qué cubren las pruebas unitarias: flujo sano, una fuente que falla, consentimiento inexistente o revocado (con
**cero** llamadas al ACL), AUTH / RISK / RATING caídos, valor inválido, que el `cliente_id` no contenga el
documento, que los logs no lo contengan, enmascarado de errores internos y el cache del token.

### Mediciones en vivo (stack local de `docker compose`)

| Caso | Resultado | Tiempo |
|---|---|---|
| Proveedores sanos | `Oferta` COMPLETA | ~130 ms en caliente (784 ms la primera vez) |
| Open Finance lento | `Oferta` PRELIMINAR; las 2 fuentes de Open Finance en `CACHE` | 726 – 787 ms |
| Tras revocar el consentimiento | `ErrorOferta` sin reintento | 7 – 12 ms |

Son muestras sueltas en un portátil con proveedores simulados, **no percentiles**. Son un piso, no un valor de producción.

### Un hallazgo de las pruebas de integración

La colección de Newman detectó, en su primera corrida, que una consulta con un campo inexistente devolvía
`"Unexpected error."` en lugar del mensaje de validación. El enmascarado de errores era demasiado agresivo: ocultaba
también los errores del esquema, que no revelan nada interno y que el frontend necesita para depurar. Se corrigió en
`app/esquema.py`: ahora solo se enmascaran las excepciones inesperadas dentro de un resolutor, y hay una prueba
unitaria que lo fija.

## 6. Cómo verificarlo

```bash
# Pruebas unitarias
cd services/bff && pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest

# Stack completo (si 5432 o 6379 están ocupados: POSTGRES_PORT=5433 REDIS_PORT=6380)
docker compose --profile servicios up -d --build --wait

# Humo y Newman
python scripts/humo_bff.py
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json

# Probar a mano: http://localhost:8000/graphql/web  (GraphiQL en el navegador)
docker compose --profile servicios down -v
```

## 7. Límites y pendientes

| Tema | Detalle |
|---|---|
| **Latencia de la oferta degradada** | Una oferta PRELIMINAR tarda ~750 ms porque el ACL espera su corte de 700 ms. Supera el p95 de 400 ms de CA-W01-03. Se mide con k6 (EC003 / EC004) en PF2-2 |
| **Token entre servicios** | PAYMENTS → ACL y `consolidador-fuentes` → ACL todavía no envían token: en Cloud Run recibirían 403. Hay que corregirlo antes del primer despliegue |
| **Mock de AUTH en staging** | `max_instancias` es 2: un consentimiento creado en una instancia no existe en la otra, y la solicitud puede responder "sin consentimiento" al azar. Se resuelve con T-W01-4 |
| **Revocación (CA-W01-08)** | El BFF valida el consentimiento en cada solicitud, pero la caché del ACL (300 s) no se invalida activamente |
| **`cliente_id`** | Provisional hasta que AUTH tenga la tabla de identidad (punto REWIRE en `identificadores.py`) |
| **Cifrado de campo (SEG-002)** | Sigue fuera de alcance en RISK; no lo cubre este cambio |
| **Nada se ha desplegado** | El pipeline sigue apagado y no existe proyecto GCP; lo de Cloud Run (token, secreto) no se ha ejecutado nunca |
| **Git** | Falta reintegrar `main` en `develop` con *merge commit*: el PR #75 se integró con *squash* y git no registra la ascendencia |

## 8. Historial de cambios (rama `feat/KAN-24-bff-graphql`)

Un commit por paso, con la clave de Jira:

1. `chore(bff)` — el BFF orquesta la consulta de fuentes al ACL Worker (manifiesto, compose, arquitectura)
2. `feat(auth)` — mock en memoria del registro de consentimientos
3. `feat(bff)` — GraphQL con Strawberry y orquestación de `solicitarOferta`
4. `infra(bff)` — secreto `clave-cliente-id`, token entre servicios y arranque en compose
5. `test(bff)` — humo GraphQL de punta a punta, en compose y en el CI
6. `docs(bff)` — README del BFF, trazabilidad de T-W01-7 y hueco del token entre servicios
7. `fix(bff)` — no enmascarar los errores de validación del esquema
8. `test(bff)` — colección de Newman y este documento
