// EC015 · La API de socios aguanta que crezca el tráfico de varios socios (KAN-223, HU-W07).
//
// **Interpretación declarada:** el repo no tiene la ficha de EC015; en el refinamiento aparece junto al
// autoescalado de Cloud Run (KAN-221). Aquí se mide lo que se puede medir sin la nube: con el tráfico
// de `SOCIOS` socios subiendo por escalones hasta `RITMO_MAXIMO` cotizaciones por segundo, la latencia
// de `POST /v1/cotizaciones` se mantiene y no hay ningún 5xx. Que Cloud Run sume instancias solo se ve
// en staging (métrica `instance_count` del servicio `bff`); en local hay UN proceso del BFF.
//
// Todo lo que reporta es medido por k6. Las cotizaciones son de PRUEBAS (modo prueba): no consultan
// fuentes, pero sí reservan presupuesto en AUTH, que es la escritura del camino de la cotización.
// Para que el límite de tasa por credencial (KAN-217) no corte la carga, el BFF de la corrida debe
// tener `LIMITE_SOCIO_RPS_PRUEBAS` por encima de RITMO_MAXIMO / SOCIOS (o 0); el informe lo declara.
//
// Umbrales: p95 ≤ 700 ms (el corte del gateway, W07-25) en cada escalón; 0 % de 5xx; ≥ 99 % de 200.
//
// Uso:
//   k6 run -e BFF_URL=http://localhost:8000 [-e RITMO_MAXIMO=60 -e SOCIOS=3 -e ESCALON=1m] \
//     tests/k6/ec015-socios-escala.js

import { check } from "k6";
import exec from "k6/execution";
import { Rate, Trend } from "k6/metrics";
import { cotizar, prepararSocio } from "./lib/socios.js";

const SOCIOS = parseInt(__ENV.SOCIOS || "3", 10);
const RITMO_MAXIMO = parseInt(__ENV.RITMO_MAXIMO || "60", 10);
const ESCALON = __ENV.ESCALON || "1m";
const ESCALONES = [0.25, 0.5, 0.75, 1].map((f) => Math.max(1, Math.round(RITMO_MAXIMO * f)));

const latencia = new Trend("latencia_cotizacion_ms", true);
const errores5xx = new Rate("errores_5xx");
const exitosas = new Rate("cotizaciones_200");

const umbrales = {
  errores_5xx: ["rate==0"],
  cotizaciones_200: ["rate>=0.99"],
  latencia_cotizacion_ms: ["p(95)<=700"],
};
for (const ritmo of ESCALONES) umbrales[`latencia_cotizacion_ms{ritmo:${ritmo}}`] = ["p(95)<=700"];

export const options = {
  scenarios: {
    escalones: {
      executor: "ramping-arrival-rate",
      startRate: ESCALONES[0],
      timeUnit: "1s",
      preAllocatedVUs: Math.max(10, RITMO_MAXIMO),
      maxVUs: RITMO_MAXIMO * 4,
      // Subida corta y escalón sostenido para cada ritmo: lo que se compara es el escalón.
      stages: ESCALONES.flatMap((ritmo) => [
        { target: ritmo, duration: "10s" },
        { target: ritmo, duration: ESCALON },
      ]),
    },
  },
  thresholds: umbrales,
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  return { socios: Array.from({ length: SOCIOS }, (_, i) => prepararSocio(i + 1)) };
}

function ritmoActual() {
  // Escalón en curso según el reloj del escenario (10 s de subida + ESCALON por ritmo).
  const segundos = (Date.now() - exec.scenario.startTime) / 1000;
  const porEscalon = 10 + parseInt(ESCALON, 10) * (ESCALON.endsWith("m") ? 60 : 1);
  return ESCALONES[Math.min(ESCALONES.length - 1, Math.floor(segundos / porEscalon))];
}

export default function ({ socios }) {
  const socio = socios[(__ITER + __VU) % socios.length];
  const ritmo = ritmoActual();
  const r = cotizar(socio.token, socio.consentimientoId, { ritmo: String(ritmo) });
  latencia.add(r.timings.duration, { ritmo: String(ritmo) });
  errores5xx.add(r.status >= 500);
  exitosas.add(r.status === 200);
  check(r, { "200 con oferta": (x) => x.status === 200 && !!x.json("oferta.id") });
}
