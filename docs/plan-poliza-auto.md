# Plan · Póliza de auto (todo riesgo)

> **Estado: aprobado por Frans (2026-11-10). Historia KAN-290 (HU-W08).** Tarea 1 (contrato v0.42) en curso.
> Cubre backend, web y API de socios; el móvil solo reporta y pide asistencia sobre la póliza.
> Sin Figma: las pantallas nuevas se arman con el sistema de diseño, como G-06 y M05.

## 1. Por qué

Hoy Solventa vende un solo producto: el seguro de **vida deudor hipotecario** (`RAMO_POLIZA`, una constante del BFF). Por eso
los recorridos de siniestros no tienen una póliza con sentido detrás: el reporte con fotos de un choque (HU-M05), la asistencia
con grúa o mecánico (HU-M06) y la mesa del operador (HU-W04) se prueban sobre una póliza de vida. Con la póliza de auto, el
recorrido completo cuadra: **cotizar → emitir → chocar → reportar → pedir grúa → aprobar → indemnizar**.

## 2. Decisiones

| # | Decisión | Estado |
|---|---|---|
| A-1 | **Coberturas «todo riesgo» estándar:** responsabilidad civil extracontractual (RCE), pérdida total por daños, pérdida parcial por daños, pérdida total por hurto, pérdida parcial por hurto y asistencia (grúa, mecánico, conductor elegido). Con deducible | Cerrada |
| A-2 | **Canales:** la web (asesor y cliente) y la API de socios (B2B2C). El móvil no la vende: reporta siniestros y pide asistencia sobre ella | Cerrada |
| A-3 | **Indemnización:** en pérdida total (daños o hurto), valor asegurado menos deducible, automática. En pérdida parcial, el operador digita el valor del daño (con tope del valor asegurado) y se resta el deducible | Cerrada |
| A-4 | **Proveedores simulados:** un solo `stub-vehiculos` con tres rutas detrás del ACL Worker: RUNT (datos y kilometraje de la revisión técnico-mecánica), guía Fasecolda (valor comercial e índice de hurto de la referencia) y siniestralidad por placa | Cerrada |
| A-5 | **El producto es un dato, no un servicio nuevo:** `producto` = `VIDA_DEUDOR` (lo de hoy, por defecto) o `AUTO`. POLICY, RATING, CLAIMS y el BFF lo llevan; `RAMO_POLIZA` deja de ser una constante | Propuesta |
| A-6 | **El riesgo del vehículo lo escribe RISK** (escritor único del riesgo), como el perfil financiero: lee las fuentes del vehículo por el ACL y guarda un perfil vehicular por placa (placa cifrada; la llave es un HMAC). RATING lo lee de la secundaria y tarifa | Propuesta |
| A-7 | **Pago y vigencia:** igual que hoy: firma, primera cuota con la pasarela y cobro mensual recurrente. Vigencia de 12 meses | Propuesta |
| A-8 | **Consentimiento antes de consultar** (regla 7): el mismo `registrarConsentimiento`, con finalidad de cotizar el seguro del vehículo. Las fuentes del vehículo se registran en `contracts/fuentes.json` como `OPEN_DATA`, sin enum nuevo | Propuesta |

## 3. Tarifa

Prima anual de daños y hurto sobre el **valor comercial** de la guía Fasecolda, más una prima fija de RCE por clase de
vehículo. La prima mensual es la anual dividida en 12. Todos los valores viven en `contracts/tarifas-auto.json` (tarifa por
configuración, como `fuentes.json`) y son ilustrativos.

```
prima_anual = valor_comercial × tasa_base(clase, uso)
              × f_antiguedad × f_kilometraje × f_hurto × f_siniestralidad × f_ciudad × f_deducible
              + prima_rce(clase)
```

| Factor | Fuente | Valores propuestos |
|---|---|---|
| Tasa base | RUNT (clase y servicio) | Automóvil particular 3,2 %; camioneta 3,6 %; uso comercial × 1,15 |
| Antigüedad | RUNT (modelo) | 0-2 años 0,95; 3-5 años 1,00; 6-10 años 1,10; 11-15 años 1,25; más de 15: solo RCE |
| Kilometraje anual | Declarado por el cliente y contrastado con el odómetro de la última revisión técnico-mecánica (RUNT) | < 10.000 km 0,90; 10-20 mil 1,00; 20-30 mil 1,10; > 30 mil 1,20 |
| Índice de hurto de la referencia | Fasecolda | Bajo 0,95; medio 1,00; alto 1,20; muy alto 1,40 |
| Siniestros de los últimos 3 años | Siniestralidad por placa | 0: 0,90 (bonificación); 1: 1,10; 2: 1,30; 3 o más: **en revisión** (UNDER) |
| Ciudad de circulación | Formulario | Bogotá, Medellín y Cali 1,10; resto 1,00 |
| Deducible | Formulario | 10 % (mínimo 1 SMMLV) 1,00; sin deducible 1,25 |

