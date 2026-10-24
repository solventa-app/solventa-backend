// Recorrido de emisión de HU-W03 para los guiones de EC005 y EC007 (plan de HU-W03, bloque 6).
//
// Un CLIENTE hace todo el camino por el BFF, como el front: consentimiento -> oferta -> aceptarOferta ->
// documentoFirma -> firmarPropuesta -> espera a que la propuesta quede EMITIDA. Solo el CLIENTE titular firma
// (decisión W03-8), por eso no se usa un ASESOR como en `preparar.js`.
//
// Lo que se mide (CA-W03-09): "de la firma a EMITIDA". El reloj arranca justo antes de `firmarPropuesta` y se
// detiene en la primera consulta de `propuesta(id)` que ya dice EMITIDA. Esa consulta se repite cada
// `SONDEO_MS`, así que la cifra tiene hasta esa resolución de más: no es un tiempo interno de POLICY.
// Todo lo anterior (oferta, aceptación, documento) se mide aparte y NO cuenta para el umbral.
//
// Variables de entorno (valores por defecto SOLO LOCALES, no secretos):
//   USUARIO_BACKOFFICE / CLAVE_BACKOFFICE   admin.solventa / admin-local-solventa (crean los CLIENTES de prueba)
//   CLIENTES_PRUEBA                         cuántos CLIENTES crear (por defecto 20)
//   SONDEO_MS                               cada cuánto se consulta la propuesta (por defecto 100)
//   TIMEOUT_EMISION_MS                      cuánto se espera la emisión antes de darla por fallida (por defecto 10000)

import { sleep } from "k6";
import { graphql, pedirOferta } from "./preparar.js";

export const SONDEO_MS = Number(__ENV.SONDEO_MS || 100);
export const TIMEOUT_EMISION_MS = Number(__ENV.TIMEOUT_EMISION_MS || 10000);

const SESION = `mutation($e: CredencialesInput!) {
  iniciarSesion(entrada: $e) { __typename ... on Sesion { token } ... on ErrorAutenticacion { mensaje } }
}`;

const CREAR_USUARIO = `mutation($e: CrearUsuarioInput!) {
  crearUsuario(entrada: $e) { __typename ... on Usuario { id } ... on ErrorUsuario { mensaje } }
}`;

const CONSENTIMIENTO = `mutation($e: ConsentimientoInput!) {
  registrarConsentimiento(entrada: $e) { id vigente }
}`;

const ACEPTAR = `mutation($id: ID!) {
  aceptarOferta(ofertaId: $id) {
    __typename
    ... on Propuesta { id estado }
    ... on ErrorAceptacion { mensaje puedeReintentar motivo }
  }
}`;

const DOCUMENTO = `query($id: ID!) { documentoFirma(propuestaId: $id) { propuestaId version } }`;

const FIRMAR = `mutation($e: FirmarPropuestaInput!) {
  firmarPropuesta(entrada: $e) {
    __typename
    ... on FirmaRegistrada { propuestaId estado }
    ... on ErrorFirma { mensaje puedeReintentar motivo }
  }
}`;

const PROPUESTA = `query($id: ID!) { propuesta(id: $id) { id estado polizaId } }`;

/**
 * Un documento nuevo por recorrido (cada uno necesita su consentimiento y su oferta). El BFF exige 6 dígitos o más
 * y 20 caracteres como máximo: `<prefijo de 2>-<7 dígitos de la marca>-<VU>-<iteración>` llega a 20 con 3 dígitos
 * de VU y 5 de iteración.
 */
export function documentoDePrueba(prefijo, marca) {
  return `${prefijo}-${String(marca % 10000000).padStart(7, "0")}-${__VU}-${__ITER}`;
}

function sufijoAleatorio() {
  const letras = "abcdefghijklmnopqrstuvwxyz";
  let s = "";
  for (let i = 0; i < 8; i++) {
    s += letras[Math.floor(Math.random() * letras.length)];
  }
  return s;
}

function exigir(condicion, mensaje) {
  if (!condicion) {
    throw new Error(`Preparación de k6 fallida: ${mensaje}`);
  }
}

