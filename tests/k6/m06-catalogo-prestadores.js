// HU-M06 (KAN-263) · EC011 / EC012 sobre el catálogo maestro de prestadores: CLAIMS lo lee de la SECUNDARIA de
// MongoDB (HA-LAT-003). Umbrales del escenario: lectura de la réplica p95 <= 150 ms y p99 <= 300 ms.
//
// Mide, cada cosa en su métrica:
//   - `latencia_lectura_replica_ms`: CLAIMS directo (`GET /prestadores`), sin versión (lista completa) y con la
//     versión vigente (respuesta mínima), mitad y mitad. Es la que se compara con EC011/EC012.
//   - `latencia_catalogo_gateway_ms`: `catalogoPrestadores` por el gateway (`/v1/graphql/app`) con sesión de
//     CLIENTE, lo que vive la app. Solo informativa.
//
// La carga real esperada es mínima (una consulta por cliente por semana, docs de la app móvil): el ritmo por
// defecto ya es muy superior. El setup crea un CLIENTE de prueba con el BACKOFFICE local.
//
// Uso:
//   k6 run -e CLAIMS_URL=http://localhost:8012 -e GATEWAY_URL=http://localhost:8080 tests/k6/m06-catalogo-prestadores.js

import http from "k6/http";
import { check } from "k6";
import { Trend } from "k6/metrics";

const CLAIMS_URL = (__ENV.CLAIMS_URL || "http://localhost:8012").replace(/\/$/, "");
const GATEWAY_URL = (__ENV.GATEWAY_URL || "http://localhost:8080").replace(/\/$/, "");
const DURACION = __ENV.DURACION_NORMAL || "2m";
const RITMO = Number(__ENV.RITMO || 10);
const CATALOGO =
  "query($v: String) { catalogoPrestadores(versionLocal: $v) { version vigente prestadores { id tipo latitud longitud activo } } }";

const latenciaReplica = new Trend("latencia_lectura_replica_ms", true);
const latenciaGateway = new Trend("latencia_catalogo_gateway_ms", true);

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
      exec: "catalogoGateway",
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

function graphql(ruta, query, variables, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return http.post(`${GATEWAY_URL}${ruta}`, JSON.stringify({ query, variables }), { headers });
}

const LOGIN = "mutation($e: CredencialesInput!) { iniciarSesion(entrada: $e) { ... on Sesion { token } } }";

export function setup() {
  const admin = graphql("/v1/graphql/web", LOGIN, {
    e: {
      usuario: __ENV.USUARIO_BACKOFFICE || "admin.solventa",
      clave: __ENV.CLAVE_BACKOFFICE || "Admin-local-solventa",
    },
  }).json("data.iniciarSesion.token");
  if (!admin) throw new Error("No se pudo iniciar sesión como BACKOFFICE: el guion no inventa un resultado");

  // Solo letras en el alias: AUTH rechaza un alias que parezca un número de documento.
  const sufijo = `${Date.now()}`;
  const letras = [...sufijo].map((d) => "abcdefghij"[Number(d)]).join("");
  const clave = "Clave-k6-m06-0001";
  const usuario = `cliente.k6.m06.${letras}`;
  const creado = graphql(
    "/v1/graphql/web",
    "mutation($e: CrearUsuarioInput!) { crearUsuario(entrada: $e) { __typename } }",
    {
      e: {
        usuario,
        clave,
        rol: "CLIENTE",
        nombre: "Cliente k6 M06",
        correo: `${usuario}@example.com`,
        documentoIdentidad: sufijo.slice(-10),
      },
    },
    admin,
  ).json("data.crearUsuario.__typename");
  if (creado !== "Usuario") throw new Error(`No se pudo crear el CLIENTE de prueba (${creado})`);

  const token = graphql("/v1/graphql/app", LOGIN, { e: { usuario, clave } }).json("data.iniciarSesion.token");
  if (!token) throw new Error("No se pudo iniciar sesión como CLIENTE");
  const version = http.get(`${CLAIMS_URL}/prestadores`).json("version");
  if (!version) throw new Error("CLAIMS no tiene catálogo de prestadores (¿se corrieron las migraciones?)");
  return { token, version };
}

export function lecturaReplica(datos) {
  const vigente = Math.random() < 0.5;
  const url = vigente
    ? `${CLAIMS_URL}/prestadores?version_local=${encodeURIComponent(datos.version)}`
    : `${CLAIMS_URL}/prestadores`;
  const r = http.get(url, { tags: { lectura: vigente ? "vigente" : "completa" } });
  latenciaReplica.add(r.timings.duration);
  check(r, {
    "CLAIMS 200": (x) => x.status === 200,
    "respuesta coherente": (x) => (x.json("vigente") ? x.json("prestadores").length === 0 : x.json("prestadores").length > 0),
  });
}

export function catalogoGateway(datos) {
  const vigente = Math.random() < 0.5;
  const r = graphql("/v1/graphql/app", CATALOGO, { v: vigente ? datos.version : null }, datos.token);
  latenciaGateway.add(r.timings.duration);
  check(r, {
    "gateway 200": (x) => x.status === 200,
    "sin errores": (x) => !x.json("errors"),
  });
}
