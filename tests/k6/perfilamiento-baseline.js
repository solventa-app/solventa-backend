// EC003 (operación normal: p95 <= 400 ms a 20.000 req/h ≈ 5,6 req/s) y
// EC004 (carga pico: p99 <= 800 ms a 3x el ritmo normal ≈ 16,8 req/s).
//
// En modo reducido (MODO_REDUCIDO=true, por defecto para no gastar el presupuesto de staging):
//   - 10 min a 5,6 req/s (~3.360 peticiones, suficiente para p95/p99 estadísticamente válido)
//   - 2 min de pico a 16,8 req/s (3x) dentro de la misma corrida
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> -e MODO_REDUCIDO=true tests/k6/perfilamiento-baseline.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";

const latenciaNormal = new Trend("latencia_normal_ms", true);
const latenciaPico = new Trend("latencia_pico_ms", true);
const errores5xx = new Rate("errores_5xx");
const ofertasValidas = new Counter("ofertas_validas");

export const options = {
  scenarios: {
    ec003_operacion_normal: {
      executor: "constant-arrival-rate",
      rate: 56,
      timeUnit: "10s", // 5,6 req/s = 20.160 req/h
      duration: MODO_REDUCIDO ? "10m" : "60m",
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: "escenarioNormal",
    },
    ec004_carga_pico: {
      executor: "ramping-arrival-rate",
      startTime: MODO_REDUCIDO ? "10m" : "60m",
      startRate: 56,
      timeUnit: "10s",
      preAllocatedVUs: 25,
      maxVUs: 60,
      stages: [
        { target: 168, duration: "30s" }, // sube a 16,8 req/s (3x)
        { target: 168, duration: MODO_REDUCIDO ? "2m" : "10m" },
        { target: 56, duration: "30s" },
      ],
      exec: "escenarioPico",
    },
  },
  thresholds: {
    "latencia_normal_ms": ["p(95)<=400"],
    "latencia_pico_ms": ["p(99)<=800"],
    "http_req_duration": ["p(95)<=400", "p(99)<=800"],
    "errores_5xx": ["rate==0"],
  },
};

const MUTACION_SESION = `mutation($entrada: CredencialesInput!) {
  iniciarSesion(entrada: $entrada) {
    __typename
    ... on Sesion { token }
  }
}`;

const MUTACION_CONSENTIMIENTO = `mutation($entrada: ConsentimientoInput!) {
  registrarConsentimiento(entrada: $entrada) {
    id
    vigente
  }
}`;

const MUTACION_OFERTA = `mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) {
    __typename
    ... on Oferta {
      id
      tipo
      prima { valor moneda }
      rangoPrima { minima { valor } maxima { valor } }
    }
    ... on ErrorOferta {
      mensaje
      puedeReintentar
    }
  }
}`;

function cabeceras(token) {
  const h = { "Content-Type": "application/json" };
  if (token) {
    h["Authorization"] = `Bearer ${token}`;
  }
  return h;
}

export function setup() {
  let token = __ENV.AUTH_TOKEN || "";
  if (!token) {
    const resSesion = http.post(
      GRAPHQL_URL,
      JSON.stringify({
        query: MUTACION_SESION,
        variables: {
          entrada: {
            usuario: __ENV.USUARIO_PRUEBA || "admin.solventa",
            clave: __ENV.CLAVE_PRUEBA || "admin-local-solventa",
          },
        },
      }),
      { headers: cabeceras("") }
    );
    try {
      const body = JSON.parse(resSesion.body);
      token = (body.data && body.data.iniciarSesion && body.data.iniciarSesion.token) || "";
    } catch (_) {
      token = "";
    }
  }

  const documento = __ENV.DOCUMENTO_PRUEBA || "CC-K6-100001";
  const resCons = http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_CONSENTIMIENTO,
      variables: {
        entrada: {
          finalidad: "cotizacion-vida-hipotecario",
          versionTexto: "v1",
        },
      },
    }),
    { headers: cabeceras(token) }
  );

  let consentimientoId = __ENV.CONSENTIMIENTO_ID || "cons-k6-fallback";
  try {
    const body = JSON.parse(resCons.body);
    if (body.data && body.data.registrarConsentimiento && body.data.registrarConsentimiento.id) {
      consentimientoId = body.data.registrarConsentimiento.id;
    }
  } catch (_) {
    // usa fallback si se corre en seco
  }

  // Primera solicitud para calentar caché de fuentes y score (LAT-001).
  http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: documento,
          valorCredito: "200000000",
          plazoMeses: 240,
          direccionInmueble: "Bogotá D.C., Chapinero",
          consentimientoId: consentimientoId,
        },
      },
    }),
    { headers: cabeceras(token) }
  );

  return { token, consentimientoId, documento };
}

function ejecutarCotizacion(data, metricaTendencia) {
  const res = http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: data.documento,
          valorCredito: "200000000",
          plazoMeses: 240,
          direccionInmueble: "Bogotá D.C., Chapinero",
          consentimientoId: data.consentimientoId,
        },
      },
    }),
    { headers: cabeceras(data.token) }
  );

  metricaTendencia.add(res.timings.duration);
  errores5xx.add(res.status >= 500);

  const ok = check(res, {
    "HTTP 200 (nunca 5xx crudo)": (r) => r.status === 200,
    "respuesta GraphQL con solicitarOferta": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Boolean(body.data && body.data.solicitarOferta);
      } catch (_) {
        return false;
      }
    },
  });

  if (ok) {
    ofertasValidas.add(1);
  }
  sleep(0.05);
}

export function escenarioNormal(data) {
  ejecutarCotizacion(data, latenciaNormal);
}

export function escenarioPico(data) {
  ejecutarCotizacion(data, latenciaPico);
}

export function handleSummary(data) {
  const salida = __ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/perfilamiento-baseline.json";
  return {
    stdout: JSON.stringify(
      {
        escenario: "EC003-EC004",
        modo_reducido: MODO_REDUCIDO,
        url: BASE_URL,
        p95_normal_ms: data.metrics.latencia_normal_ms ? data.metrics.latencia_normal_ms.values["p(95)"] : null,
        p99_pico_ms: data.metrics.latencia_pico_ms ? data.metrics.latencia_pico_ms.values["p(99)"] : null,
        tasa_5xx: data.metrics.errores_5xx ? data.metrics.errores_5xx.values.rate : 0,
      },
      null,
      2
    ) + "\n",
    [salida]: JSON.stringify(data, null, 2),
  };
}
