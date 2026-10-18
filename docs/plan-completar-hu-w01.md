# Plan para completar HU-W01

> Fuente para los agentes de este repo: las decisiones de abajo están **cerradas** (las tomó felipe con el equipo el
> 2026-10-17 y 2026-10-18). No las reinterpretes; si algo del documento de arquitectura choca con ellas, manda esto.

## Decisiones tomadas

| # | Tema | Decisión |
|---|---|---|
| 1 | Datos suficientes (CA-W01-05) | Las 5 fuentes se consultan **en paralelo**; solo `historial-crediticio` es obligatoria. Sin historial (ni en línea ni en caché) → `ErrorOferta` con reintento. Historial OK y cualquier otra caída o caché → **PRELIMINAR**. Las 5 en línea → **COMPLETA**. La regla vive en RATING (`DatosInsuficientes` → 422) y el BFF la traduce. |
| 2 | Plazo y dirección | Se validan en el BFF y entran a la prima en RATING (`factor_plazo` y factor por ciudad detectada en la dirección). Google Geocoding vía el ACL queda como trabajo futuro. |
| 3 | Dónde se mide | Todo (k6, tableros, E2E de Cypress) **contra staging desplegado**. El compose local no es parte del plan de medición. Lo único simulado son los proveedores externos (stubs, D-05). |
| 4 | Despliegue | Sin Kubernetes y **sin malla de servicios**. En tránsito: HTTPS de Cloud Run + token de identidad entre servicios. |
| 5 | Observabilidad | Grafana **en local** (compose `observabilidad`, puerto 3100) leyendo **Google Cloud Monitoring** de staging con la cuenta `grafana-lector` (solo `roles/monitoring.viewer`). La llave JSON la crea felipe a mano y nunca va al repo. Solo métricas nativas de Cloud Run: **sin Managed Service for Prometheus** (cobra). Atlas se mira en su consola. |
| 6 | Cifrado en reposo (cierra D-04) | Cifrado de campo AES-GCM en AUTH y RISK (RATING descifra al leer); la llave de datos vive en **Secret Manager envuelta por Cloud KMS** (`crear_kms = true`). **CMEK** en Cloud SQL (instancia nueva + migración de datos, PR propio) y en Memorystore (en el próximo encendido). **Atlas se queda en M0 sin CMEK** (CMEK exige M10, ~USD 57/mes): lo cubre el cifrado de campo. |
| 7 | Seguridad en CI/CD | Se agregan Bandit, pip-audit, Trivy y OWASP ZAP baseline (back) y `npm audit` (front), **informativos** al principio (fuera de `CI OK`). Sonar ya existe en los dos repos; se activa cargando `SONAR_TOKEN` y `SONAR_HOST_URL`. |
| 8 | Perfil de riesgo | `PerfilRiesgo` gana `nivelRiesgo` (BAJO/MEDIO/ALTO) y `fechaVigencia`, junto con el score guardado en RISK. |
| 9 | Eventos | Pub/Sub **solo para `PerfilActualizado`** (RISK publica, RATING borra su caché del score), detrás de la bandera `crear_pubsub`. Los demás eventos, cuando una historia los pida. |
| 10 | Guardar la oferta | **No se guarda en W01.** Se calcula, se muestra y se recalcula si el cliente vuelve. Pasa al **Sprint 2 con HU-W03**: se guarda la oferta que el cliente **acepta**. Avisar cuando una preliminar pase a completa no está en ninguna HU. |
| 11 | Exploratoria FC-01 | Plantilla en `docs/evidencias/exploratoria-FC-01.md`; la sesión la hace una persona contra staging. |
| 12 | HU-W05 | Opción A del prototipo: RECHAZADO se reintenta a mano hasta 3 veces (referencia `id_intento_cobro-rN`); PENDIENTE lo reintenta el sistema. Sin captura de tarjeta (detalle en `docs/cobros-conciliacion.md`). |
| 13 | Costos | Solo los aprobados: Cloud KMS (centavos al mes) y Pub/Sub, Cloud Scheduler y Cloud Tasks en capa gratuita. Nada más sin confirmación. |
| 14 | Pruebas a escala | Para **todas** las historias de arquitectura: no se repiten los experimentos; se prueba con menos tiempo, menos volumen o picos cortos y se proyecta con regla de tres, diciendo en la evidencia qué se midió y qué se proyectó. Todo en una sola sesión contra staging. Ver "Cómo se prueban todas las historias de arquitectura sin gastar". |
| 15 | Batch nocturno (HA-ESC-004) | Se adelanta del Sprint 3 al **Sprint 1** (bloque 9). |
| 16 | API Gateway (HA-LAT-004) | Se monta **en el Sprint 1** un API Gateway de GCP delante del BFF, con plazo duro de 700 ms. El BFF deja de ser público: solo el gateway lo invoca. El presupuesto ≤ 120 ms por proveedor vive en el ACL (es quien llama a los proveedores). Bloque 10. |
| 17 | JWT en el gateway | En el Sprint 1 el JWT lo sigue validando el BFF (HS256). Pasarlo al gateway exige ES256 + JWKS en AUTH: Sprint 2. |
| 18 | ESC-001 | Se queda en el **Sprint 2** (ya tendrá el gateway montado). |

