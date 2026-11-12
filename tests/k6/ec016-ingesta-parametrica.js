// EC016 (HA-ESC-002, KAN-259, T-M04-5, Fase 7): ingesta masiva de eventos paramétricos (clima y
// vuelos) hacia CLAIMS, sin perder ninguno y sin degradar el camino síncrono del usuario.
//
// D9 (plan de notificaciones push, sección E): "100.000 eventos en 10 min en local/staging
// declarado como reducido, con la proyección a 1.000.000". Ese plan también dice (sección G) que
// "las APIs gratis no aguantan 1.000.000 de eventos: EC016 se mide con `stub-eventos-parametricos`
// y un generador que publica directo en el tema".
//
// SUSTITUCIÓN DECLARADA DE ESTE GUION (no hay Pub/Sub real en este entorno, ver CLAUDE.md de
// backend y el encargo de esta tarea): en vez de publicar en el tema `eventos-parametricos` y dejar
// que la suscripción push de Pub/Sub entregue a CLAIMS, este guion publica DIRECTO contra el
// endpoint HTTP que esa suscripción llamaría: `POST {CLAIMS_URL}/eventos/parametricos`, con el
// mismo payload (`evento.parametrico.recibido`, ver `contracts/events/evento.parametrico.recibido.schema.json`).
// En staging con Pub/Sub real, la ruta sería ACL Worker -> Pub/Sub -> suscripción push -> CLAIMS, con
// reintentos y DLQ que aquí no existen: lo que este guion mide es el tramo final (CLAIMS) a la tasa
// de llegada declarada, no el tramo de Pub/Sub.
//
// Qué mide:
//   1. Cero pérdida: de los eventos publicados, cuántos CLAIMS aceptó (200, SIN importar si el
//      evento califica o no para un siniestro: "procesado, sin acción" también cuenta) contra
//      cuántos fallaron (5xx, timeout, u otro código). Si CLAIMS está al tope y responde 429/503 en
//      vez de aceptar o tumbarse, se cuenta como contrapresión explícita, no como pérdida.
//   2. Cuántos de esos eventos SÍ generaron un siniestro real (creados > 0 en la respuesta), para
//      confirmar que la idempotencia y el cruce de coberturas funcionan bajo carga, sin disparar
//      una orden de pago por cada evento (solo una fracción pequeña, declarada abajo, cae sobre el
//      umbral de la semilla de POLICY: AV9340:2026-11-11 con retraso >= 120 min, o la celda
//      4.62,-74.09 con PRECIPITACION_MM >= 50).
//
// Este guion NO corre en paralelo con otro guion de k6 contra el BFF en este entorno (ver limitación
// en la evidencia EC016): se corrió solo. El efecto sobre el camino síncrono del BFF queda pendiente
// de una corrida simultánea.
//
// Uso:
//   k6 run -e CLAIMS_URL=http://localhost:8012 tests/k6/ec016-ingesta-parametrica.js
//   EVENTOS_TOTAL=5000 DURACION_INGESTA=2m k6 run tests/k6/ec016-ingesta-parametrica.js

import http from "k6/http";
import { check } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const CLAIMS_URL = (__ENV.CLAIMS_URL || "http://localhost:8012").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";

// Default de D9: 100.000 eventos en 10 min (~167 eventos/s). Configurable para una escala menor si
// el entorno no la sostiene (ver evidencia EC016 por lo que de verdad se corrió aquí).
const EVENTOS_TOTAL = Number(__ENV.EVENTOS_TOTAL || (MODO_REDUCIDO ? 100000 : 100000));
const DURACION_INGESTA = __ENV.DURACION_INGESTA || "10m";

// Fracción de eventos que caen SOBRE el umbral de una póliza real de la semilla
// (`sembrar_polizas_parametricas`), para que de verdad generen un siniestro. El resto son
// "procesados, sin acción": vuelos o celdas que no tienen cobertura, o bajo el umbral.
const FRACCION_CALZA = Number(__ENV.FRACCION_CALZA || 0.02);

const eventosPublicados = new Counter("eventos_publicados");
const eventosAceptados = new Rate("eventos_aceptados_200");
const eventosFallidos = new Counter("eventos_fallidos");
const eventosContrapresion = new Counter("eventos_contrapresion_429_503");
const siniestrosCreados = new Counter("siniestros_creados");
const duracionIngesta = new Trend("duracion_ingesta_ms", true);

