// EC018 · Un socio que llama de más no degrada a los demás (KAN-223, KAN-217, HU-W07).
//
// **Interpretación declarada:** el repo no tiene la ficha de EC018. Se mide el aislamiento entre
// socios que da el límite de tasa por credencial (KAN-217, contrato v0.61): mientras el socio 1
// manda `RITMO_ABUSIVO` cotizaciones por segundo (muy por encima de su tope), el socio 2 manda
// `RITMO_NORMAL` (por debajo del suyo) y no debe notar nada.
//
// Todo lo que reporta es medido por k6. Cotizaciones de PRUEBAS (modo prueba): el tope por defecto en
// PRUEBAS es 5 por segundo (`LIMITE_SOCIO_RPS_PRUEBAS`), así que el BFF de la corrida debe tener el
// límite encendido (el valor por defecto) y el informe declara cuál.
//
// Umbrales:
//   - Socio normal: 0 % de 429, 0 % de 5xx, p95 ≤ 700 ms.
//   - Socio abusivo: 0 % de 5xx; todo 429 es `LIMITE_DE_TASA` con `Retry-After`; lo que pasa por
//     segundo no supera el tope (se mide como cotizaciones 200 por segundo de la corrida).
//   - El presupuesto del abusivo solo baja por sus 200 (un 429 no gasta): se compara al final con
//     `GET /v1/consumo`.
//
// Uso:
//   k6 run -e BFF_URL=http://localhost:8000 [-e RITMO_ABUSIVO=50 -e RITMO_NORMAL=3 -e DURACION=2m] \
//     tests/k6/ec018-socios-aislamiento.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { BASE_URL, cabeceras, cotizar, prepararSocio } from "./lib/socios.js";

const RITMO_ABUSIVO = parseInt(__ENV.RITMO_ABUSIVO || "50", 10);
const RITMO_NORMAL = parseInt(__ENV.RITMO_NORMAL || "3", 10);
const DURACION = __ENV.DURACION || "2m";
const TOPE = parseInt(__ENV.TOPE_ESPERADO || "5", 10);

const latenciaNormal = new Trend("latencia_socio_normal_ms", true);
const normal429 = new Rate("socio_normal_429");
const normal5xx = new Rate("socio_normal_5xx");
const abusivo5xx = new Rate("socio_abusivo_5xx");
const abusivo429Correcto = new Rate("socio_abusivo_429_con_codigo_y_retry_after");
const abusivoAceptadas = new Counter("socio_abusivo_200");
const abusivoRechazadas = new Counter("socio_abusivo_429");

export const options = {
  scenarios: {
    abusivo: {
      executor: "constant-arrival-rate",
      rate: RITMO_ABUSIVO,
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: Math.max(10, RITMO_ABUSIVO),
      maxVUs: RITMO_ABUSIVO * 3,
      exec: "abusivo",
    },
    normal: {
      executor: "constant-arrival-rate",
      rate: RITMO_NORMAL,
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: 5,
      maxVUs: 20,
      exec: "normal",
    },
  },
  thresholds: {
    socio_normal_429: ["rate==0"],
    socio_normal_5xx: ["rate==0"],
    latencia_socio_normal_ms: ["p(95)<=700"],
    socio_abusivo_5xx: ["rate==0"],
    socio_abusivo_429_con_codigo_y_retry_after: ["rate==1"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function consumido(tok) {
  const r = http.get(`${BASE_URL}/v1/consumo`, { headers: cabeceras(tok) });
  return r.status === 200 ? r.json("presupuesto.consumidas") : null;
}

export function setup() {
  const abusivo = prepararSocio(1);
  const normal = prepararSocio(2);
  sleep(1.1); // que la ventana del límite arranque limpia
  return { abusivo, normal, consumidasAntes: consumido(abusivo.token) };
}

export function abusivo({ abusivo: socio }) {
  const r = cotizar(socio.token, socio.consentimientoId, { socio: "abusivo" });
  abusivo5xx.add(r.status >= 500);
  if (r.status === 200) abusivoAceptadas.add(1);
  if (r.status === 429) {
    abusivoRechazadas.add(1);
    abusivo429Correcto.add(r.json("codigo") === "LIMITE_DE_TASA" && r.headers["Retry-After"] === "1");
  }
}

export function normal({ normal: socio }) {
  const r = cotizar(socio.token, socio.consentimientoId, { socio: "normal" });
  latenciaNormal.add(r.timings.duration);
  normal429.add(r.status === 429);
  normal5xx.add(r.status >= 500);
  check(r, { "socio normal: 200": (x) => x.status === 200 });
}

export function teardown({ abusivo: socio, consumidasAntes }) {
  sleep(1.1);
  const despues = consumido(socio.token);
  // k6 no deja leer contadores en teardown: el informe compara este número con `socio_abusivo_200`.
  console.log(
    `presupuesto del abusivo: antes=${consumidasAntes} despues=${despues} ` +
      `diferencia=${despues === null || consumidasAntes === null ? "?" : despues - consumidasAntes} ` +
      `(debe ser igual a socio_abusivo_200; tope esperado ${TOPE}/s)`,
  );
}