Basado en la revisión de pendientes de HU-W01 (18 puntos, numerados P1–P3 abajo) y en las restricciones que puso el equipo el 2026-10-18:

- **No se cambia el despliegue** (Cloud Run, sin Kubernetes) **ni la arquitectura**.
- **No se generan costos nuevos en GCP**, salvo los aprobados: Cloud KMS (centavos al mes) y Pub/Sub en su capa gratuita.
- Se mide **contra staging ya desplegado** (el despliegue a staging corrió bien el 2026-10-18 a las 17:41 UTC). Lo único
  simulado son los **proveedores externos** (Open Finance, Open Data y la pasarela): en staging son los stubs desplegados
  como servicios de Cloud Run (decisión D-05), porque no hay contrato con Belvo/Prometeo. La infraestructura es la real.
- **Grafana corre en local** y se conecta a **Google Cloud Monitoring** para ver la infraestructura desplegada.

## Respuestas a las preguntas del equipo

**¿Cómo hacemos k6 si no podemos tocar el despliegue?** k6 corre desde tu máquina (o un job manual de GitHub) **contra
la URL pública del BFF en staging**; no se toca el despliegue. Los modos de falla se fuerzan con el endpoint de control de
los stubs de staging. Costo: solo el uso de Cloud Run de esas peticiones, que para una prueba de una hora a 20.000 req/h
cabe en la capa gratuita (2 millones de peticiones al mes); Cloud SQL, Memorystore y Atlas ya están pagos.

**Seguridad en CI/CD tipo SonarQube, open source:** sí, y sin costo:
- **SonarCloud** (gratis para repos públicos, y los dos lo son): el job `sonar` ya existe en el CI del backend; solo falta
  crear el proyecto y cargar `SONAR_TOKEN` y `SONAR_HOST_URL`. Igual en el front.
- **Bandit** o **Semgrep** (SAST de Python), **pip-audit** / `npm audit` (dependencias), **Trivy** (imágenes Docker) y
  **OWASP ZAP baseline** (DAST contra el BFF levantado en el job del humo). Todos son acciones de GitHub gratis.
- Ya hay **gitleaks**, **Dependabot** y **actionlint**.

**¿Una malla de servicios (service mesh) para cifrar en reposo y en tránsito?** **No la recomiendo:**
- Una malla (Istio, Linkerd, Anthos Service Mesh) **necesita Kubernetes**: en Cloud Run no hay dónde poner los sidecars.
  Sería cambiar el despliegue y pagar GKE, justo lo que no queremos.
- Una malla **solo cifra en tránsito** (mTLS). **No cifra en reposo**.
- **En tránsito ya está cubierto** sin malla: Cloud Run sirve solo por HTTPS (TLS), y entre servicios cada llamada va
  con token de identidad (PR del token entre servicios).
- **En reposo** (decidido 2026-10-18: se usa **Secret Manager + CMEK con Cloud KMS** desde ya, el costo se acepta):
  campos sensibles cifrados en la aplicación con AES-GCM (como AUTH) y además los almacenes con llave propia de Cloud KMS.
  Detalle en el bloque 3.

**¿Grafana local conectado a GCP para monitorear lo desplegado?** Sí:
- El Grafana del compose (`observabilidad/`, puerto 3100) con el datasource **Google Cloud Monitoring**, provisionado
  como código, y una cuenta de servicio con rol **Monitoring Viewer** (solo lectura). La llave JSON la carga una persona
  del equipo en Grafana; nunca va al repo.
- Tableros: los de Cloud Run por defecto (latencia p50/p95/p99, peticiones por código de respuesta, instancias) y uno por
  HU (W01: BFF, AUTH, ACL Worker, RISK, RATING y stubs; W05: BFF, PAYMENTS, ACL Worker y pasarela), más Cloud SQL,
  Memorystore (Atlas no está en Cloud Monitoring: sus métricas se ven en la consola de Atlas).
- Las métricas propias de `/metrics` (Prometheus) de cada servicio **no** llegan a Cloud Monitoring sin Managed Service
  for Prometheus, que sí cobra. Para no generar costo, en staging se usan las métricas nativas de Cloud Run.

## Plan de cambios

Orden pensado para que lo primero dé números y lo siguiente los mejore. Cada bloque es un PR hacia `develop` con su
rama `feat|fix|chore/KAN-N-…`.

### Bloque 1 · Medir: k6 contra staging + tableros + evidencias (P1 #1, #2, #7, #18)

