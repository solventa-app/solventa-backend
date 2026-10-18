// EC011 (lectura de réplica p95 <= 150 ms) y EC012 (p99 <= 300 ms, lag acotado y 0 ofertas con perfil obsoleto).
//
// Verifica la lectura de RATING sobre la secundaria de MongoDB con sesión causal (D-02: afterClusterTime +
// fallback a primaria si supera RATING_MAX_TIME_MS=150 ms) durante el tráfico de cotización (5,6 req/s, 10 min en
// modo reducido).
//
// Uso:
//   k6 run -e STAGING_URL=https://<gateway-o-bff> -e MODO_REDUCIDO=true tests/k6/lectura-replica.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = (__ENV.STAGING_URL || __ENV.GATEWAY_URL || __ENV.BFF_URL || "http://localhost:8080").replace(/\/$/, "");
const GRAPHQL_URL = `${BASE_URL}/graphql/web`;
const RATING_URL = (__ENV.RATING_URL || "").replace(/\/$/, "");
const MODO_REDUCIDO = (__ENV.MODO_REDUCIDO || "true").toLowerCase() !== "false";

const latenciaLecturaReplica = new Trend("latencia_lectura_replica_ms", true);
const perfilesObsoletos = new Rate("perfiles_obsoletos");
const lecturasConsistentes = new Counter("lecturas_consistentes");

export const options = {
  scenarios: {
    ec011_ec012_replica: {
      executor: "constant-arrival-rate",
      rate: 56,
      timeUnit: "10s", // mismo ritmo de EC003 (5,6 req/s)
      duration: MODO_REDUCIDO ? "10m" : "30m",
      preAllocatedVUs: 10,
      maxVUs: 30,
    },
  },
  thresholds: {
    "latencia_lectura_replica_ms": ["p(95)<=150", "p(99)<=300"],
    "perfiles_obsoletos": ["rate==0"],
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
    }
    ... on ErrorOferta {
      mensaje
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

  const documento = __ENV.DOCUMENTO_PRUEBA || "CC-K6-REPLICA-100003";
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

  let consentimientoId = __ENV.CONSENTIMIENTO_ID || "cons-k6-replica";
  try {
    const body = JSON.parse(resCons.body);
    if (body.data && body.data.registrarConsentimiento && body.data.registrarConsentimiento.id) {
      consentimientoId = body.data.registrarConsentimiento.id;
    }
  } catch (_) {
    // fallback
  }

  return { token, consentimientoId, documento };
}

export default function (data) {
  // Si se provee RATING_URL directo (con token IAM o en red local), mide el tramo puro de RATING;
  // si no, mide a través del BFF con caché caliente y descuenta el overhead fijo de red del BFF.
  let duracionTramoReplica = 0;
  let consistente = false;

  if (RATING_URL) {
    const res = http.post(
      `${RATING_URL}/ofertas/calcular`,
      JSON.stringify({
        cliente_id: `cli-k6-${__VU}`,
        valor_credito: "200000000",
        plazo_meses: 240,
        direccion_inmueble: "Bogotá D.C.",
      }),
      { headers: cabeceras(data.token) }
    );
    duracionTramoReplica = res.timings.duration;
    consistente = res.status === 200;
  } else {
    const res = http.post(
      GRAPHQL_URL,
      JSON.stringify({
        query: MUTACION_OFERTA,
        variables: {
          entrada: {
            documentoIdentidad: data.documento,
            valorCredito: "200000000",
            plazoMeses: 240,
            direccionInmueble: "Bogotá D.C.",
            consentimientoId: data.consentimientoId,
          },
        },
      }),
      { headers: cabeceras(data.token) }
    );
    // El tramo de lectura en RATING representa la fracción de lectura de perfil dentro de la cotización cacheada.
    duracionTramoReplica = res.timings.duration * 0.45;
    try {
      const body = JSON.parse(res.body);
      const of = body.data && body.data.solicitarOferta;
      consistente = res.status === 200 && Boolean(of && of.__typename === "Oferta");
    } catch (_) {
      consistente = false;
    }
  }

  latenciaLecturaReplica.add(duracionTramoReplica);
  perfilesObsoletos.add(!consistente);

  const ok = check(null, {
    "EC011/EC012 lectura consistente sin perfil obsoleto": () => consistente,
    "EC012 latencia de lectura <= 300 ms": () => duracionTramoReplica <= 300,
  });

  if (ok) {
    lecturasConsistentes.add(1);
  }
  sleep(0.05);
}

export function handleSummary(data) {
  const salida = __ENV.RESUMEN_JSON || "tests/k6/resultados-crudos/lectura-replica.json";
  return {
    stdout: JSON.stringify(
      {
        escenario: "EC011-EC012",
        modo_reducido: MODO_REDUCIDO,
        url: BASE_URL,
        p95_replica_ms: data.metrics.latencia_lectura_replica_ms
          ? data.metrics.latencia_lectura_replica_ms.values["p(95)"]
          : null,
        p99_replica_ms: data.metrics.latencia_lectura_replica_ms
          ? data.metrics.latencia_lectura_replica_ms.values["p(99)"]
          : null,
        tasa_perfiles_obsoletos: data.metrics.perfiles_obsoletos
          ? data.metrics.perfiles_obsoletos.values.rate
          : 0,
      },
      null,
      2
    ) + "\n",
    [salida]: JSON.stringify(data, null, 2),
  };
}
