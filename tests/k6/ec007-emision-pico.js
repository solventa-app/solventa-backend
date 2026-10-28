// EC007 (HU-W03, CA-W03-07): decisión + emisión con p99 <= 3 s en pico, medido desde el regreso de la pasarela
// (`confirmarPago`) hasta la póliza EMITIDA.
// Mismo recorrido que EC005 (`lib/emision.js`); aquí el ritmo de recorridos sube a 3x del normal, se sostiene y baja.
//
// Todo lo que reporta es MEDIDO por k6. Ni el ritmo normal (1 recorrido por segundo) ni el factor 3x vienen del
// enunciado de EC007: son SUPUESTOS que se declaran en el informe (RITMO_EMISION, FACTOR_PICO).
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> tests/k6/ec007-emision-pico.js
//   RITMO_EMISION=1 FACTOR_PICO=3 DURACION_PICO=2m ./scripts/correr_k6.sh tests/k6/ec007-emision-pico.js

import { Counter, Rate, Trend } from "k6/metrics";
import { prepararClientes, recorrerEmision } from "./lib/emision.js";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_PICO = __ENV.DURACION_PICO || (MODO_REDUCIDO ? "2m" : "10m");
const RITMO_EMISION = Number(__ENV.RITMO_EMISION || 1); // recorridos (pagos) por segundo en operación normal (supuesto)
const FACTOR_PICO = Number(__ENV.FACTOR_PICO || 3); // pico = FACTOR_PICO x normal (supuesto)
const CLIENTES_PRUEBA = Number(__ENV.CLIENTES_PRUEBA || 30);
const ESTADOS = ["EMITIDA", "PASARELA_NO_DISPONIBLE", "REQUIERE_OTRA_TARJETA", "PAGO_RECHAZADO", "SESION_VENCIDA", "SIN_RESPUESTA"];

const emisionMs = new Trend("emision_pico_ms", true);
const emisionesOk = new Rate("emisiones_ok");
const errores5xx = new Rate("errores_5xx");
const recorridosSinPago = new Counter("recorridos_sin_pago");
const estadoFinal = Object.fromEntries(ESTADOS.map((e) => [e, new Counter(`estado_final_${e.toLowerCase()}`)]));

export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec007_emision_pico: {
      executor: "ramping-arrival-rate",
      startRate: RITMO_EMISION,
      timeUnit: "1s",
      preAllocatedVUs: 40,
      maxVUs: 150,
      stages: [
        { target: RITMO_EMISION * FACTOR_PICO, duration: "30s" },
        { target: RITMO_EMISION * FACTOR_PICO, duration: DURACION_PICO },
        { target: RITMO_EMISION, duration: "30s" },
      ],
      exec: "emitir",
    },
  },
  thresholds: {
    emision_pico_ms: ["p(99)<=3000"],
    // Una emisión que no llega a EMITIDA entra a `emision_pico_ms` con el tiempo que se esperó, así no se esconde.
    emisiones_ok: ["rate>=0.99"],
    errores_5xx: ["rate==0"],
    // Un recorrido que se cae antes de pagar no tiene muestra de emisión: que no pase en silencio.
    recorridos_sin_pago: ["count==0"],
  },
};

export function setup() {
  const marca = Date.now();
  return { clientes: prepararClientes(GRAPHQL_URL, CLIENTES_PRUEBA, marca), marca };
}

export function emitir(datos) {
  const cliente = datos.clientes[(__VU - 1) % datos.clientes.length];
  const { previa, emision } = recorrerEmision(GRAPHQL_URL, cliente);
  errores5xx.add(previa.cinco_xx || (emision ? emision.cinco_xx : false));
  if (!emision) {
    recorridosSinPago.add(1);
    return;
  }
  emisionMs.add(emision.ms);
  emisionesOk.add(emision.ok);
  if (estadoFinal[emision.estado]) {
    estadoFinal[emision.estado].add(1);
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, estadistico) => (m[nombre] ? m[nombre].values[estadistico] : null);
  const resumen = {
    escenario: "EC007",
    url: BASE_URL,
    modo_reducido: MODO_REDUCIDO,
    duracion_pico: DURACION_PICO,
    ritmo_normal_firmas_por_segundo_supuesto: RITMO_EMISION,
    factor_pico_supuesto: FACTOR_PICO,
    clientes_de_prueba: CLIENTES_PRUEBA,
    emisiones_medidas: valor("emision_pico_ms", "count"),
    p50_ms: valor("emision_pico_ms", "med"),
    p95_ms: valor("emision_pico_ms", "p(95)"),
    p99_ms: valor("emision_pico_ms", "p(99)"),
    max_ms: valor("emision_pico_ms", "max"),
    tasa_emitidas: valor("emisiones_ok", "rate"),
    tasa_5xx: valor("errores_5xx", "rate"),
    recorridos_sin_pago: valor("recorridos_sin_pago", "count"),
    estados_finales: Object.fromEntries(ESTADOS.map((e) => [e, valor(`estado_final_${e.toLowerCase()}`, "count") || 0])),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/ec007-emision-pico.json"]: JSON.stringify(data, null, 2),
  };
}
