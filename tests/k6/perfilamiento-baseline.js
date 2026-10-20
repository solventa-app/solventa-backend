// EC003 (operación normal: p95 <= 400 ms a 20.000 req/h ≈ 5,6 req/s) y
// EC004 (carga pico: p99 <= 800 ms a 3x el ritmo normal ≈ 16,8 req/s).
//
// Todo lo que reporta es MEDIDO por k6 (tiempos de las respuestas de `solicitarOferta`).
//
// En modo reducido (MODO_REDUCIDO=true, por defecto, para no gastar el presupuesto de staging):
//   - 10 min a 5,6 req/s (~3.360 peticiones, suficiente para un p95 válido)
//   - 2 min de pico a 16,8 req/s (3x) dentro de la misma corrida
// Para una corrida más corta (p. ej. local) se puede acotar con DURACION_NORMAL y DURACION_PICO
// (formato de k6: "3m"); el informe debe decir cuántas peticiones salieron de verdad.
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> tests/k6/perfilamiento-baseline.js

import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { esOferta, pedirOferta, prepararSesion } from "./lib/preparar.js";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_NORMAL = __ENV.DURACION_NORMAL || (MODO_REDUCIDO ? "10m" : "60m");
const DURACION_PICO = __ENV.DURACION_PICO || (MODO_REDUCIDO ? "2m" : "10m");

const latenciaNormal = new Trend("latencia_normal_ms", true);
const latenciaPico = new Trend("latencia_pico_ms", true);
const errores5xx = new Rate("errores_5xx");
const ofertasInvalidas = new Rate("ofertas_invalidas");
const ofertasValidas = new Counter("ofertas_validas");

// El resumen incluye p(99) y la cantidad de muestras: sin ellas no se puede decir cuánto se midió.
export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec003_operacion_normal: {
      executor: "constant-arrival-rate",
      rate: 56,
      timeUnit: "10s", // 5,6 req/s = 20.160 req/h
      duration: DURACION_NORMAL,
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: "escenarioNormal",
    },
    ec004_carga_pico: {
      executor: "ramping-arrival-rate",
      startTime: DURACION_NORMAL,
      startRate: 56,
      timeUnit: "10s",
      preAllocatedVUs: 25,
      maxVUs: 60,
      stages: [
        { target: 168, duration: "30s" }, // sube a 16,8 req/s (3x)
        { target: 168, duration: DURACION_PICO },
        { target: 56, duration: "30s" },
      ],
      exec: "escenarioPico",
    },
  },
  thresholds: {
    latencia_normal_ms: ["p(95)<=400"],
    latencia_pico_ms: ["p(99)<=800"],
    errores_5xx: ["rate==0"],
    ofertas_invalidas: ["rate==0"],
  },
};

export function setup() {
  const sesion = prepararSesion(GRAPHQL_URL, __ENV.DOCUMENTO_PRUEBA || "CC-K6-100001");
  // Primera solicitud para calentar la caché de fuentes y del score (LAT-001); no cuenta en las métricas.
  pedirOferta(GRAPHQL_URL, sesion);
  return sesion;
}

function ejecutarCotizacion(sesion, tendencia) {
  const { res, oferta } = pedirOferta(GRAPHQL_URL, sesion);
  tendencia.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  const valida = res.status === 200 && esOferta(oferta);
  ofertasInvalidas.add(!valida);
  check(res, { "HTTP 200 (nunca 5xx crudo)": (r) => r.status === 200 });
  if (valida) {
    ofertasValidas.add(1);
  }
  sleep(0.05);
}

export function escenarioNormal(sesion) {
  ejecutarCotizacion(sesion, latenciaNormal);
}

export function escenarioPico(sesion) {
  ejecutarCotizacion(sesion, latenciaPico);
}

export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, estadistico) => (m[nombre] ? m[nombre].values[estadistico] : null);
  const resumen = {
    escenario: "EC003-EC004",
    url: BASE_URL,
    modo_reducido: MODO_REDUCIDO,
    duracion_normal: DURACION_NORMAL,
    duracion_pico: DURACION_PICO,
    peticiones_normal: valor("latencia_normal_ms", "count"),
    peticiones_pico: valor("latencia_pico_ms", "count"),
    p50_normal_ms: valor("latencia_normal_ms", "med"),
    p95_normal_ms: valor("latencia_normal_ms", "p(95)"),
    p99_pico_ms: valor("latencia_pico_ms", "p(99)"),
    tasa_5xx: valor("errores_5xx", "rate"),
    tasa_ofertas_invalidas: valor("ofertas_invalidas", "rate"),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/perfilamiento-baseline.json"]: JSON.stringify(data, null, 2),
  };
}
