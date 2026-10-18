// EC011 (lectura de la réplica p95 <= 150 ms) y EC012 (p99 <= 300 ms y 0 lecturas con un perfil
// obsoleto: "leer lo que acabo de escribir", D-02 con `afterClusterTime`).
//
// Medición REAL, sin descontar ni estimar nada. En cada iteración:
//   1. escribe un perfil en RISK (`POST /perfiles`) con una marca única y obtiene `perfil_version` y
//      `operation_time` (el escritor único);
//   2. lo lee de RATING (`GET /perfiles/{cliente}?operation_time=`), que lee de la SECUNDARIA de MongoDB
//      con sesión causal. Solo el tiempo de ESTA lectura entra en `latencia_lectura_replica_ms`;
//   3. comprueba que lo leído sea lo escrito (misma versión y misma marca) y que haya salido de la
//      secundaria: si no, cuenta como perfil obsoleto.
//
// Requiere acceso directo a RISK y a RATING (`RISK_URL`, `RATING_URL`). En staging son privados: pasa
// `RISK_TOKEN` y `RATING_TOKEN` (token de identidad de cada servicio, `gcloud auth print-identity-token
// --audiences=<url>`). Sin acceso, el guion se aborta en vez de inventar un resultado.
//
// Uso:
//   k6 run -e RISK_URL=http://localhost:8002 -e RATING_URL=http://localhost:8003 tests/k6/lectura-replica.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const RISK_URL = (__ENV.RISK_URL || "http://localhost:8002").replace(/\/$/, "");
const RATING_URL = (__ENV.RATING_URL || "http://localhost:8003").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION = __ENV.DURACION_NORMAL || (MODO_REDUCIDO ? "10m" : "30m");
const CLIENTES = 50;

const latenciaLecturaReplica = new Trend("latencia_lectura_replica_ms", true);
const latenciaEscrituraRisk = new Trend("latencia_escritura_risk_ms", true);
const perfilesObsoletos = new Rate("perfiles_obsoletos");
const leidasDeLaPrimaria = new Rate("lecturas_de_la_primaria");
const lecturasConsistentes = new Counter("lecturas_consistentes");

// El resumen incluye p(99) y la cantidad de muestras: sin ellas no se puede decir cuánto se midió.
export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec011_ec012_replica: {
      executor: "constant-arrival-rate",
      rate: 56,
      timeUnit: "10s", // mismo ritmo que EC003 (5,6 req/s)
      duration: DURACION,
      preAllocatedVUs: 10,
      maxVUs: 30,
    },
  },
  thresholds: {
    latencia_lectura_replica_ms: ["p(95)<=150", "p(99)<=300"],
    perfiles_obsoletos: ["rate==0"],
  },
};

function cabeceras(token) {
  const h = { "Content-Type": "application/json" };
  if (token) {
    h["Authorization"] = `Bearer ${token}`;
  }
  return h;
}

export function setup() {
  const risk = http.get(`${RISK_URL}/health`, { timeout: "5s", headers: cabeceras(__ENV.RISK_TOKEN) });
  const rating = http.get(`${RATING_URL}/health`, { timeout: "5s", headers: cabeceras(__ENV.RATING_TOKEN) });
  if (risk.status !== 200 || rating.status !== 200) {
    throw new Error(
      `Sin acceso directo a RISK (${risk.status}) o RATING (${rating.status}): no se puede medir la réplica. ` +
        "Define RISK_URL/RATING_URL (y RISK_TOKEN/RATING_TOKEN si son privados)."
    );
  }
  return { inicio: Date.now() };
}

export default function () {
  const cliente = `cli-k6-replica-${Math.floor(Math.random() * CLIENTES)}`;
  // Marca única de esta escritura: si la lectura la devuelve, vio lo que se acaba de escribir.
  const marca = Date.now() * 1000 + Math.floor(Math.random() * 1000);

  const escritura = http.post(
    `${RISK_URL}/perfiles`,
    JSON.stringify({
      cliente_id: cliente,
      fuentes: [
        {
          fuente: "historial-crediticio",
          datos: { score_crediticio: 600 + Math.floor(Math.random() * 300), marca },
          capturado_en: new Date().toISOString(),
        },
      ],
    }),
    { headers: cabeceras(__ENV.RISK_TOKEN) }
  );
  latenciaEscrituraRisk.add(escritura.timings.duration);

  let escrito = null;
  try {
    escrito = JSON.parse(escritura.body);
  } catch (_) {
    escrito = null;
  }
  if (escritura.status !== 200 || !escrito || !escrito.operation_time) {
    // Una escritura fallida no es una lectura obsoleta, pero tampoco se puede medir: se cuenta aparte.
    check(escritura, { "RISK aceptó el perfil": (r) => r.status === 200 });
    return;
  }

  const lectura = http.get(
    `${RATING_URL}/perfiles/${cliente}?operation_time=${encodeURIComponent(escrito.operation_time)}`,
    { headers: cabeceras(__ENV.RATING_TOKEN) }
  );
  latenciaLecturaReplica.add(lectura.timings.duration);

  let perfil = null;
  try {
    perfil = JSON.parse(lectura.body);
  } catch (_) {
    perfil = null;
  }
  const leido =
    lectura.status === 200 &&
    perfil &&
    perfil.perfil_version === escrito.perfil_version &&
    perfil.fuentes &&
    perfil.fuentes[0] &&
    perfil.fuentes[0].datos.marca === marca;
  perfilesObsoletos.add(!leido);
  leidasDeLaPrimaria.add(Boolean(perfil && perfil.leido_de_secundaria === false));
  if (leido) {
    lecturasConsistentes.add(1);
  }
  check(lectura, { "RATING leyó el perfil recién escrito": () => Boolean(leido) });
  sleep(0.05);
}

export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, estadistico) => (m[nombre] ? m[nombre].values[estadistico] : null);
  const resumen = {
    escenario: "EC011-EC012",
    risk_url: RISK_URL,
    rating_url: RATING_URL,
    duracion: DURACION,
    lecturas: valor("latencia_lectura_replica_ms", "count"),
    p50_lectura_ms: valor("latencia_lectura_replica_ms", "med"),
    p95_lectura_ms: valor("latencia_lectura_replica_ms", "p(95)"),
    p99_lectura_ms: valor("latencia_lectura_replica_ms", "p(99)"),
    p95_escritura_risk_ms: valor("latencia_escritura_risk_ms", "p(95)"),
    tasa_perfiles_obsoletos: valor("perfiles_obsoletos", "rate"),
    tasa_lecturas_de_la_primaria: valor("lecturas_de_la_primaria", "rate"),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/lectura-replica.json"]: JSON.stringify(data, null, 2),
  };
}
