// EC005 (HU-W03, CA-W03-09): decisión + emisión de la póliza con p95 <= 1,5 s en carga normal, medido de la
// firma (`firmarPropuesta`) a que `propuesta(id)` diga EMITIDA. Recorrido completo por el BFF en
// `lib/emision.js`.
//
// Todo lo que reporta es MEDIDO por k6. La carga NO viene del enunciado de EC005 (el repo no la trae): el ritmo
// por defecto, 1 firma por segundo, es un SUPUESTO que se declara en el informe; se cambia con RITMO_EMISION.
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> tests/k6/ec005-emision.js
//   RITMO_EMISION=2 DURACION_NORMAL=3m ./scripts/correr_k6.sh tests/k6/ec005-emision.js

import { Counter, Rate, Trend } from "k6/metrics";
import { documentoDePrueba, prepararClientes, recorrerEmision } from "./lib/emision.js";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_NORMAL = __ENV.DURACION_NORMAL || (MODO_REDUCIDO ? "5m" : "30m");
const RITMO_EMISION = Number(__ENV.RITMO_EMISION || 1); // firmas por segundo (supuesto)
const CLIENTES_PRUEBA = Number(__ENV.CLIENTES_PRUEBA || 20);
const ESTADOS = ["ACEPTADA", "EN_EMISION", "EN_REVISION", "PENDIENTE_PAGO", "PAGO_RECHAZADO", "NO_EMITIDA", "EMITIDA", "FIRMA_RECHAZADA"];

const emisionMs = new Trend("emision_ms", true);
const emisionesOk = new Rate("emisiones_ok");
const errores5xx = new Rate("errores_5xx");
const recorridosSinFirma = new Counter("recorridos_sin_firma");
const estadoFinal = Object.fromEntries(ESTADOS.map((e) => [e, new Counter(`estado_final_${e.toLowerCase()}`)]));

export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec005_emision_normal: {
      executor: "constant-arrival-rate",
      rate: RITMO_EMISION,
      timeUnit: "1s",
      duration: DURACION_NORMAL,
      preAllocatedVUs: 20,
      maxVUs: 60,
      exec: "emitir",
    },
  },
  thresholds: {
    emision_ms: ["p(95)<=1500"],
    // Una emisión que no llega a EMITIDA entra a `emision_ms` con el tiempo que se esperó, así no se esconde.
    emisiones_ok: ["rate>=0.99"],
    errores_5xx: ["rate==0"],
    // Un recorrido que se cae antes de firmar no tiene muestra de emisión: que no pase en silencio.
    recorridos_sin_firma: ["count==0"],
  },
};

export function setup() {
  return { clientes: prepararClientes(GRAPHQL_URL, CLIENTES_PRUEBA), marca: Date.now() };
}

export function emitir(datos) {
  const cliente = datos.clientes[(__VU - 1) % datos.clientes.length];
  // Un documento nuevo por recorrido: cada uno necesita su propio consentimiento y su propia oferta.
  const documento = documentoDePrueba("E5", datos.marca);
  const { previa, emision } = recorrerEmision(GRAPHQL_URL, cliente, documento);
  errores5xx.add(previa.cinco_xx || (emision ? emision.cinco_xx : false));
  if (!emision) {
    recorridosSinFirma.add(1);
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
    escenario: "EC005",
    url: BASE_URL,
    modo_reducido: MODO_REDUCIDO,
    duracion: DURACION_NORMAL,
    ritmo_firmas_por_segundo_supuesto: RITMO_EMISION,
    clientes_de_prueba: CLIENTES_PRUEBA,
    emisiones_medidas: valor("emision_ms", "count"),
    p50_ms: valor("emision_ms", "med"),
    p95_ms: valor("emision_ms", "p(95)"),
    p99_ms: valor("emision_ms", "p(99)"),
    max_ms: valor("emision_ms", "max"),
    tasa_emitidas: valor("emisiones_ok", "rate"),
    tasa_5xx: valor("errores_5xx", "rate"),
    recorridos_sin_firma: valor("recorridos_sin_firma", "count"),
    estados_finales: Object.fromEntries(ESTADOS.map((e) => [e, valor(`estado_final_${e.toLowerCase()}`, "count") || 0])),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/ec005-emision.json"]: JSON.stringify(data, null, 2),
  };
}
