// Recorrido de emisión de HU-W03 para los guiones de EC005 y EC007 (plan de HU-W03, bloque 6; F-B9 con el
// contrato v0.18).
//
// Un CLIENTE hace todo el camino por el BFF, como el front (flujo con sesión, W03-33): consentimiento -> oferta ->
// aceptarOferta -> emitirPropuesta («Firmar», UNDER y KYC) -> firmaPorEnlace -> firmarPorEnlace (modo NOMBRE) ->
// iniciarPago -> paga en la página de la pasarela de prueba -> confirmarPago, que devuelve la Poliza EMITIDA.
//
// Lo que se mide (CA-W03-07, F-B9): «desde el regreso de la pasarela». El reloj arranca justo antes de
// `confirmarPago` (lo que hace la web al volver a `/pago/retorno/...`) y se detiene con su respuesta: concilia el
// pago con la pasarela, registra el cobro y emite la póliza en la misma llamada. Todo lo anterior se mide aparte y
// NO cuenta para el umbral.
//
// Variables de entorno (valores por defecto SOLO LOCALES, no secretos):
//   USUARIO_BACKOFFICE / CLAVE_BACKOFFICE   admin.solventa / admin-local-solventa (crean los CLIENTES de prueba)
//   CLIENTES_PRUEBA                         cuántos CLIENTES crear (por defecto 20)
//   STUB_PASARELA_URL                       la pasarela de prueba donde se paga (por defecto http://localhost:8103)

import http from "k6/http";
import { graphql, pedirOferta } from "./preparar.js";

const PASARELA_URL = (__ENV.STUB_PASARELA_URL || "http://localhost:8103").replace(/\/$/, "");
// Tarjeta de prueba que la pasarela aprueba (`stubs/pasarela/README.md`).
const TARJETA_APROBADA = { numero: "4242424242424242", nombre: "Cliente k6", vencimiento: "12/30", cvv: "123", tipo: "CREDITO" };

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

const EMITIR = `mutation($p: ID!) {
  emitirPropuesta(propuestaId: $p) { __typename ... on Propuesta { estado } ... on ErrorEmision { mensaje } }
}`;

const PAGINA = `query($p: ID) { firmaPorEnlace(propuestaId: $p) { vigente documento { version } } }`;

const FIRMAR = `mutation($e: FirmarPorEnlaceInput!) {
  firmarPorEnlace(entrada: $e) {
    __typename
    ... on FirmaRegistrada { estado }
    ... on ErrorFirma { mensaje motivo }
  }
}`;

const INICIAR_PAGO = `mutation($p: ID) {
  iniciarPago(propuestaId: $p) { __typename ... on SesionPago { sesionId } ... on ErrorPago { motivo } }
}`;

const CONFIRMAR_PAGO = `mutation($s: ID!, $p: ID) {
  confirmarPago(sesionId: $s, propuestaId: $p) {
    __typename
    ... on Poliza { id estado }
    ... on ErrorPago { motivo fallosSeguidos }
  }
}`;

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

/**
 * Crea `cantidad` CLIENTES con el BACKOFFICE y devuelve sus tokens. Aborta la corrida si algo falla. Cada CLIENTE
 * necesita su documento y su correo (W03-27) y cotiza con el documento de su cuenta (W03-34, F-B3): solo dígitos y
 * distinto por corrida (`marca`).
 */
