// HA-LAT-004 · API Gateway y presupuesto por dependencia:
//   - EC001 / EC002: p99 <= 500 ms extremo a extremo A TRAVÉS DEL GATEWAY (deadline duro de 700 ms).
//   - EC008: <= 120 ms por dependencia (medido contra el ACL Worker, que es quien llama al proveedor) y
//     corte duro de 700 ms cuando el proveedor se degrada.
//
// Todo lo que reporta es MEDIDO por k6:
//   - `latencia_gateway_e2e_ms`: la duración real de `solicitarOferta` por el gateway.
//   - `latencia_dependencia_acl_ms`: la duración real de `POST /fuentes/consultar` con
//     `forzar_proveedor=true` en el ACL (una llamada en vivo al proveedor, sin caché). Requiere acceso al
//     ACL (`ACL_URL`, privado en staging: `ACL_TOKEN`); sin él, esa parte NO se mide y se dice.
//   - `latencia_corte_700_ms`: con el stub en `lento` y la caché caliente, lo que tarda el gateway.
//
// Fases (modo reducido): 10 min normal + 2 min con el stub en `lento`. Acotables con DURACION_NORMAL y
// DURACION_CORTE (p. ej. "3m" y "1m").
//
// Uso:
//   k6 run -e GATEWAY_URL=https://<gateway-url> [-e ACL_URL=...] tests/k6/gateway-presupuesto.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { esOferta, pedirOferta, prepararSesion } from "./lib/preparar.js";