| Repo | Cambio |
|---|---|
| back | `tests/k6/perfilamiento-baseline.js` (EC003 p95 ≤ 400 ms, EC004 p99 ≤ 800 ms, ritmo de 20.000 req/h), `tests/k6/falla-proveedor.js` (EC009/EC010: stub en `lento`/`caido`, corte a 700 ms, 0 % de 5xx; copiado del Experimento 1) y `tests/k6/lectura-replica.js` (EC011/EC012 y lag; del Experimento 2). |
| back | `scripts/correr_k6.sh`: corre los guiones contra la URL del BFF de staging y guarda el resumen. |
| back · infra | Terraform: cuenta de servicio `grafana-lector` con solo `roles/monitoring.viewer` en el proyecto de staging (sin costo). Se aplica por el pipeline. |
| equipo (a mano, una vez) | Una persona con acceso al proyecto crea la llave: `gcloud iam service-accounts keys create ~/Downloads/grafana-lector-key.json --iam-account=grafana-lector@solventa-511815.iam.gserviceaccount.com` y la copia a `observabilidad/grafana/grafana-lector-key.json` (ignorado por git con `*-key.json`). Si GCP responde que la política `iam.disableServiceAccountKeyCreation` lo impide, un admin de la organización la desactiva solo para `solventa-511815`. |
| back | `observabilidad/grafana/provisioning/datasources/`: datasource de Google Cloud Monitoring (proyecto de staging) que lee la llave del archivo local montado en el contenedor; si el archivo no está, Grafana arranca igual con los tableros de Prometheus local. `observabilidad/grafana/dashboards/hu-w01.json`: latencia p95/p99 y códigos de respuesta de los servicios de W01 en Cloud Run, instancias, Cloud SQL y Memorystore. |
| back | `docs/evidencias/EC003-EC004-<fecha>.md`, `EC009-EC010-<fecha>.md`, `EC011-EC012-<fecha>.md` y la corrida de Newman (FC-06), con comando, cifras y límites. |

### Bloque 2 · Caché del score y journey degradado (P1 #3, #4)

| Repo | Cambio |
|---|---|
| back · RISK | Mover el **Cache-Aside del score a RISK**, como dice el documento: RISK calcula y guarda el score (`ScoreCacheado`) en Redis con clave estable (cliente + versión de reglas) y TTL. |
| back · ACL | Ante una solicitud repetida dentro del TTL, servir el dato reciente de la caché **sin llamar al proveedor**. Hoy la caché solo se usa cuando el proveedor falla. |
| back · ACL | **EC010 ≤ 500 ms**: cuando hay dato en caché, competir caché contra proveedor con un presupuesto menor (p. ej. 300 ms) y quedarse con la caché si el proveedor no llega. Sin caché, mantener el corte de 700 ms. |
| back · RATING | Leer el score que deja RISK. El cálculo de prima no cambia. |
| back | Volver a correr el k6 del bloque 1 y actualizar la evidencia. |

### Bloque 3 · Cifrado en reposo: Secret Manager + Cloud KMS (CMEK) (P1 #5)

**Decidido (felipe, 2026-10-18):** se usa Secret Manager y CMEK con Cloud KMS de una vez, sin importar el costo.
El Terraform **ya tiene** el key ring `solventa-backend` y la llave `campos-personales` (rotación de 90 días) detrás de
la bandera `crear_kms`, que hoy está en `false` en `infra/staging.tfvars`.

| Repo | Cambio |
|---|---|
| back · infra | `crear_kms = true` en `staging.tfvars`. Ojo: un key ring de KMS **no se puede borrar** nunca. Se aplica por el pipeline (merge a `main`) con el guardia de destrucciones. |
| back · infra | Permiso `roles/cloudkms.cryptoKeyEncrypterDecrypter` sobre la llave solo para las cuentas de AUTH, RISK y RATING (escritor y lector). |
| back · AUTH y RISK | **Cifrado de sobre:** la llave de datos de AES-GCM (`CIFRADO_CLAVE`) se guarda en Secret Manager **envuelta por la llave de KMS**; el servicio la desenvuelve con KMS al arrancar y la deja en memoria. Rotar la llave de KMS no obliga a recifrar los datos. En local y en CI sigue la llave en claro de entorno (sin KMS). |
| back · RISK | Copiar el cifrador AES-GCM de AUTH y cifrar los datos de las fuentes antes de guardarlos. Migración idempotente de MongoDB que cifra lo ya guardado. |
| back · RATING | Descifrar al leer de la réplica (misma llave de datos, solo lectura). |
| back · infra · Cloud SQL | **CMEK del disco** (`encryption_key_name`). Solo se puede poner **al crear** la instancia: hay que crear una instancia nueva con CMEK, migrar `auth` y `payments` con export/import y borrar la vieja. El guardia del pipeline bloquea el reemplazo, así que se hace en un PR propio con ventana acordada. Costo: el mismo de la instancia actual. |
| back · infra · Memorystore | **CMEK** (`customer_managed_key`) al crear la instancia. Como Redis es caché desechable y ya se apaga y prende con `redis_encendida`, se aplica en el próximo encendido. |
| back · infra · MongoDB Atlas | **Se queda en M0, sin CMEK** (decidido por felipe el 2026-10-18, opción A). CMEK en Atlas solo existe desde M10 (~USD 57/mes, casi una quinta parte del presupuesto de USD 300 cada mes). En M0 el disco lo cifra Atlas con su llave (AES-256) y los campos sensibles de RISK quedan cifrados con nuestra llave de KMS por el cifrado de campo: sin esa llave no se leen, que es lo que pide HA-SEG-002 y prueba EC022. Si algún día se sube a M10, se activa "Encryption at Rest using your Key Management" con la misma llave. |
| back | Prueba de EC022: un campo en Mongo y una fila en Postgres no se leen sin la llave; y deshabilitar la versión de la llave de KMS deja el servicio sin poder descifrar. Evidencia en `docs/evidencias/EC022-<fecha>.md`. |

