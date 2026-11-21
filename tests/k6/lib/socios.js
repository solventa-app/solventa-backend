// Preparación común de los guiones de la API de socios (`/v1`, HU-W07): token ES256, consentimiento
// y cotización. Con un token de PRUEBAS el BFF responde el modo prueba (W07-10): datos simulados, sin
// consultar fuentes, pero la reserva del presupuesto en AUTH es real. Las claves llegan por variables
// de entorno; las de por defecto son las SOLO LOCALES de los socios de k6 (no secretas).

import http from "k6/http";
import { fail } from "k6";

export const BASE_URL = (__ENV.BFF_URL || __ENV.GATEWAY_URL || "http://localhost:8000").replace(/\/$/, "");
export const DOCUMENTO = __ENV.DOCUMENTO_TITULAR || "1000000001"; // modo prueba: oferta COMPLETA

const JSON_ = { "Content-Type": "application/json" };

export function credencial(n, entorno = "PRUEBAS") {
  const sufijo = entorno === "PRUEBAS" ? "test" : "prod";
  const nombre = entorno === "PRUEBAS" ? "pruebas" : "produccion";
  return {
    clientId: __ENV[`SOCIO_${n}_CLIENT_ID`] || `sol_${sufijo}_k6_${n}`,
    clave: __ENV[`SOCIO_${n}_CLAVE`] || `solo-local-k6-${n}-${nombre}`,
  };
}

export function token({ clientId, clave }) {
  const r = http.post(`${BASE_URL}/v1/token`, JSON.stringify({ clientId, clave }), { headers: JSON_ });
  if (r.status !== 200) fail(`token de ${clientId}: HTTP ${r.status}`);
  return r.json("accessToken");
}

export function cabeceras(tok, extra = {}) {
  return { ...JSON_, Authorization: `Bearer ${tok}`, ...extra };
}

export function consentimiento(tok, documento = DOCUMENTO) {
  const r = http.post(
    `${BASE_URL}/v1/consentimientos`,
    JSON.stringify({ documentoIdentidad: documento, finalidad: "cotizacion", versionTexto: "v1" }),
    { headers: cabeceras(tok) },
  );
  if (r.status !== 201) fail(`consentimiento: HTTP ${r.status} ${r.body}`);
  return r.json("id");
}

export function cotizar(tok, consentimientoId, etiquetas = {}, documento = DOCUMENTO) {
  return http.post(
    `${BASE_URL}/v1/cotizaciones`,
    JSON.stringify({
      consentimientoId,
      documentoIdentidad: documento,
      valorCredito: "150000000",
      plazoMeses: 60,
      direccionInmueble: "Calle 1 # 2-3",
    }),
    { headers: cabeceras(tok), tags: { name: "POST /v1/cotizaciones", ...etiquetas } },
  );
}

// Una sesión por socio para la corrida: el token vive 15 minutos, así que la corrida no debe durar más.
export function prepararSocio(n, entorno = "PRUEBAS") {
  const tok = token(credencial(n, entorno));
  return { n, token: tok, consentimientoId: consentimiento(tok) };
}
