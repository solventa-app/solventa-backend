// Preparación común de los guiones de k6 (se corre una vez, en `setup()`).
//
// Un BACKOFFICE no puede registrar consentimientos ni pedir ofertas (matriz de permisos de
// docs/autenticacion-usuarios.md): por eso se crea un ASESOR de prueba con el BACKOFFICE y con ese
// ASESOR se registra el consentimiento y se piden las ofertas. Si algo falla, se aborta la corrida:
// medir respuestas de error con HTTP 200 daría números que no significan nada.
//
// Variables de entorno (valores por defecto SOLO LOCALES, no secretos):
//   AUTH_TOKEN + CONSENTIMIENTO_ID   usar un ASESOR y un consentimiento que ya existen (staging)
//   USUARIO_BACKOFFICE / CLAVE_BACKOFFICE   admin.solventa / admin-local-solventa

import http from "k6/http";

const SESION = `mutation($e: CredencialesInput!) {
  iniciarSesion(entrada: $e) { __typename ... on Sesion { token } ... on ErrorAutenticacion { mensaje } }
}`;

const CREAR_USUARIO = `mutation($e: CrearUsuarioInput!) {
  crearUsuario(entrada: $e) { __typename ... on Usuario { id } ... on ErrorUsuario { mensaje } }
}`;

const CONSENTIMIENTO = `mutation($e: ConsentimientoInput!) {
  registrarConsentimiento(entrada: $e) { id vigente }
}`;

export const OFERTA = `mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) {
    __typename
    ... on Oferta { id tipo prima { valor moneda } fuentes { nombre estado } }
    ... on ErrorOferta { mensaje puedeReintentar motivo }
  }
}`;

export function cabeceras(token) {
  const h = { "Content-Type": "application/json" };
  if (token) {
    h["Authorization"] = `Bearer ${token}`;
  }
  return h;
}

export function graphql(url, query, variables, token, parametros) {
  const res = http.post(url, JSON.stringify({ query, variables }), {
    headers: cabeceras(token),
    ...(parametros || {}),
  });
  let cuerpo = null;
  try {
    cuerpo = JSON.parse(res.body);
  } catch (_) {
    cuerpo = null;
  }
  return { res, cuerpo };
}

function exigir(condicion, mensaje) {
  if (!condicion) {
    throw new Error(`Preparación de k6 fallida: ${mensaje}`);
  }
}

function sufijoAleatorio() {
  const letras = "abcdefghijklmnopqrstuvwxyz";
  let s = "";
  for (let i = 0; i < 8; i++) {
    s += letras[Math.floor(Math.random() * letras.length)];
  }
  return s;
}

/** Devuelve `{ token, consentimientoId, documento }` de un ASESOR de prueba. */
export function prepararSesion(graphqlUrl, documento) {
  if (__ENV.AUTH_TOKEN && __ENV.CONSENTIMIENTO_ID) {
    return { token: __ENV.AUTH_TOKEN, consentimientoId: __ENV.CONSENTIMIENTO_ID, documento };
  }

  const backoffice = graphql(graphqlUrl, SESION, {
    e: {
      usuario: __ENV.USUARIO_BACKOFFICE || "admin.solventa",
      clave: __ENV.CLAVE_BACKOFFICE || "admin-local-solventa",
    },
  });
  const tokenBackoffice =
    backoffice.cuerpo && backoffice.cuerpo.data && backoffice.cuerpo.data.iniciarSesion.token;
  exigir(tokenBackoffice, "no se pudo iniciar sesión como BACKOFFICE");

  const alias = `k6.${sufijoAleatorio()}`;
  const clave = "clave-k6-0001";
  const alta = graphql(
    graphqlUrl,
    CREAR_USUARIO,
    { e: { usuario: alias, clave, rol: "ASESOR" } },
    tokenBackoffice
  );
  exigir(
    alta.cuerpo && alta.cuerpo.data && alta.cuerpo.data.crearUsuario.__typename === "Usuario",
    "no se pudo crear el ASESOR de prueba"
  );

  const asesor = graphql(graphqlUrl, SESION, { e: { usuario: alias, clave } });
  const token = asesor.cuerpo && asesor.cuerpo.data && asesor.cuerpo.data.iniciarSesion.token;
  exigir(token, "no se pudo iniciar sesión como el ASESOR de prueba");

  const consentimiento = graphql(
    graphqlUrl,
    CONSENTIMIENTO,
    {
      e: {
        finalidad: "cotizacion-vida-hipotecario",
        versionTexto: "v1",
        documentoIdentidad: documento,
        tipoDato: "financiero",
        fuenteDatos: "open-finance",
        canal: "web",
      },
    },
    token
  );
  const registrado =
    consentimiento.cuerpo && consentimiento.cuerpo.data && consentimiento.cuerpo.data.registrarConsentimiento;
  exigir(registrado && registrado.vigente, "no se pudo registrar el consentimiento");

  return { token, consentimientoId: registrado.id, documento };
}

/** Pide una oferta y devuelve `{ res, oferta }` (`oferta` es `null` si la respuesta no es GraphQL). */
export function pedirOferta(graphqlUrl, sesion, extra, parametros) {
  const { res, cuerpo } = graphql(
    graphqlUrl,
    OFERTA,
    {
      entrada: {
        documentoIdentidad: sesion.documento,
        valorCredito: "200000000",
        plazoMeses: 60,
        direccionInmueble: "Bogotá D.C., Chapinero",
        consentimientoId: sesion.consentimientoId,
        ...(extra || {}),
      },
    },
    sesion.token,
    parametros
  );
  const oferta = cuerpo && cuerpo.data ? cuerpo.data.solicitarOferta : null;
  return { res, oferta };
}

/** Una oferta válida: `Oferta` (completa o preliminar). Un `ErrorOferta` no cuenta como éxito. */
export function esOferta(oferta) {
  return Boolean(oferta && oferta.__typename === "Oferta");
}