**Costo aproximado:** KMS ~USD 0,06 por llave al mes + ~USD 0,03 por 10.000 operaciones (centavos). Cloud SQL y
Memorystore cuestan lo mismo que hoy y Atlas sigue gratis (M0). No hay que subir el presupuesto.

### Bloque 4 · Consentimiento completo (P2 #8, #9)

| Repo | Cambio |
|---|---|
| back · contrato | `ConsentimientoInput` suma `documentoIdentidad`, `tipoDato`, `fuenteDatos` y `canal` (aditivo); `Consentimiento` suma `estado`. |
| back · AUTH | Migración que agrega esas columnas y el titular (hash opaco del documento, nunca el documento en claro). |
| back · BFF | `solicitarOferta` exige que el consentimiento sea del mismo titular que el documento. |
| back · ACL + stub | La consulta a Open Finance lleva `consentimiento_id` y `finalidad`; el stub responde 403 sin ellos (CE 004/2024). |
| front | `useCotizacion` manda los campos nuevos al registrar el consentimiento; sincronizar contrato y mocks. |

### Bloque 5 · Fuentes por configuración y EC031 (P2 #10, #11)

| Repo | Cambio |
|---|---|
| back | Un solo archivo de fuentes (`nombre`, `origen`, `peso`, `obligatoria`) que leen el BFF, el ACL y RATING; agregar una fuente es un adaptador más una fila. |
| back | Prueba de EC031: cambiar el adaptador de la pasarela por otro que cumple el mismo puerto, sin tocar PAYMENTS ni el BFF; evidencia en `docs/evidencias/`. |

### Bloque 6 · Seguridad en CI/CD (P2 #12)

**Estado hoy (revisado en `develop`):** SonarQube **ya está** como job `sonar` en los dos repos (`ci.yml` del back y
`ci-web.yml` del front), pero **no corre**: sus pasos se saltan porque faltan los secretos `SONAR_TOKEN` y
`SONAR_HOST_URL`. Lo único de seguridad que sí corre es **gitleaks** (back) y **Dependabot** (los dos). **No existen**
Bandit/Semgrep, pip-audit, `npm audit`, Trivy ni OWASP ZAP.

**Se crean pipelines nuevos** (pasos de GitHub Actions, sin recursos en GCP ni costo; los repos son públicos):

| Repo | Archivo | Paso nuevo | Qué revisa | Cómo corre |
|---|---|---|---|---|
| back | `.github/workflows/ci.yml`, job `servicio` (por cada servicio) | **Bandit** | Patrones inseguros en el código Python | Después de `ruff`; reporte como artefacto |
| back | mismo job | **pip-audit** | Librerías Python con vulnerabilidades conocidas | Sobre el `requirements.txt` del servicio |
| back | mismo job, después de "Construir imagen" | **Trivy** | Paquetes vulnerables en la imagen Docker | Severidad HIGH/CRITICAL; reporte como artefacto |
| back | job `humo` (ya levanta el compose) | **OWASP ZAP baseline** | El BFF levantado: cabeceras, errores expuestos, configuración | Contra `http://localhost:8000/graphql/web`; reporte HTML como artefacto |
| front | `.github/workflows/ci-web.yml`, job `web` | **npm audit** | Librerías npm con vulnerabilidades | `--audit-level=high` |
| back y front | job `sonar` existente | **SonarCloud** | Calidad y seguridad del código | Solo hay que activarlo (abajo) |

**Política:** al principio son **informativos** (`continue-on-error`, no entran en `CI OK`): dejan su reporte en cada
PR sin bloquear. Cuando los hallazgos estén resueltos, se suman a `CI OK` y pasan a ser obligatorios.

**Para activar Sonar** (lo hace alguien con permisos de admin en `solventa-app`): crear los proyectos
`solventa-backend` y `solventa-frontend` en SonarCloud (gratis para repos públicos) y cargar `SONAR_TOKEN` y
`SONAR_HOST_URL=https://sonarcloud.io` en *Settings → Secrets and variables → Actions* de cada repo.

**Evidencia:** los reportes de cada herramienta se guardan como artefactos del workflow y se resumen en
`docs/evidencias/seguridad-<fecha>.md` (estrategia de pruebas: SAST con SonarQube y DAST con OWASP ZAP).

### Bloque 7 · Front (P2 #6, P3 #14)

| Repo | Cambio |
|---|---|
| front | Cypress de **valores límite** en W01-01 (documento de 6 dígitos, plazo 1/360, valor 0, dirección vacía). |
| front | Correr los E2E de W01 **contra el backend de staging** (no solo mocks) y guardar el video o el reporte como evidencia de FC-01. |
| front | Pantalla de **revocar consentimiento** desde el detalle de fuentes (CA-W01-08). |
| front | Tablero en Grafana: no aplica en el front; las métricas salen del BFF. |