/** Crea `cantidad` CLIENTES con el BACKOFFICE y devuelve sus tokens. Aborta la corrida si algo falla. */
export function prepararClientes(graphqlUrl, cantidad) {
  const backoffice = graphql(graphqlUrl, SESION, {
    e: {
      usuario: __ENV.USUARIO_BACKOFFICE || "admin.solventa",
      clave: __ENV.CLAVE_BACKOFFICE || "admin-local-solventa",
    },
  });
  const tokenBackoffice =
    backoffice.cuerpo && backoffice.cuerpo.data && backoffice.cuerpo.data.iniciarSesion.token;
  exigir(tokenBackoffice, "no se pudo iniciar sesión como BACKOFFICE");

  const clientes = [];
  for (let i = 0; i < cantidad; i++) {
    const alias = `k6.cliente.${sufijoAleatorio()}`;
    const clave = "clave-k6-0001";
    const alta = graphql(
      graphqlUrl,
      CREAR_USUARIO,
      { e: { usuario: alias, clave, rol: "CLIENTE", nombre: `Cliente k6 ${i + 1}` } },
      tokenBackoffice
    );
    exigir(
      alta.cuerpo && alta.cuerpo.data && alta.cuerpo.data.crearUsuario.__typename === "Usuario",
      "no se pudo crear un CLIENTE de prueba"
    );
    const sesion = graphql(graphqlUrl, SESION, { e: { usuario: alias, clave } });
    const token = sesion.cuerpo && sesion.cuerpo.data && sesion.cuerpo.data.iniciarSesion.token;
    exigir(token, "no se pudo iniciar sesión como un CLIENTE de prueba");
    clientes.push({ token });
  }
  return clientes;
}

function datos(cuerpo, campo) {
  return cuerpo && cuerpo.data ? cuerpo.data[campo] : null;
}

/**
 * Un recorrido completo. Devuelve:
 *   `previa`    { cinco_xx, ok }  consentimiento, oferta, aceptación y documento (no cuentan para EC005/EC007)
 *   `emision`   { cinco_xx, ok, ms, estado }  solo si llegó a firmar; `ms` es de la firma a EMITIDA (o hasta el
 *               corte por `TIMEOUT_EMISION_MS`); `estado` es el último estado visto de la propuesta
 * Un fallo antes de firmar devuelve `emision: null`: no hay muestra de emisión y la corrida lo cuenta aparte.
 */
export function recorrerEmision(graphqlUrl, cliente, documento) {
  const previa = { cinco_xx: false, ok: false };
  const huboCincoXx = (res) => res.status >= 500;

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
    cliente.token
  );
  previa.cinco_xx = huboCincoXx(consentimiento.res);
  const registrado = datos(consentimiento.cuerpo, "registrarConsentimiento");
  if (!registrado || !registrado.vigente) {
    return { previa, emision: null };
  }

  const pedida = pedirOferta(graphqlUrl, { token: cliente.token, consentimientoId: registrado.id, documento });
  previa.cinco_xx = previa.cinco_xx || huboCincoXx(pedida.res);
  // Solo una oferta COMPLETA se puede aceptar (CA-W03-01).
  if (!pedida.oferta || pedida.oferta.__typename !== "Oferta" || pedida.oferta.tipo !== "COMPLETA") {
    return { previa, emision: null };
  }

  const aceptada = graphql(graphqlUrl, ACEPTAR, { id: pedida.oferta.id }, cliente.token);
  previa.cinco_xx = previa.cinco_xx || huboCincoXx(aceptada.res);
  const propuesta = datos(aceptada.cuerpo, "aceptarOferta");
  if (!propuesta || propuesta.__typename !== "Propuesta") {
    return { previa, emision: null };
  }

  const documentoFirma = graphql(graphqlUrl, DOCUMENTO, { id: propuesta.id }, cliente.token);
  previa.cinco_xx = previa.cinco_xx || huboCincoXx(documentoFirma.res);
  const aFirmar = datos(documentoFirma.cuerpo, "documentoFirma");
  if (!aFirmar) {
    return { previa, emision: null };
  }
  previa.ok = true;

  // ---- Lo que cuenta: de la firma a EMITIDA ----
  const inicio = Date.now();
  const firma = graphql(
    graphqlUrl,
    FIRMAR,
    { e: { propuestaId: propuesta.id, autorizaCobro: true, versionDocumento: aFirmar.version } },
    cliente.token
  );
  const emision = { cinco_xx: huboCincoXx(firma.res), ok: false, ms: 0, estado: null };
  const registrada = datos(firma.cuerpo, "firmarPropuesta");
  if (!registrada || registrada.__typename !== "FirmaRegistrada") {
    emision.ms = Date.now() - inicio;
    emision.estado = "FIRMA_RECHAZADA";
    return { previa, emision };
  }

  emision.estado = registrada.estado;
  while (Date.now() - inicio < TIMEOUT_EMISION_MS) {
    if (emision.estado === "EMITIDA") {
      emision.ok = true;
      break;
    }
    // Estados que ya no cambian solos en este recorrido: seguir esperando solo gastaría tiempo.
    if (["EN_REVISION", "NO_EMITIDA", "PAGO_RECHAZADO"].includes(emision.estado)) {
      break;
    }
    sleep(SONDEO_MS / 1000);
    const consulta = graphql(graphqlUrl, PROPUESTA, { id: propuesta.id }, cliente.token);
    emision.cinco_xx = emision.cinco_xx || huboCincoXx(consulta.res);
    const actual = datos(consulta.cuerpo, "propuesta");
    if (actual) {
      emision.estado = actual.estado;
    }
  }
  emision.ms = Date.now() - inicio;
  return { previa, emision };
}