export const options = {
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  scenarios: {
    ec016_ingesta_parametrica: {
      executor: "constant-arrival-rate",
      rate: Math.max(1, Math.round(EVENTOS_TOTAL / duracionASegundos(DURACION_INGESTA))),
      timeUnit: "1s",
      duration: DURACION_INGESTA,
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  thresholds: {
    // Cero pérdida: todo lo publicado se contabiliza como aceptado o fallido; aquí solo se exige
    // que la aceptación sea alta. El detalle exacto (aceptados + contrapresión + fallidos == publicados)
    // se verifica en el resumen, no en un threshold (k6 no puede comparar dos counters entre sí).
    eventos_aceptados_200: ["rate>=0.95"],
  },
};

function duracionASegundos(texto) {
  const m = /^(\d+)(s|m|h)$/.exec(texto);
  if (!m) return 600; // 10 min por defecto si el formato no se reconoce
  const valor = Number(m[1]);
  return m[2] === "h" ? valor * 3600 : m[2] === "m" ? valor * 60 : valor;
}

// UUID v4 simple (sin `k6/crypto`, que no trae randomUUID en todas las versiones de k6).
function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Vuelo y celda de la semilla (`services/policy/app/sembrar_polizas_parametricas.py`):
// AV9340:2026-11-11, umbral_retraso_min=120; celda 4.62,-74.09, PRECIPITACION_MM, umbral=50.
function eventoVueloQueCalza() {
  return {
    tipo: "VUELO",
    clave: "AV9340:2026-11-11",
    valor: 120 + Math.floor(Math.random() * 180), // 120 a 300 min: siempre sobre el umbral
    medidoEn: "2026-06-01T08:00:00+00:00", // dentro de la vigencia 2026-01-01..2027-01-01
    fuente: "aviationstack",
    cancelado: false,
  };
}

function eventoClimaQueCalza() {
  return {
    tipo: "CLIMA",
    clave: "4.62,-74.09",
    valor: 50 + Math.floor(Math.random() * 100), // 50 a 150 mm: siempre sobre el umbral
    medidoEn: "2026-06-01T08:00:00+00:00",
    fuente: "open-meteo",
    variable: "PRECIPITACION_MM",
  };
}

// La mayoría: vuelos o celdas sin ninguna cobertura (procesado, sin acción) o, para variar, el
// vuelo AV1234 de la semilla (que SÍ tiene cobertura, pero con un retraso bajo el umbral).
function eventoVueloQueNoCalza() {
  const usarAv1234 = Math.random() < 0.3;
  return {
    tipo: "VUELO",
    clave: usarAv1234 ? "AV1234:2026-11-11" : `K6${Math.floor(Math.random() * 9000)}:2026-11-${1 + Math.floor(Math.random() * 28)}`,
    valor: Math.floor(Math.random() * 100), // 0 a 99 min: bajo cualquier umbral razonable (120)
    medidoEn: "2026-06-01T08:00:00+00:00",
    fuente: "aviationstack",
    cancelado: false,
  };
}

function eventoClimaQueNoCalza() {
  // Celda lejos de 4.62,-74.09 (la única con cobertura de clima en la semilla).
  const lat = (Math.random() * 10).toFixed(2);
  const lon = (-70 - Math.random() * 10).toFixed(2);
  return {
    tipo: "CLIMA",
    clave: `${lat},${lon}`,
    valor: Math.floor(Math.random() * 40), // 0 a 39 mm: bajo el umbral de 50 también si calzara
    medidoEn: "2026-06-01T08:00:00+00:00",
    fuente: "open-meteo",
    variable: "PRECIPITACION_MM",
  };
}

function generarEvento() {
  const calza = Math.random() < FRACCION_CALZA;
  const esVuelo = Math.random() < 0.5;
  let datos;
  if (calza) {
    datos = esVuelo ? eventoVueloQueCalza() : eventoClimaQueCalza();
  } else {
    datos = esVuelo ? eventoVueloQueNoCalza() : eventoClimaQueNoCalza();
  }
  return {
    eventId: uuidv4(),
    tipo: "evento.parametrico.recibido",
    version: 1,
    ocurridoEn: new Date().toISOString(),
    datos,
  };
}

export function setup() {
  const salud = http.get(`${CLAIMS_URL}/health`, { timeout: "5s" });
  if (salud.status !== 200) {
    throw new Error(`CLAIMS no responde en ${CLAIMS_URL}/health (status ${salud.status}): no se puede medir EC016.`);
  }
  return { inicio: Date.now() };
}

export default function () {
  const evento = generarEvento();
  const respuesta = http.post(`${CLAIMS_URL}/eventos/parametricos`, JSON.stringify(evento), {
    headers: { "Content-Type": "application/json" },
    timeout: "10s",
  });
  eventosPublicados.add(1);
  duracionIngesta.add(respuesta.timings.duration);

  if (respuesta.status === 429 || respuesta.status === 503) {
    eventosContrapresion.add(1);
    eventosAceptados.add(false);
    return;
  }
  const ok = respuesta.status === 200;
  eventosAceptados.add(ok);
  if (!ok) {
    eventosFallidos.add(1);
    check(respuesta, { "CLAIMS acepta el evento (200)": () => ok });
    return;
  }
  let cuerpo = null;
  try {
    cuerpo = JSON.parse(respuesta.body);
  } catch (_) {
    cuerpo = null;
  }
  if (cuerpo && cuerpo.creados > 0) {
    siniestrosCreados.add(cuerpo.creados);
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, estadistico) => (m[nombre] ? m[nombre].values[estadistico] : null);
  const publicados = valor("eventos_publicados", "count") || 0;
  const aceptados = Math.round((valor("eventos_aceptados_200", "rate") || 0) * publicados);
  const fallidos = valor("eventos_fallidos", "count") || 0;
  const contrapresion = valor("eventos_contrapresion_429_503", "count") || 0;
  const resumen = {
    escenario: "EC016",
    nota: "HTTP directo a CLAIMS en vez de Pub/Sub real (no disponible en este entorno); ver cabecera del guion.",
    claims_url: CLAIMS_URL,
    eventos_total_pedidos: EVENTOS_TOTAL,
    duracion_ingesta: DURACION_INGESTA,
    fraccion_calza_umbral: FRACCION_CALZA,
    eventos_publicados: publicados,
    eventos_aceptados_200: aceptados,
    eventos_fallidos: fallidos,
    eventos_contrapresion_429_503: contrapresion,
    perdida: publicados - aceptados - fallidos - contrapresion, // debe ser 0: publicados = aceptados + fallidos + contrapresion
    siniestros_creados: valor("siniestros_creados", "count") || 0,
    p50_ms: valor("duracion_ingesta_ms", "med"),
    p95_ms: valor("duracion_ingesta_ms", "p(95)"),
    p99_ms: valor("duracion_ingesta_ms", "p(99)"),
    max_ms: valor("duracion_ingesta_ms", "max"),
  };
  return {
    stdout: JSON.stringify(resumen, null, 2) + "\n",
    [__ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/ec016-ingesta-parametrica.json"]: JSON.stringify(data, null, 2),
  };
}
