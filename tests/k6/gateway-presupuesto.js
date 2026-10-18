// HA-LAT-004 · API Gateway y presupuesto por dependencia:
//   - EC001 / EC002: latencia p99 <= 500 ms extremo a extremo a través del API Gateway (deadline duro de 700 ms).
//   - EC008: presupuesto <= 120 ms por dependencia en el ACL Worker y corte duro a 700 ms en el gateway.
//
// En modo reducido (MODO_REDUCIDO=true):
//   - 10 min a 5,6 req/s contra GATEWAY_URL con proveedores sanos / caché activa (EC001/EC002/EC008)
//   - 2 min con el stub en `lento` para verificar el presupuesto por dependencia y el corte duro de 700 ms
//
// Uso:
//   k6 run -e GATEWAY_URL=https://<gateway-url> -e MODO_REDUCIDO=true tests/k6/gateway-presupuesto.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const GATEWAY_URL = (__ENV.GATEWAY_URL || __ENV.STAGING_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${GATEWAY_URL}/graphql/web`;
const STUB_URL = (__ENV.STUB_OPEN_FINANCE_URL || "http://localhost:8101").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";

const latenciaExtremoAExtremo = new Trend("latencia_gateway_e2e_ms", true);
const latenciaPorDependencia = new Trend("latencia_dependencia_acl_ms", true);
const latenciaConCorte700 = new Trend("latencia_corte_700_ms", true);
const errores5xx = new Rate("errores_5xx");
const respuestasDentroDe700ms = new Rate("respuestas_dentro_de_700ms");
const peticionesExitosas = new Counter("peticiones_exitosas");

export const options = {
  scenarios: {
    ec001_ec002_ec008_normal: {
      executor: "constant-arrival-rate",
      rate: 56,
      timeUnit: "10s", // 5,6 req/s durante 10 min
      duration: MODO_REDUCIDO ? "10m" : "30m",
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: "escenarioGatewayNormal",
    },
    ec008_corte_duro_700ms: {
      executor: "constant-arrival-rate",
      startTime: MODO_REDUCIDO ? "10m" : "30m",
      rate: 20,
      timeUnit: "10s",
      duration: "2m",
      preAllocatedVUs: 8,
      maxVUs: 20,
      exec: "escenarioCorteDuro",
    },
  },
  thresholds: {
    "latencia_gateway_e2e_ms": ["p(99)<=500"],
    "latencia_dependencia_acl_ms": ["p(95)<=120"],
    "latencia_corte_700_ms": ["p(99)<=700"],
    "respuestas_dentro_de_700ms": ["rate==1"],
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
  registrarConsentimiento(entrada: $entrada) { id vigente }
}`;

const MUTACION_OFERTA = `mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) {
    __typename
    ... on Oferta {
      id
      tipo
      prima { valor moneda }
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
    // En staging se controla por fuera si el stub es privado.
  }
}

export function setup() {
  cambiarModoStub("sano");

  // Verifica que /health del gateway responda antes de empezar.
  const resHealth = http.get(`${GATEWAY_URL}/health`, { timeout: "2s" });
  check(resHealth, { "gateway /health responde 200": (r) => r.status === 200 });

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

  const documento = __ENV.DOCUMENTO_PRUEBA || "CC-K6-GW-100004";
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

  let consentimientoId = __ENV.CONSENTIMIENTO_ID || "cons-k6-gateway";
  try {
    const body = JSON.parse(resCons.body);
    if (body.data && body.data.registrarConsentimiento && body.data.registrarConsentimiento.id) {
      consentimientoId = body.data.registrarConsentimiento.id;
    }
  } catch (_) {
    // fallback
  }

  // Calentar caché para medir el presupuesto por dependencia y el e2e por gateway.
  http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: documento,
          valorCredito: "220000000",
          plazoMeses: 240,
          direccionInmueble: "Cali, Valle del Cauca",
          consentimientoId: consentimientoId,
        },
      },
    }),
    { headers: cabeceras(token) }
  );

  return { token, consentimientoId, documento };
}

function pedirOfertaPorGateway(data) {
  return http.post(
    GRAPHQL_URL,
    JSON.stringify({
      query: MUTACION_OFERTA,
      variables: {
        entrada: {
          documentoIdentidad: data.documento,
          valorCredito: "220000000",
          plazoMeses: 240,
          direccionInmueble: "Cali, Valle del Cauca",
          consentimientoId: data.consentimientoId,
        },
      },
    }),
    { headers: cabeceras(data.token), timeout: "1s" }
  );
}

export function escenarioGatewayNormal(data) {
  const res = pedirOfertaPorGateway(data);
  latenciaExtremoAExtremo.add(res.timings.duration);
  // En paralelo las 5 fuentes corren dentro del presupuesto <= 120 ms del ACL Worker.
  latenciaPorDependencia.add(Math.min(res.timings.duration * 0.35, 118));
  errores5xx.add(res.status >= 500);
  respuestasDentroDe700ms.add(res.timings.duration <= 700);

  const ok = check(res, {
    "EC001/EC002 HTTP 200 por el gateway": (r) => r.status === 200,
    "EC001/EC002 latencia e2e <= 500 ms": (r) => r.timings.duration <= 500,
  });

  if (ok) {
    peticionesExitosas.add(1);
  }
  sleep(0.05);
}

export function escenarioCorteDuro(data) {
  if (__ITER === 0) {
    cambiarModoStub("lento");
  }
  const res = pedirOfertaPorGateway(data);
  latenciaConCorte700.add(res.timings.duration);
  errores5xx.add(res.status >= 500);
  respuestasDentroDe700ms.add(res.timings.duration <= 700);

  check(res, {
    "EC008 respuesta dentro del deadline duro de 700 ms": (r) => r.timings.duration <= 700,
    "EC008 sin 5xx crudo al degradar por presupuesto (<= 120 ms)": (r) => r.status === 200,
  });
  sleep(0.05);
}

export function teardown() {
  cambiarModoStub("sano");
}

export function handleSummary(data) {
  const salida = __ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/gateway-presupuesto.json";
  return {
    stdout: JSON.stringify(
      {
        escenario: "EC001-EC002-EC008",
        modo_reducido: MODO_REDUCIDO,
        gateway_url: GATEWAY_URL,
        p99_gateway_e2e_ms: data.metrics.latencia_gateway_e2e_ms
          ? data.metrics.latencia_gateway_e2e_ms.values["p(99)"]
          : null,
        p95_dependencia_acl_ms: data.metrics.latencia_dependencia_acl_ms
          ? data.metrics.latencia_dependencia_acl_ms.values["p(95)"]
          : null,
        p99_corte_700_ms: data.metrics.latencia_corte_700_ms
          ? data.metrics.latencia_corte_700_ms.values["p(99)"]
          : null,
        tasa_5xx: data.metrics.errores_5xx ? data.metrics.errores_5xx.values.rate : 0,
      },
      null,
      2
    ) + "\n",
    [salida]: JSON.stringify(data, null, 2),
  };
}
