// Ayudas compartidas por los guiones de cobros (HU-W05, FC-10). Todo pasa por el BFF, como el front.
//
// Variables de entorno (ninguna clave va en el repo):
//   BFF_URL             URL base (en staging, la del API Gateway; local: http://localhost:8000)
//   USUARIO_BACKOFFICE  alias del operador (local: admin.solventa)
//   CLAVE_BACKOFFICE    su clave (local: admin-local-solventa)
//   STUB_PASARELA_URL   solo para fijar el modo del stub (local: http://localhost:8103)
//   TOKEN_PASARELA      token de identidad para el stub si es privado:
//                       gcloud auth print-identity-token --audiences=stub-pasarela
import http from 'k6/http';
import { fail } from 'k6';

export const BFF = (__ENV.BFF_URL || 'http://localhost:8000') + '/graphql/web';
const STUB = __ENV.STUB_PASARELA_URL || 'http://localhost:8103';

export function graphql(query, variables, token, etiqueta) {
  const cabeceras = { 'Content-Type': 'application/json' };
  if (token) cabeceras.Authorization = `Bearer ${token}`;
  return http.post(BFF, JSON.stringify({ query, variables }), {
    headers: cabeceras,
    tags: { tipo: etiqueta || 'graphql' },
  });
}

export function iniciarSesion() {
  const r = graphql(
    `mutation($e: CredencialesInput!) { iniciarSesion(entrada: $e) {
      __typename ... on Sesion { token } ... on ErrorAutenticacion { mensaje } } }`,
    {
      e: {
        usuario: __ENV.USUARIO_BACKOFFICE || 'admin.solventa',
        clave: __ENV.CLAVE_BACKOFFICE || 'admin-local-solventa',
      },
    },
    null,
    'sesion',
  );
  const sesion = r.json('data.iniciarSesion');
  if (!sesion || sesion.__typename !== 'Sesion') fail('No se pudo iniciar sesión como BACKOFFICE');
  return sesion.token;
}

export const CREAR_COBRO = `mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) {
  __typename ... on Cobro { id estado intentos } ... on ErrorCobro { mensaje } } }`;

export function entradaCobro(idIntentoCobro, polizaId) {
  return {
    idIntentoCobro,
    polizaId,
    monto: { valor: '185000.00', moneda: 'COP' },
    tokenMedioPago: 'tok_k6_prueba',
  };
}

export function cobrosDePoliza(token, polizaId) {
  const r = graphql(
    'query($p: ID) { cobros(polizaId: $p) { id estado intentos } }',
    { p: polizaId },
    token,
    'consulta',
  );
  return r.json('data.cobros') || [];
}

// Fija el modo del stub de la pasarela: sano | lento | error | caido | rechaza.
export function modoPasarela(modo) {
  const cabeceras = { 'Content-Type': 'application/json' };
  if (__ENV.TOKEN_PASARELA) cabeceras.Authorization = `Bearer ${__ENV.TOKEN_PASARELA}`;
  const r = http.post(`${STUB}/control/modo`, JSON.stringify({ modo }), {
    headers: cabeceras,
    tags: { tipo: 'control' },
  });
  if (r.status !== 200) fail(`No se pudo poner la pasarela en modo ${modo}: HTTP ${r.status}`);
}