const GATEWAY_URL = (__ENV.GATEWAY_URL || __ENV.STAGING_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${GATEWAY_URL}/graphql/web`;
const STUB_URL = (__ENV.STUB_OPEN_FINANCE_URL || "http://localhost:8101").replace(/\/$/, "");
const ACL_URL = (__ENV.ACL_URL || "").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_NORMAL = __ENV.DURACION_NORMAL || (MODO_REDUCIDO ? "10m" : "30m");
const DURACION_CORTE = __ENV.DURACION_CORTE || "2m";
const FUENTES_OPEN_FINANCE = ["cuentas-bancarias", "historial-crediticio"];

const latenciaExtremoAExtremo = new Trend("latencia_gateway_e2e_ms", true);
const latenciaPorDependencia = new Trend("latencia_dependencia_acl_ms", true);
const latenciaConCorte700 = new Trend("latencia_corte_700_ms", true);
const errores5xx = new Rate("errores_5xx");
const respuestasDentroDe700ms = new Rate("respuestas_dentro_de_700ms");
const peticionesExitosas = new Counter("peticiones_exitosas");

const escenarios = {
  ec001_ec002_normal: {
    executor: "constant-arrival-rate",
    rate: 56,
    timeUnit: "10s", // 5,6 req/s
    duration: DURACION_NORMAL,
    preAllocatedVUs: 10,
    maxVUs: 30,
    exec: "escenarioGatewayNormal",
  },
  activar_proveedor_lento: {
    executor: "per-vu-iterations",
    vus: 1,
    iterations: 1,
    startTime: DURACION_NORMAL,
    maxDuration: "30s",
    exec: "activarProveedorLento",
  },
  ec008_corte_duro_700ms: {
    executor: "constant-arrival-rate",
    startTime: `${parseFloat(DURACION_NORMAL) * (DURACION_NORMAL.endsWith("m") ? 60 : 1) + 5}s`,
    rate: 20,
    timeUnit: "10s",
    duration: DURACION_CORTE,
    preAllocatedVUs: 8,
    maxVUs: 20,
    exec: "escenarioCorteDuro",
  },
};
if (ACL_URL) {
  escenarios.ec008_dependencia_acl = {
    executor: "constant-arrival-rate",
    rate: 56,
    timeUnit: "10s",
    duration: DURACION_NORMAL,
    preAllocatedVUs: 5,
    maxVUs: 15,
    exec: "escenarioDependencia",
  };
}

const umbrales = {
  latencia_gateway_e2e_ms: ["p(99)<=500"],
  latencia_corte_700_ms: ["p(99)<=700"],
  respuestas_dentro_de_700ms: ["rate==1"],
  errores_5xx: ["rate==0"],
};
if (ACL_URL) {
  umbrales.latencia_dependencia_acl_ms = ["p(95)<=120"];
}

// El resumen incluye p(99) y la cantidad de muestras: sin ellas no se puede decir cuánto se midió.
export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: escenarios,
  thresholds: umbrales,
};

function cambiarModoStub(modo) {
  if (__ENV.OMITIR_CONTROL_STUB === "true") {
    return;
  }
  http.post(`${STUB_URL}/control/modo`, JSON.stringify({ modo }), {
    headers: { "Content-Type": "application/json" },
    timeout: "3s",
  });
}

export function setup() {
  cambiarModoStub("sano");
  const salud = http.get(`${GATEWAY_URL}/health`, { timeout: "2s" });
  if (salud.status !== 200) {
    throw new Error(`El gateway no responde /health (${salud.status})`);
  }
  const sesion = prepararSesion(GRAPHQL_URL, __ENV.DOCUMENTO_PRUEBA || "CC-K6-GW-100004");
  // Calentar la caché de fuentes y del score para medir el camino normal y el degradado con caché.
  pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "220000000" });
  return sesion;
}

export function escenarioGatewayNormal(sesion) {
  const { res, oferta } = pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "220000000" }, { timeout: "2s" });
  latenciaExtremoAExtremo.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  respuestasDentroDe700ms.add(res.timings.duration <= 700);
  const ok = check(res, {
    "EC001/EC002 HTTP 200 por el gateway": (r) => r.status === 200,
    "EC001/EC002 respuesta es una oferta": () => esOferta(oferta),
  });
  if (ok) {
    peticionesExitosas.add(1);
  }
  sleep(0.05);
}

export function activarProveedorLento() {
  cambiarModoStub("lento");
}

export function escenarioCorteDuro(sesion) {
  const { res } = pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "220000000" }, { timeout: "2s" });
  latenciaConCorte700.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  respuestasDentroDe700ms.add(res.timings.duration <= 700);
  check(res, {
    "EC008 respuesta dentro del deadline duro de 700 ms": (r) => r.timings.duration <= 700,
    "EC008 sin 5xx crudo al degradar": (r) => r.status === 200,
  });
  sleep(0.05);
}

export function escenarioDependencia() {
  const fuente = FUENTES_OPEN_FINANCE[Math.floor(Math.random() * FUENTES_OPEN_FINANCE.length)];
  const res = http.post(
    `${ACL_URL}/fuentes/consultar`,
    JSON.stringify({
      cliente_id: "cli-k6-ec008",
      consentimiento_id: "consentimiento-k6-ec008",
      fuente,
      forzar_proveedor: true,
    }),
    {
      headers: {
        "Content-Type": "application/json",
        ...(__ENV.ACL_TOKEN ? { Authorization: `Bearer ${__ENV.ACL_TOKEN}` } : {}),
      },
    }
  );
  if (res.status === 200) {
    let cuerpo = null;
    try {
      cuerpo = JSON.parse(res.body);
    } catch (_) {
      cuerpo = null;
    }
    // Solo cuenta la llamada que de verdad llegó al proveedor (no la degradada ni la de caché).
    if (cuerpo && !cuerpo.de_cache && !cuerpo.degradado) {
      latenciaPorDependencia.add(res.timings.duration);
    }
  }
  sleep(0.05);
}

export function teardown() {
  cambiarModoStub("sano");
}

export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, estadistico) => (m[nombre] ? m[nombre].values[estadistico] : null);
  const resumen = {
    escenario: "EC001-EC002-EC008",
    gateway_url: GATEWAY_URL,
    duracion_normal: DURACION_NORMAL,
    duracion_corte: DURACION_CORTE,
    peticiones_e2e: valor("latencia_gateway_e2e_ms", "count"),
    p50_e2e_ms: valor("latencia_gateway_e2e_ms", "med"),
    p95_e2e_ms: valor("latencia_gateway_e2e_ms", "p(95)"),
    p99_e2e_ms: valor("latencia_gateway_e2e_ms", "p(99)"),
    dependencia_acl_medida: Boolean(ACL_URL),
    llamadas_dependencia: valor("latencia_dependencia_acl_ms", "count"),
    p95_dependencia_acl_ms: valor("latencia_dependencia_acl_ms", "p(95)"),
    peticiones_corte: valor("latencia_corte_700_ms", "count"),
    p99_corte_700_ms: valor("latencia_corte_700_ms", "p(99)"),
    max_corte_700_ms: valor("latencia_corte_700_ms", "max"),
    tasa_dentro_de_700ms: valor("respuestas_dentro_de_700ms", "rate"),
    tasa_5xx: valor("errores_5xx", "rate"),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/gateway-presupuesto.json"]: JSON.stringify(data, null, 2),
  };
}