export function prepararClientes(graphqlUrl, cantidad, marca) {
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
    const sufijo = sufijoAleatorio();
    const alias = `k6.cliente.${sufijo}`;
    const clave = "clave-k6-0001";
    const documento = `7${String(marca % 10000000).padStart(7, "0")}${String(i).padStart(3, "0")}`;
    const alta = graphql(
      graphqlUrl,
      CREAR_USUARIO,
      {
        e: {
          usuario: alias,
          clave,
          rol: "CLIENTE",
          nombre: `Cliente k6 ${i + 1}`,
          correo: `k6.${sufijo}@ejemplo.co`,
          documentoIdentidad: documento,
        },
      },
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
 *   `previa`    { cinco_xx, ok }  de la oferta al pago en la pasarela (no cuenta para EC005/EC007)
 *   `emision`   { cinco_xx, ok, ms, estado }  solo si llegó a pagar; `ms` es lo que tardó `confirmarPago`;
 *               `estado` es EMITIDA o el motivo del `ErrorPago`
 * Un fallo antes de pagar devuelve `emision: null`: no hay muestra de emisión y la corrida lo cuenta aparte.
 */
export function recorrerEmision(graphqlUrl, cliente) {
  const previa = { cinco_xx: false, ok: false };
  const huboCincoXx = (res) => res.status >= 500;
  const paso = (consulta, variables, campo) => {
    const r = graphql(graphqlUrl, consulta, variables, cliente.token);
    previa.cinco_xx = previa.cinco_xx || huboCincoXx(r.res);
    return datos(r.cuerpo, campo);
  };

  // Sin documento: el CLIENTE consiente y cotiza con el de su cuenta.
  const registrado = paso(
    CONSENTIMIENTO,
    { e: { finalidad: "cotizacion-vida-hipotecario", versionTexto: "v1", tipoDato: "financiero", fuenteDatos: "open-finance", canal: "web" } },
    "registrarConsentimiento"
  );
  if (!registrado || !registrado.vigente) {
    return { previa, emision: null };
  }

  const pedida = pedirOferta(graphqlUrl, { token: cliente.token, consentimientoId: registrado.id, documento: null });
  previa.cinco_xx = previa.cinco_xx || huboCincoXx(pedida.res);
  // Solo una oferta COMPLETA se puede aceptar (CA-W03-01).
  if (!pedida.oferta || pedida.oferta.__typename !== "Oferta" || pedida.oferta.tipo !== "COMPLETA") {
    return { previa, emision: null };
  }

  const propuesta = paso(ACEPTAR, { id: pedida.oferta.id }, "aceptarOferta");
  if (!propuesta || propuesta.__typename !== "Propuesta") {
    return { previa, emision: null };
  }
  const emitida = paso(EMITIR, { p: propuesta.id }, "emitirPropuesta");
  if (!emitida || emitida.estado !== "EN_FIRMA") {
    return { previa, emision: null };
  }
  const pagina = paso(PAGINA, { p: propuesta.id }, "firmaPorEnlace");
  if (!pagina || !pagina.vigente) {
    return { previa, emision: null };
  }
  const firma = paso(
    FIRMAR,
    {
      e: {
        propuestaId: propuesta.id,
        versionDocumento: pagina.documento.version,
        aceptaTerminos: true,
        autorizaCobro: true,
        modo: "NOMBRE",
        nombreFirma: "Cliente k6",
      },
    },
    "firmarPorEnlace"
  );
  if (!firma || firma.__typename !== "FirmaRegistrada") {
    return { previa, emision: null };
  }
  const sesion = paso(INICIAR_PAGO, { p: propuesta.id }, "iniciarPago");
  if (!sesion || sesion.__typename !== "SesionPago") {
    return { previa, emision: null };
  }
  const pago = http.post(`${PASARELA_URL}/checkout/${sesion.sesionId}/pagar`, JSON.stringify(TARJETA_APROBADA), {
    headers: { "Content-Type": "application/json" },
    tags: { name: "pasarela_pagar" },
  });
  if (pago.status !== 200) {
    previa.cinco_xx = previa.cinco_xx || huboCincoXx(pago);
    return { previa, emision: null };
  }
  previa.ok = true;

  // ---- Lo que cuenta: del regreso de la pasarela a la póliza EMITIDA ----
  const inicio = Date.now();
  const confirmacion = graphql(graphqlUrl, CONFIRMAR_PAGO, { s: sesion.sesionId, p: propuesta.id }, cliente.token);
  const emision = { cinco_xx: huboCincoXx(confirmacion.res), ok: false, ms: Date.now() - inicio, estado: null };
  const resultado = datos(confirmacion.cuerpo, "confirmarPago");
  if (resultado && resultado.__typename === "Poliza") {
    emision.estado = resultado.estado;
    emision.ok = resultado.estado === "EMITIDA";
  } else {
    emision.estado = (resultado && resultado.motivo) || "SIN_RESPUESTA";
  }
  return { previa, emision };
}