### Bloque 8 · Pendientes de diseño (P2 #13, P3 #15–#17)

**Decidido (felipe, 2026-10-18):** van tres de los cuatro. **Guardar la oferta pasa al Sprint 2, con HU-W03**: ningún
criterio de W01 (CA-W01-01 a 11) ni el prototipo piden guardarla. En W01 la oferta (completa o preliminar) se calcula,
se muestra y no se guarda; si el cliente vuelve, se recalcula rápido con la caché de fuentes y del score. Avisar al
cliente cuando una preliminar pase a completa no lo pide la HU (sería una historia nueva). En W03 se guarda la oferta
que el cliente **acepta**, con precio, plazo y si fue completa o preliminar; ahí se decide quién la escribe.

| Repo | Cambio |
|---|---|
| back · RISK | **`PerfilRiesgo` con `nivelRiesgo` (BAJO/MEDIO/ALTO) y `fechaVigencia`**, junto con el score guardado del bloque 2. Cambio aditivo en el contrato. |
| back · infra | **Pub/Sub, solo el evento `PerfilActualizado`.** Tema y suscripción en Terraform detrás de una bandera `crear_pubsub` (capa gratuita: 10 GB/mes). RISK publica al actualizar el perfil; RATING se suscribe y borra su caché del score. Esquema del evento en `contracts/eventos/`. Los demás eventos, cuando una historia los pida. |
| back | **Plantilla de la exploratoria de FC-01 y de los indicadores** en `docs/evidencias/exploratoria-FC-01.md`: misión, tiempo, datos usados, hallazgos y tabla de indicadores (defectos por severidad, casos ejecutados, cobertura, escenarios EC cumplidos). La sesión la hace una persona (ver "Lo que hace felipe a mano"). |

### Bloque 9 · Batch nocturno de reprocesamiento (HA-ESC-004, EC017), a escala reducida

**Qué pide:** RISK reprocesa en masa los perfiles fuera de horario pico (≥ 10 millones en < 2 h) sin afectar el tráfico en
línea. **No existe hoy.** `docs/sprint-1.md` la dejaba para el Sprint 3; **felipe decidió el 2026-10-18 hacerla ahora,
en el Sprint 1** (con la prueba reducida de abajo). Al ejecutar el bloque se corrige la línea de diferidas de `docs/sprint-1.md`.

| Repo | Cambio |
|---|---|
| back · infra | **Cloud Scheduler** (el "cron", 3 tareas gratis al mes) que de noche lanza un **Cloud Run Job** `risk-reproceso`, aislado del servicio en línea (otra instancia, su propio límite de instancias). Si hace falta repartir el trabajo, por **Cloud Tasks** (1 millón de operaciones gratis al mes). Detrás de una bandera `crear_batch`. |
| back · RISK | El job lee perfiles por lotes, recalcula y escribe con el mismo escritor único (RISK). Idempotente: si se corta, retoma donde iba. |
| back | `scripts/sembrar_perfiles.py` para cargar perfiles sintéticos en staging y la prueba de EC017 a escala reducida (abajo). Evidencia en `docs/evidencias/EC017-<fecha>.md`. |

### Bloque 10 · API Gateway delante del BFF (HA-LAT-004: EC001, EC002, EC008)

**Qué pide LAT-004:** un API Gateway con **timeout duro de 700 ms** y un **presupuesto ≤ 120 ms por proveedor**, con
fallback. EC001/EC002: p99 ≤ 500 ms extremo a extremo; EC008: ≤ 120 ms por dependencia. Hoy no hay gateway: el BFF es
la puerta pública. **Decidido (felipe, 2026-10-18): se hace en el Sprint 1.**

**Cómo queda el tráfico:**
```
Nube:   navegador → Vercel (rewrite /graphql) → API Gateway (700 ms) → BFF (privado) → servicios
Local:  navegador → Vite (proxy /graphql)     → gateway local :8080 (Envoy, 700 ms) → BFF :8000 → servicios
```

**En la nube (GCP API Gateway):**
- Es un servicio administrado que se configura con un archivo **OpenAPI 2.0** (`infra/gateway/openapi.yaml`): rutas
  `POST /graphql/web`, `POST /graphql/movil` y `GET /health`, cada una con `x-google-backend` apuntando al BFF de Cloud
  Run y `deadline: 0.7`.
- Por dentro corre ESPv2, que está hecho sobre **Envoy**; por eso localmente se usa Envoy.
- El gateway llama al BFF con su propia cuenta de servicio (`gateway-bff`, rol `run.invoker` solo sobre el BFF). El BFF
  **deja de ser público** (se quita `publico` del BFF en `.github/servicios.json`).
- Costo: gratis hasta 2 millones de llamadas al mes; después USD 3 por millón.

**En local (docker compose):**
- Servicio `gateway` (imagen oficial de Envoy) en el perfil `servicios` del compose, configurado con
  `infra/gateway/envoy.yaml`: las mismas rutas y el mismo plazo de 700 ms, en el puerto **8080**.
