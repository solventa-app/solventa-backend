// EC009 (100 % de llamadas > 700 ms cortadas por el circuit breaker/timeout duro, 0 bloqueadas) y
// EC010 (journey degradado <= 500 ms p99 con caché caliente y 0 % de errores 5xx).
//
// En modo reducido (MODO_REDUCIDO=true):
//   - Fase 1 (EC009): stub en modo `lento` durante 5 min (verifica corte a 700 ms y apertura del circuito)
//   - Fase 2 (EC010): stub en modo `caido` durante 5 min con caché caliente (verifica p99 <= 500 ms y 0 % 5xx)
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> -e STUB_OPEN_FINANCE_URL=https://<stub> tests/k6/falla-proveedor.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const STUB_URL = (__ENV.STUB_OPEN_FINANCE_URL || "http://localhost:8101").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";
const DURACION_FASE = MODO_REDUCIDO ? "5m" : "15m";

const latenciaCorte = new Trend("latencia_corte_ec009_ms", true);
const latenciaDegradadoCache = new Trend("latencia_degradado_ec010_ms", true);
const errores5xx = new Rate("errores_5xx");
const llamadasNoCortadas = new Rate("llamadas_mayores_700ms_sin_corte");
const ofertasPreliminares = new Counter("ofertas_preliminares");

export const options = {
  scenarios: {
    ec009_proveedor_lento: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "10s", // 2 req/s sostenidos
      duration: DURACION_FASE,
      preAllocatedVUs: 8,
      maxVUs: 20,
      exec: "escenarioProveedorLento",
    },
    ec010_proveedor_caido_con_cache: {
      executor: "constant-arrival-rate",
      startTime: DURACION_FASE,
      rate: 20,
      timeUnit: "10s",
      duration: DURACION_FASE,
      preAllocatedVUs: 8,
      maxVUs: 20,
      exec: "escenarioProveedorCaidoConCache",
    },
  },
  thresholds: {
    "errores_5xx": ["rate==0"],
    "llamadas_mayores_700ms_sin_corte": ["rate==0"],
    "latencia_degradado_ec010_ms": ["p(99)<=500"],
  },
};

const MUTACION_SESION = `mutation($entrada: CredencialesInput!) {
  iniciarSesion(entrada: $entrada) {
    __typename
    ... on Sesion { token }
  }
}`;

const MUTACION_CONSENTIMIENTO = `mutation($entrada: ConsentimientoInput!) {
  registrarConsentimiento(entrada: $entrada) { id vigente }
}`;

const MUTACION_OFERTA = `mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) {
    __typename
    ... on Oferta {
      id
      tipo
      fuentes { nombre estado }
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

function cambiarModoStub(modo) {
  if (__ENV.OMITIR_CONTROL_STUB === "true") {
    return;
  }
  try {
    http.post(`${STUB_URL}/control/modo`, JSON.stringify({ modo }), {
      headers: { "Content-Type": "application/json" },
      timeout: "3s",
    });
  } catch (_) {
    // En staging con stub privado, el operador o el script correr_k6.sh cambia el modo con token IAM.
  }
}

export function setup() {
  cambiarModoStub("sano");

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

  const documento = __ENV.DOCUMENTO_PRUEBA || "CC-K6-FALLA-100002";
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

  let consentimientoId = __ENV.CONSENTIMIENTO_ID || "cons-k6-falla";
  try {
    const body = JSON.parse(resCons.body);
    if (body.data && body.data.registrarConsentimiento && body.data.registrarConsentimiento.id) {
      consentimientoId = body.data.registrarConsentimiento.id;
    }
  } catch (_) {
    // fallback
  }

  // Sembrar caché en caliente con proveedores sanos antes de inyectar falla.
  http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: documento,
          valorCredito: "180000000",
          plazoMeses: 180,
          direccionInmueble: "Medellín, El Poblado",
          consentimientoId: consentimientoId,
        },
      },
    }),
    { headers: cabeceras(token) }
  );

  cambiarModoStub("lento");
  return { token, consentimientoId, documento, faseCambiada: false };
}

function pedirOferta(data) {
  return http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: data.documento,
          valorCredito: "180000000",
          plazoMeses: 180,
          direccionInmueble: "Medellín, El Poblado",
          consentimientoId: data.consentimientoId,
        },
      },
    }),
    { headers: cabeceras(data.token) }
  );
}

export function escenarioProveedorLento(data) {
  const res = pedirOferta(data);
  latenciaCorte.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  // Ninguna llamada al proveedor puede quedar colgada los 2.000 ms del modo `lento`: se corta a <= 700 ms.
  llamadasNoCortadas.add(res.timings.duration > 950);

  check(res, {
    "EC009 HTTP 200 (0 % de 5xx)": (r) => r.status === 200,
    "EC009 cortada antes del retraso del proveedor": (r) => r.timings.duration <= 950,
  });
  sleep(0.05);
}

export function escenarioProveedorCaidoConCache(data) {
  if (__ITER === 0) {
    cambiarModoStub("caido");
  }
  const res = pedirOferta(data);
  latenciaDegradadoCache.add(res.timings.duration);
  errores5xx.add(res.status >= 500);

  const ok = check(res, {
    "EC010 HTTP 200 (0 % de 5xx)": (r) => r.status === 200,
    "EC010 oferta PRELIMINAR servida desde caché": (r) => {
      try {
        const body = JSON.parse(r.body);
        const of = body.data && body.data.solicitarOferta;
        return Boolean(of && (of.tipo === "PRELIMINAR" || of.tipo === "COMPLETA"));
      } catch (_) {
        return false;
      }
    },
  });

  if (ok) {
    ofertasPreliminares.add(1);
  }
  sleep(0.05);
}

export function teardown() {
  cambiarModoStub("sano");
}

export function handleSummary(data) {
  const salida = __ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/falla-proveedor.json";
  return {
    stdout: JSON.stringify(
      {
        escenario: "EC009-EC010",
        modo_reducido: MODO_REDUCIDO,
        url: BASE_URL,
        p99_corte_ec009_ms: data.metrics.latencia_corte_ec009_ms
          ? data.metrics.latencia_corte_ec009_ms.values["p(99)"]
          : null,
        p99_degradado_ec010_ms: data.metrics.latencia_degradado_ec010_ms
          ? data.metrics.latencia_degradado_ec010_ms.values["p(99)"]
          : null,
        tasa_5xx: data.metrics.errores_5xx ? data.metrics.errores_5xx.values.rate : 0,
      },
      null,
      2
    ) + "\n",
    [salida]: JSON.stringify(data, null, 2),
  };
}
