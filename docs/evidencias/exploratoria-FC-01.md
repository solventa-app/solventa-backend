# Evidencia de Prueba Exploratoria FC-01 e Indicadores de Calidad (HU-W01 y HU-W05)

- **Fecha:** 2026-10-18
- **Factor de calidad:** FC-01 (Adecuación funcional, resiliencia ante entradas bordes y experiencia de usuario frente a degradación de proveedores)
- **Historias cubiertas:** HU-W01 (Cotización y perfilamiento de seguro de vida deudor hipotecario, pantallas W01-01, W01-02 y W01-03) y HU-W05 (Conciliación y reintento de cobros en back-office, pantalla W05-01).
- **Entorno:** Staging desplegado en GCP (`us-central1`, proyecto `solventa-511815`) a través del API Gateway (700 ms) y front-end conectado.

---

## 1. Carta de sesión exploratoria (Session-Based Test Management)

| Campo | Detalle |
|---|---|
| **Misión (Charter)** | Explorar el flujo de cotización hipotecaria (HU-W01, como `CLIENTE` y como `ASESOR`) y la mesa de conciliación de pagos (HU-W05, como `BACKOFFICE`) buscando fallos al combinar documentos en valores límite, direcciones con y sin ciudad reconocida, plazos fuera de rango, revocación de consentimiento y caídas parciales o totales de los proveedores externos. |
| **Duración de la sesión (Timebox)** | **60 minutos** (45 min de ejecución activa + 15 min de registro y aislamiento de hallazgos). |
| **Responsable (Tester)** | Integrante del equipo que no implementó el resolutor evaluado (revisión cruzada, decisión 11 de `docs/plan-completar-hu-w01.md`). |
| **Datos de prueba utilizados (ficticios, regla 6)** | - Usuarios: `cliente.demo` (`CLIENTE`), `asesor.demo` (`ASESOR`), `operador.demo` (`BACKOFFICE`)<br>- Documentos: `CC-100001` (6 dígitos, límite inferior válido), `CC-12345` (5 dígitos, inválido), `CC-9999999999` (sin historial en caché)<br>- Valores de crédito: `COP 50.000.000`, `COP 200.000.000`, `COP 0` y negativos<br>- Plazos: `12`, `180`, `240`, `360` meses (válidos) y `0`, `-12`, `361` meses (inválidos)<br>- Direcciones: `"Bogotá D.C., Chapinero"`, `"Medellín, El Poblado"`, `"Cali"`, `"Vereda El Rosal km 4"` (ciudad no catalogada → factor neutro `1.00`) |

---

## 2. Recorridos y hallazgos de la sesión exploratoria

| # | Escenario explorado | Comportamiento observado | Estado |
|---|---|---|---|
| 1 | **Cotización con las 5 fuentes en línea (`COMPLETA`)** | Al otorgar el consentimiento para el documento `CC-100001` y cotizar un crédito de `200.000.000 COP` a `240` meses en Bogotá, se entrega oferta `COMPLETA` con prima puntual, cobertura y las 5 fuentes en estado `OK` en < 300 ms. | ✅ Sin defectos |
| 2 | **Degradación de fuente no obligatoria (`Open Data` en `caido`)** | Con `historial-crediticio` disponible pero `catastro-inmueble` caído, RATING calcula oferta `PRELIMINAR` con `rangoPrima` (`minima`/`maxima`), nunca muestra error 5xx y encola la reconciliación diferida en Redis. | ✅ Sin defectos |
| 3 | **Falla de la única fuente obligatoria (`historial-crediticio`) sin caché (CA-W01-05)** | Para un documento nuevo (`CC-9999999999`) con `stub-open-finance` en `caido`, el sistema devuelve `ErrorOferta` con `motivo: DATOS_INSUFICIENTES` y `puedeReintentar: true`, mostrando el botón de reintentar en lugar de una prima inventada. | ✅ Sin defectos |
| 4 | **Intento de reutilizar el `consentimientoId` con otro documento de identidad** | Si el usuario cambia el documento en el formulario después de registrar el consentimiento, el BFF detecta que el `titular_hash` no coincide y responde `ErrorOferta` con `motivo: CONSENTIMIENTO_NO_VIGENTE`, obligando a renovar el consentimiento. | ✅ Sin defectos |
| 5 | **Valores límite en plazo (`0`, `361` meses) y dirección sin ciudad principal** | Plazos `<= 0` o `> 360` se rechazan en el BFF con `ErrorOferta` (`puedeReintentar: false`) sin invocar servicios aguas abajo. Una dirección rural sin ciudad conocida aplica factor geográfico neutro (`1.00`) sin fallar. | ✅ Sin defectos |
| 6 | **Conciliación de cobros en `W05-01` (RECHAZADO vs. PENDIENTE)** | Un cobro `RECHAZADO` permite hasta 3 reintentos manuales generando referencias `id_intento_cobro-r1..r3` y decrementando `reintentosRestantes`. Un cobro `PENDIENTE` por timeout de la pasarela concilia con la referencia original sin duplicar el cargo. | ✅ Sin defectos |

---

## 3. Tabla de indicadores de calidad del Sprint 1

Resumen consolidado exigido por la estrategia de pruebas (defectos por severidad, casos ejecutados, cobertura y escenarios de calidad EC cumplidos):

| Indicador | Valor | Meta del Sprint 1 | Estado |
|---|---|---|---|
| **Defectos críticos (Blocker / Critical) abiertos** | **0** | 0 | ✅ Cumple |
| **Defectos mayores (Major) abiertos** | **0** (3 detectados durante desarrollo y cerrados antes del corte: EC010 > 500 ms sin caché competitiva, titular en consentimiento y cabecera `X-Forwarded-Authorization` tras el gateway) | 0 abiertos al cierre | ✅ Cumple |
| **Defectos menores / cosméticos (Minor) abiertos** | **0** | ≤ 2 | ✅ Cumple |
| **Casos de prueba automatizados ejecutados (pytest + humo E2E + validación de contratos y gateway)** | **100 % en verde** (7 servicios + 3 stubs + contratos GraphQL/JSON Schema + `humo_bff.py` + `validar_servicios.py` + `validar_gateway.py`) | 100 % de aprobación | ✅ Cumple |
| **Cobertura de criterios de aceptación de HU-W01 (CA-W01-01 a CA-W01-11) y HU-W05** | **100 %** | 100 % | ✅ Cumple |
| **Escenarios de calidad (EC) verificados con evidencia** | **EC001, EC002, EC003, EC004, EC008, EC009, EC010, EC011, EC012, EC017, EC022, EC023 (CA-W01-08), EC031, EC033** | Todos los asignados al Sprint 1 (incluido el adelanto de HA-ESC-004 y HA-LAT-004) | ✅ Cumple |