- El front local apunta a `VITE_BFF_URL=http://localhost:8080`. Ir directo a `:8000` sigue funcionando para depurar.
- Un script (`scripts/validar_gateway.py`) compara rutas y plazos de `openapi.yaml` y `envoy.yaml` para que no se separen.

**El presupuesto por proveedor (≤ 120 ms):** el gateway solo ve la petición al BFF, no las llamadas a los proveedores.
Ese presupuesto se pone donde están esas llamadas: en el **ACL Worker**, con un plazo configurable por proveedor
(`PRESUPUESTO_MS_<PROVEEDOR>`), y se mide por dependencia en la corrida de k6 (EC008).

**Riesgos y cómo se cubren:**
1. **Hoy la oferta preliminar tarda ~730 ms** y el gateway la cortaría en 700 ms con un 504. Por eso el **bloque 2
   (EC010 ≤ 500 ms) entra antes o junto con este bloque**.
2. **Arranque en frío:** si el BFF o un servicio del camino está apagado (escala a cero), la primera petición tarda más de
   700 ms y el gateway responde 504. El front muestra ese 504 como "intenta de nuevo" (regla 4: nunca un 5xx crudo) y
   para las sesiones de medición se usa `min_instances = 1` en los servicios del camino crítico solo mientras dura la
   sesión (bandera `calentar_camino_critico`, se apaga al terminar).
3. **El guardia de destrucciones** (`scripts/revisar_plan.py`) bloquearía el reemplazo de la configuración del gateway
   (`google_api_gateway_api_config` es inmutable: cada cambio crea una nueva). Se agrega una excepción solo para ese
   recurso, con `create_before_destroy`.

| Repo | Cambio |
|---|---|
| back · infra | `infra/gateway.tf` (proveedor `google-beta`): API, configuración desde `openapi.yaml`, gateway en `us-central1` y cuenta `gateway-bff`, detrás de la bandera `crear_gateway`. Salida `gateway_url`. |
| back · infra | `.github/servicios.json`: el BFF deja de ser `publico`. `scripts/validar_servicios.py` lo acepta. |
| back · infra | `scripts/revisar_plan.py`: excepción para reemplazar `google_api_gateway_api_config`. |
| back · compose | Servicio `gateway` (Envoy) en `docker-compose.yml` y `infra/gateway/envoy.yaml`. |
| back · CI (`ci.yml`) | El job `humo` pasa por el gateway local (`:8080`) y prueba el corte: con el stub en `lento`, la respuesta llega antes de 700 ms y nunca como 5xx crudo. El job `infra` valida `openapi.yaml` y corre `validar_gateway.py`. |
| back · deploy (`deploy-staging.yml`) | La humo de la revisión nueva sigue yendo directo al BFF con token de identidad (ya funciona). Se agrega una humo final contra `gateway_url/health` después de promover, y el informe del despliegue muestra la URL del gateway. |
| back · ACL | Plazo por proveedor configurable (`PRESUPUESTO_MS_*`, 120 ms por defecto para EC008) dentro del corte duro de 700 ms. |
| back | k6 de EC001/EC002/EC008 contra `gateway_url` (prueba reducida, abajo). Evidencia en `docs/evidencias/EC001-EC002-EC008-<fecha>.md`. |
| front · Vercel | `apps/web/vercel.json`: el rewrite de `/graphql/:path*` pasa de la URL del BFF a la del gateway. |
| front · app | `graphql/cliente.ts`: un 502/503/504 se muestra como error con reintento, igual que `ErrorOferta`, nunca como pantalla rota. Prueba en Vitest. |
| front · local | `.env.example` y README: `VITE_BFF_URL=http://localhost:8080` (el gateway local). |

**Lo que no cambia:** el BFF, el contrato GraphQL, las URL que usa el navegador (siempre `/graphql/web` a través de
Vercel) y los workflows del front (`ci-web.yml`, `deploy-web.yml`, `e2e-staging.yml`).

## Cómo se prueban todas las historias de arquitectura sin gastar

Aplica a **todos** los escenarios pendientes de las historias de arquitectura, no solo al batch. Los experimentos más
importantes ya se hicieron (`solventa-arquitectura@11e4be6`): aquí no se repiten, se verifica que el producto cumple lo
mismo con la menor carga posible.

**Reglas:**
1. **Menos tiempo, mismo ritmo** cuando el ritmo real es bajo: se corre al ritmo del escenario el tiempo justo para
   tener muestras suficientes (≥ 1.000 peticiones para un p95, ≥ 3.000 para un p99).
2. **Menos ritmo y regla de tres** cuando el ritmo real no cabe en staging (Atlas M0, Cloud SQL `db-f1-micro`): se mide
   lo que saca **una instancia** y se proyecta cuántas hacen falta para la meta.
3. **Picos cortos**: un pico se sostiene 2 minutos, no una hora.
4. **Escenarios funcionales sin carga**: si lo que se prueba es un comportamiento (cifrado, cambiar un adaptador), basta
   una prueba que lo demuestre una vez.