**Degradación (regla 4):** si cae RUNT o Fasecolda no hay valor comercial y la oferta es **preliminar** con rango ±20 % sobre
el valor que declare el cliente; si cae la siniestralidad, se tarifa con factor 1,00 y la oferta es preliminar. Nunca un 5xx.
**Rechazos:** vehículo con más de 15 años (solo RCE), placa con limitación o hurto vigente en RUNT, o kilometraje declarado
menor que el del odómetro (se avisa y se usa el del odómetro).

## 4. Contrato (aditivo, v0.42)

- `enum Producto { VIDA_DEUDOR AUTO }`.
- `SolicitudOfertaInput.producto: Producto = VIDA_DEUDOR` y `vehiculo: VehiculoInput` (placa, kilometraje anual, ciudad de
  circulación, uso, deducible). Para `AUTO` el valor del crédito y la dirección del inmueble no aplican.
- `Oferta.producto`, `Oferta.vehiculo` (marca, línea, modelo, valor comercial; la placa enmascarada) y
  `Oferta.coberturas: [Cobertura!]` (nombre, valor asegurado, deducible).
- `Poliza.ramo` sale del producto; `Poliza.vehiculo` y `Poliza.coberturas`.
- Siniestros: `ReporteSiniestroInput.tipo: TipoSiniestroAuto` (choque, pérdida total, hurto total, hurto parcial,
  responsabilidad civil); `aprobarSiniestro` admite `valorDano` en pérdida parcial (A-3).
- API de socios (`socios-v1.json`): `producto` y `vehiculo` en la cotización. Modo prueba con respuestas fijas de auto.
- Eventos: `poliza.emitida` lleva `producto`.

## 5. Tareas

| # | Tarea | Repos | Tamaño |
|---|---|---|---|
| 1 | **Contrato:** lo de la sección 4, más `tarifas-auto.json` y las fuentes del vehículo en `fuentes.json` | backend | M |
| 2 | **`stub-vehiculos`:** RUNT, Fasecolda y siniestralidad con placas de demo que cubren cada caso (nuevo sin siniestros, viejo, hurto alto, 3 siniestros, placa con limitación y una que falla). Servicio en `servicios.json`, compose y Terraform | backend, infra | M |
| 3 | **ACL Worker:** adaptador del vehículo (puerto y adaptador, Circuit Breaker, 700 ms, caché) | backend | M |
| 4 | **RISK:** perfil vehicular por placa (cifrado, migración idempotente) | backend | M |
| 5 | **RATING:** tarificador de auto con `tarifas-auto.json`, oferta completa o preliminar | backend | M |
| 6 | **UNDER y POLICY:** reglas de rechazo y revisión; propuesta y póliza con producto, vehículo y coberturas (migración de POLICY); PDF de la póliza de auto | backend | L |
| 7 | **BFF:** producto en cotizar, aceptar y emitir; `ramo` por producto; API de socios | backend | M |
| 8 | **CLAIMS y PAYMENTS:** tipo de siniestro de auto, indemnización por tipo con deducible y valor del daño en parcial | backend | M |
| 9 | **Web:** selector de producto en W01-01, formulario del vehículo, oferta con coberturas, G-06 y su detalle con el vehículo, y valor del daño en la aprobación de la mesa | web | L |
| 10 | **Móvil:** M05 con el tipo de siniestro de auto y la póliza de auto en la lista | móvil | S |
| 11 | **Semilla y punta a punta:** póliza de auto de demo para `cliente.siniestros`; Newman de cotizar a indemnizar; Cypress del asesor y del cliente; prueba de integración del móvil | todos | M |

Orden: 1 → 2 → 3 → 4 → 5 → (6, 7) → 8 → (9, 10) → 11. La venta funciona al terminar la 7; el recorrido de siniestros, al
terminar la 8.

## 6. Riesgos

- **Datos personales:** la placa identifica al propietario. Se guarda cifrada y en logs solo su HMAC (regla 6).
- **Atlas M0:** el perfil vehicular suma escrituras en RISK; es poca carga por cotización.
- **Costo:** un Cloud Run más (`stub-vehiculos`), con mínimo 0 instancias.
- **Alcance:** es el cambio más grande desde HU-W03. Si el tiempo aprieta, se corta primero la API de socios (A-2) y después
  la pérdida parcial por hurto.
