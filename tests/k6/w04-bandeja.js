// HU-W04 · EC011 / EC012 sobre la mesa de siniestros: la bandeja y el detalle se leen de la SECUNDARIA de
// MongoDB (W04-2, HA-LAT-003). Umbrales del escenario: lectura de la réplica p95 <= 150 ms y p99 <= 300 ms.
//
// Mide dos cosas, cada una en su métrica:
//   - `latencia_lectura_replica_ms`: CLAIMS directo (`GET /siniestros` y `GET /siniestros/{id}`), es decir,
//     la lectura en la réplica más el armado del expediente. Es la que se compara con EC011/EC012.
//   - `latencia_bandeja_gateway_ms`: la bandeja por el gateway (`siniestros` en `/v1/graphql/web` con un
//     token de BACKOFFICE), lo que vive el operador. Solo informativa.
//
// Que las lecturas salgan de la secundaria se comprueba fuera del guion, con los contadores de MongoDB
// (ver docs/evidencias/EC011-EC012-W04-<fecha>.md). Requiere la semilla (`sembrar-siniestros`).
//
// Uso:
//   k6 run -e CLAIMS_URL=http://localhost:8011 -e GATEWAY_URL=http://localhost:8080 tests/k6/w04-bandeja.js

import http from "k6/http";
import { check } from "k6";
import { Trend } from "k6/metrics";

const CLAIMS_URL = (__ENV.CLAIMS_URL || "http://localhost:8011").replace(/\/$/, "");
const GATEWAY_URL = (__ENV.GATEWAY_URL || "http://localhost:8080").replace(/\/$/, "");
const DURACION = __ENV.DURACION_NORMAL || "2m";
const RITMO = Number(__ENV.RITMO || 10);
const IDS = ["sin-01", "sin-02", "sin-03", "sin-04", "sin-05", "sin-06", "sin-07", "sin-08", "sin-09", "sin-10"];

const latenciaReplica = new Trend("latencia_lectura_replica_ms", true);
const latenciaGateway = new Trend("latencia_bandeja_gateway_ms", true);

export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    replica: {
      executor: "constant-arrival-rate",
      exec: "lecturaReplica",
      rate: RITMO,
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
    gateway: {
      executor: "constant-arrival-rate",
      exec: "bandejaGateway",
      rate: Math.max(1, Math.floor(RITMO / 2)),
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: 5,
      maxVUs: 25,
    },
  },
  thresholds: {
    latencia_lectura_replica_ms: ["p(95)<=150", "p(99)<=300"],
    checks: ["rate>0.99"],
  },
};

export function setup() {
  const r = http.post(
    `${GATEWAY_URL}/v1/graphql/web`,
    JSON.stringify({
      query:
        "mutation($e: CredencialesInput!) { iniciarSesion(entrada: $e) { ... on Sesion { token } } }",
      variables: {
        e: {
          usuario: __ENV.USUARIO_BACKOFFICE || "admin.solventa",
          clave: __ENV.CLAVE_BACKOFFICE || "admin-local-solventa",
        },
      },
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  const token = r.json("data.iniciarSesion.token");
  if (!token) throw new Error("No se pudo iniciar sesión como BACKOFFICE: el guion no inventa un resultado");
  return { token };
}

export function lecturaReplica() {
  const detalle = Math.random() < 0.5;
  const url = detalle
    ? `${CLAIMS_URL}/siniestros/${IDS[Math.floor(Math.random() * IDS.length)]}`
    : `${CLAIMS_URL}/siniestros`;
  const r = http.get(url, { tags: { lectura: detalle ? "detalle" : "bandeja" } });
  latenciaReplica.add(r.timings.duration);
  check(r, { "CLAIMS 200": (x) => x.status === 200 });
}

export function bandejaGateway(datos) {
  const r = http.post(
    `${GATEWAY_URL}/v1/graphql/web`,
    JSON.stringify({ query: "{ siniestros { id radicado estado asegurado { nombre } notificadoEn } }" }),
    { headers: { "Content-Type": "application/json", Authorization: `Bearer ${datos.token}` } },
  );
  latenciaGateway.add(r.timings.duration);
  check(r, {
    "gateway 200": (x) => x.status === 200,
    "10 casos": (x) => (x.json("data.siniestros") || []).length === 10,
  });
}