5. Toda evidencia dice **qué se midió, qué se proyectó y con qué factor** (p. ej. "medido a 1/20 y proyectado").
6. Se corre todo en **una sola sesión** contra staging (encender Redis con `redis_encendida`, correr, apagar), para que
   el costo sea de minutos.

| HA | Escenario | Meta real | Cómo se prueba (reducido) | Qué es medido y qué proyectado |
|---|---|---|---|---|
| LAT-001 Caché de scores | EC003 operación normal | p95 ≤ 400 ms a 20.000 req/h | Ritmo real (5,6 req/s) **10 min** (~3.300 peticiones) | Medido: p95 y lectura de caché ≤ 5 ms |
| LAT-001 | EC004 carga pico | p99 ≤ 800 ms en pico | **Pico de 2 min** (rampa a 3× el ritmo normal) dentro de la misma corrida | Medido: p99. Si M0 limita, se proyecta por instancia |
| LAT-002 Circuit breaker | EC009 | 100 % de llamadas > 700 ms cortadas | Stub en `lento` **5 min** | Medido: cortes, 0 bloqueadas, overhead ≤ 1 ms |
| LAT-002 | EC010 | journey ≤ 500 ms p99 con caché | Stub en `caido` **5 min** con caché caliente | Medido: p99 y 0 % de 5xx |
| LAT-003 Réplica | EC011 / EC012 | p95 ≤ 150 ms / p99 ≤ 300 ms | Mismo tráfico de EC003 (no corrida aparte) | Medido: latencia de lectura y lag de la réplica |
| LAT-004 Presupuesto por dependencia | EC001 / EC002 / EC008 | p99 ≤ 500 ms extremo a extremo; ≤ 120 ms por dependencia | Misma corrida de EC003 pero **contra la URL del gateway** (10 min), más 2 min con el stub en `lento` para ver el corte de 700 ms | Medido: p99 a través del gateway y tiempo por dependencia en el ACL |
| ESC-004 Batch | EC017 | 10 M perfiles en < 2 h (~1.390/s) | **5 min a 1/20** (~70/s, ~21.000 perfiles) con tráfico de EC003 en paralelo | Medido: perfiles/s por instancia y p95 en línea. Proyectado: instancias para 1.390/s |
| SEG-002 Cifrado | EC022 | 100 % cifrado en tránsito y reposo | Funcional: leer Mongo y Postgres sin la llave y deshabilitar la versión de KMS | Medido (sin carga) |
| MOD-001 Capa anticorrupción | EC031 | 0 líneas cambiadas en el Core | Funcional: cambiar el adaptador de un proveedor y mostrar el diff | Medido (sin carga) |
| INT-003 Fuente nueva | EC033 | v2 junto a v1, 0 caídas | Funcional: agregar una fuente por configuración y llamar v1 y v2 | Medido (sin carga) |

**Costo de toda la sesión:** unos 30 minutos de tráfico (~10.000 peticiones a Cloud Run, dentro de la capa gratuita),
Redis encendido solo durante la sesión (~USD 0,12/h) y el Cloud Run Job del batch unos minutos.

## Documentos que actualiza cada bloque

Regla: **cada PR de un bloque actualiza en el mismo PR los documentos que dice esta tabla**, para que el repo nunca
contradiga estas decisiones. Al cerrar un bloque se marca aquí como hecho.

| Bloque | Documentos a actualizar |
|---|---|
| 1 · Medir | `observabilidad/README.md` (Grafana con Cloud Monitoring y la llave local), `infra/README.md` (cuenta `grafana-lector`), `docs/pruebas-manuales.md` (cómo correr k6 contra staging), `docs/evidencias/` (resultados) |
| 2 · Caché del score | `docs/arquitectura-backend.md` (el score cacheado vive en RISK), `contracts/` + `contracts/CHANGELOG.md` (`nivelRiesgo`, `fechaVigencia`), `docs/sprint-1.md` (estado de CA-W01-04 y EC010) |
| 3 · Cifrado | `docs/sprint-1.md` y `CLAUDE.md` (D-04 cerrada; quitar "cifrado de campo fuera de alcance en risk"), `infra/README.md` (KMS, CMEK de Cloud SQL y Memorystore, Atlas M0 sin CMEK), README de `auth`, `risk` y `rating` (cifrado de sobre) |
| 4 · Consentimiento | `contracts/` + CHANGELOG, `docs/autenticacion-usuarios.md`, `docs/sprint-1.md` (CA-W01-08) |
| 5 · Fuentes | README del `acl-worker`, `docs/arquitectura-backend.md` |
| 6 · Seguridad en CI | `docs/convenciones.md` (escaneos informativos y cuándo pasan a obligatorios), `infra/README.md` (secretos de Sonar) |
| 7 · Front | `docs/convenciones.md` y README del repo del front |
| 10 · Gateway | `docs/sprint-1.md` (LAT-004 sale de diferidas), `docs/arquitectura-backend.md` (el gateway es la puerta pública), `docs/autenticacion-usuarios.md` (sección 8: qué falta para el JWT en el gateway), `infra/README.md` (`crear_gateway`, `gateway_url`), `observabilidad/README.md`; en el front: `README.md`, `.env.example` y `CLAUDE.md` |
| 9 · Batch | `docs/sprint-1.md` (ESC-004 sale de diferidas), `infra/README.md` (bandera `crear_batch`, Scheduler y Job), `docs/arquitectura-backend.md`, `docs/evidencias/EC017-<fecha>.md` |
| 8 · Diseño | `contracts/` + CHANGELOG (evento `PerfilActualizado`), `infra/README.md` (bandera `crear_pubsub`), `docs/arquitectura-backend.md` (Pub/Sub solo para ese evento; la oferta se guarda en W03), `docs/evidencias/exploratoria-FC-01.md` |
| Al terminar todo | `CLAUDE.md` → sección **Estado** con la fecha nueva y lo que quedó hecho |

