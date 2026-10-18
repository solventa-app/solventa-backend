// EC009 (100 % de las llamadas que superan 700 ms se cortan, 0 bloqueadas) y
// EC010 (journey degradado <= 500 ms p99 con caché caliente y 0 % de errores 5xx).
//
// Todo lo que reporta es MEDIDO por k6. Las fases se encadenan con escenarios que cambian el modo
// del stub de Open Finance:
//   1. EC009: stub en `lento` (750–1100 ms): ningún journey puede quedar esperando al proveedor.
//   2. transición: stub en `sano`, se espera a que el circuito cierre, se recalienta la caché y se
//      pasa a `caido` (la caché del paso 1 pudo vencer).
//   3. EC010: stub en `caido` con caché caliente.
// Duración de cada fase: 5 min en modo reducido (DURACION_FASE para acotarla, p. ej. "2m").
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> -e STUB_OPEN_FINANCE_URL=https://<stub> tests/k6/falla-proveedor.js
// Si el stub es privado (staging), el operador cambia el modo a mano y se usa OMITIR_CONTROL_STUB=true.

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { esOferta, pedirOferta, prepararSesion } from "./lib/preparar.js";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const STUB_URL = (__ENV.STUB_OPEN_FINANCE_URL || "http://localhost:8101").replace(/\/$/, "");
const ACL_URL = (__ENV.ACL_URL || "").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_FASE = __ENV.DURACION_FASE || (MODO_REDUCIDO ? "5m" : "15m");
const SEGUNDOS_TRANSICION = 45;

const latenciaCorte = new Trend("latencia_corte_ec009_ms", true);
const latenciaDegradadoCache = new Trend("latencia_degradado_ec010_ms", true);
const errores5xx = new Rate("errores_5xx");
const llamadasNoCortadas = new Rate("llamadas_mayores_700ms_sin_corte");
const degradadasSinDato = new Rate("degradadas_sin_dato_en_cache");
const ofertasPreliminares = new Counter("ofertas_preliminares");

function aSegundos(duracion) {
  const n = parseFloat(duracion);
  return duracion.endsWith("m") ? n * 60 : n;
}

// El resumen incluye p(99) y la cantidad de muestras: sin ellas no se puede decir cuánto se midió.
export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec009_proveedor_lento: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "10s", // 2 req/s
      duration: DURACION_FASE,
      preAllocatedVUs: 8,
      maxVUs: 20,
      exec: "escenarioProveedorLento",
    },
    transicion: {
      executor: "per-vu-iterations",
      vus: 1,
      iterations: 1,
      startTime: DURACION_FASE,
      maxDuration: "120s",
      exec: "transicion",
    },
    ec010_proveedor_caido_con_cache: {
      executor: "constant-arrival-rate",
      startTime: `${aSegundos(DURACION_FASE) + SEGUNDOS_TRANSICION + 20}s`,
      rate: 20,
      timeUnit: "10s",
      duration: DURACION_FASE,
      preAllocatedVUs: 8,
      maxVUs: 20,
      exec: "escenarioProveedorCaidoConCache",
    },
  },
  thresholds: {
    errores_5xx: ["rate==0"],
    llamadas_mayores_700ms_sin_corte: ["rate==0"],
    latencia_degradado_ec010_ms: ["p(99)<=500"],
  },
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

function circuitosCerrados() {
  if (!ACL_URL) {
    return true; // sin acceso al ACL (staging) solo se puede esperar un tiempo fijo
  }
  const res = http.get(`${ACL_URL}/circuitos`, { timeout: "3s" });
  try {
    return Object.values(JSON.parse(res.body)).every((estado) => estado === "closed");
  } catch (_) {
    return false;
  }
}

export function setup() {
  cambiarModoStub("sano");
  const sesion = prepararSesion(GRAPHQL_URL, __ENV.DOCUMENTO_PRUEBA || "CC-K6-FALLA-100002");
  // Sembrar la caché en caliente con proveedores sanos antes de inyectar la falla.
  pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "180000000", plazoMeses: 180 });
  cambiarModoStub("lento");
  return sesion;
}

export function escenarioProveedorLento(sesion) {
  const { res } = pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "180000000", plazoMeses: 180 });
  latenciaCorte.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  // El stub `lento` tarda 750–1100 ms: si la respuesta llega después de ~950 ms, nadie cortó.
  llamadasNoCortadas.add(res.timings.duration > 950);
  check(res, {
    "EC009 HTTP 200 (0 % de 5xx)": (r) => r.status === 200,
    "EC009 cortada antes del retraso del proveedor": (r) => r.timings.duration <= 950,
  });
  sleep(0.05);
}

export function transicion(sesion) {
  cambiarModoStub("sano");
  // La sonda de recuperación del ACL corre fuera del camino síncrono: esperar a que cierre el circuito.
  for (let i = 0; i < SEGUNDOS_TRANSICION && !circuitosCerrados(); i++) {
    sleep(1);
  }
  sleep(5);
  pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "180000000", plazoMeses: 180 });
  cambiarModoStub("caido");
}

export function escenarioProveedorCaidoConCache(sesion) {
  const { res, oferta } = pedirOferta(GRAPHQL_URL, sesion, { valorCredito: "180000000", plazoMeses: 180 });
  latenciaDegradadoCache.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  const valida = res.status === 200 && esOferta(oferta);
  degradadasSinDato.add(!valida);
  check(res, { "EC010 HTTP 200 (0 % de 5xx)": (r) => r.status === 200 });
  if (valida) {
    ofertasPreliminares.add(1);
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
    escenario: "EC009-EC010",
    url: BASE_URL,
    modo_reducido: MODO_REDUCIDO,
    duracion_fase: DURACION_FASE,
    peticiones_ec009: valor("latencia_corte_ec009_ms", "count"),
    p95_corte_ec009_ms: valor("latencia_corte_ec009_ms", "p(95)"),
    p99_corte_ec009_ms: valor("latencia_corte_ec009_ms", "p(99)"),
    max_corte_ec009_ms: valor("latencia_corte_ec009_ms", "max"),
    tasa_sin_corte_ec009: valor("llamadas_mayores_700ms_sin_corte", "rate"),
    peticiones_ec010: valor("latencia_degradado_ec010_ms", "count"),
    p95_degradado_ec010_ms: valor("latencia_degradado_ec010_ms", "p(95)"),
    p99_degradado_ec010_ms: valor("latencia_degradado_ec010_ms", "p(99)"),
    tasa_sin_dato_ec010: valor("degradadas_sin_dato_en_cache", "rate"),
    tasa_5xx: valor("errores_5xx", "rate"),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/falla-proveedor.json"]: JSON.stringify(data, null, 2),
  };
}