## Lo que hace felipe a mano

Todo lo demás (código, Terraform, pipelines, pruebas y evidencias) lo hago yo en PR hacia `develop`. Estos pasos
necesitan tus permisos o tu máquina, y van en este orden:

| # | Cuándo | Qué haces | Dónde |
|---|---|---|---|
| 1 | Después de que el PR del bloque 1 llegue a `main` y el pipeline aplique el Terraform (crea `grafana-lector`) | Crear la llave de Grafana: `gcloud iam service-accounts keys create ~/Downloads/grafana-lector-key.json --iam-account=grafana-lector@solventa-511815.iam.gserviceaccount.com` | Tu terminal, con `gcloud` logueado en `solventa-511815` |
| 2 | Justo después del paso 1 | Copiarla al repo (git la ignora): `cp ~/Downloads/grafana-lector-key.json observabilidad/grafana/grafana-lector-key.json` | Carpeta `solventa-backend` |
| 3 | Si el paso 1 falla por `iam.disableServiceAccountKeyCreation` | Pedir a un admin de la organización de GCP que desactive esa política solo para `solventa-511815`, y repetir el paso 1 | Consola de GCP → IAM → Políticas de la organización |
| 4 | Para ver los tableros | `docker compose --profile observabilidad up -d` y abrir http://localhost:3100 | Carpeta `solventa-backend` |
| 5 | Para activar Sonar (bloque 6) | Crear los proyectos `solventa-backend` y `solventa-frontend` en SonarCloud y cargar los secretos `SONAR_TOKEN` y `SONAR_HOST_URL=https://sonarcloud.io` en cada repo (lo hace un admin de `solventa-app`) | sonarcloud.io y GitHub → Settings → Secrets and variables → Actions |
| 6 | Bloque 3 | Aprobar y hacer el merge a `main` del PR que pone `crear_kms = true` (el key ring de KMS no se puede borrar nunca) | GitHub |
| 7 | Bloque 3, Cloud SQL con CMEK | Acordar con el equipo una ventana en que staging puede quedar sin AUTH y PAYMENTS mientras se pasa a la instancia nueva | Con el equipo |
| 8 | Bloques 1 y 7 | Correr k6 y los E2E contra staging cuando te pase el comando (o lanzar el job manual de GitHub) | Tu terminal o GitHub → Actions |

| 9 | Bloque 8, cuando W01 esté completa en staging | Hacer la **sesión exploratoria de FC-01** (unos 60 minutos usando la app web contra staging como lo haría un cliente, probando casos raros) y llenar la plantilla `docs/evidencias/exploratoria-FC-01.md` con lo encontrado. Puede hacerla otra persona del equipo; mejor alguien que no programó W01 | Navegador y el repo |
| 10 | Bloque 8 | Aprobar y hacer el merge a `main` del PR que activa Pub/Sub (`crear_pubsub = true`) | GitHub |

| 11 | Bloque 10, después del merge a `main` que crea el gateway | Revisar en el informe del despliegue la `gateway_url` y aprobar el PR del front que cambia el rewrite de Vercel a esa URL | GitHub |

Nunca: subir la llave JSON al repo, pegarla en el chat ni compartirla por Slack. Si se filtra, se borra en
GCP (`gcloud iam service-accounts keys delete`) y se crea otra.

## Para el Sprint 2 (no se hace ahora)

Queda escrito para que nadie lo construya antes de tiempo ni lo olvide:

- **ESC-001**: depende del API Gateway, que ya quedará montado en el Sprint 1.
- **Guardar la oferta aceptada** (con HU-W03): precio, plazo y si fue completa o preliminar; ahí se decide el escritor.
- **JWT en el gateway**: AUTH firma con ES256 y publica JWKS; el gateway valida (`x-google-issuer`, `x-google-jwks_uri`)
  y el BFF deja de verificar (`docs/autenticacion-usuarios.md`, sección 8).
- **Límites por IP o por socio en el gateway** (cuotas): con HU-W07 (socios B2B).
- **D-03**: cobro antes o después de emitir, con HU-W03.

## Decisiones que necesito del equipo

1. ¿Usamos **SonarCloud** (gratis, en la nube de Sonar) o tienen un **SonarQube** propio?
2. ¿El bloque 4 (consentimiento ligado al titular) entra en este sprint? Cambia el contrato y el front.
