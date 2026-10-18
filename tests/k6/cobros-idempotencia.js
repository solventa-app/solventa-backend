// FC-10 · CA-W05-04: un mismo intento de cobro se ejecuta UNA sola vez, aunque llegue muchas veces a la vez.
//
// 20 usuarios virtuales mandan `crearCobro` con el MISMO idIntentoCobro al mismo tiempo. Al final se
// cuenta en la base (por el BFF) cuántos cobros quedaron para esa póliza: debe ser exactamente 1.
//
// Prueba reducida (plan de HU-W05, bloque 4): una iteración por usuario, no una hora de carga. Lo que se
// mide es la corrección (0 duplicados), no el rendimiento.
//
// Con la pasarela en su modo normal (`sano`). Uso:
//   k6 run -e BFF_URL=... -e CLAVE_BACKOFFICE=... tests/k6/cobros-idempotencia.js
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';
import { CREAR_COBRO, cobrosDePoliza, entradaCobro, graphql, iniciarSesion } from './lib/cobros.js';

const sinError5xx = new Rate('sin_error_5xx');
const cobrosCreados = new Counter('cobros_creados_para_el_intento');
const idempotente = new Rate('idempotencia_ok');

export const options = {
  scenarios: {
    mismo_intento: { executor: 'per-vu-iterations', vus: 20, iterations: 1, maxDuration: '1m' },
  },
  thresholds: {
    sin_error_5xx: ['rate==1'],
    idempotencia_ok: ['rate==1'], // exactamente 1 cobro para el intento
  },
};

export function setup() {
  const marca = Date.now();
  return {
    token: iniciarSesion(),
    idIntentoCobro: `k6-idem-${marca}`,
    polizaId: `K6-IDEM-${marca}`,
  };
}

export default function (datos) {
  const r = graphql(
    CREAR_COBRO,
    { e: entradaCobro(datos.idIntentoCobro, datos.polizaId) },
    datos.token,
    'crear',
  );
  sinError5xx.add(r.status < 500);
  check(r, { 'responde el cobro, no un error': (x) => x.json('data.crearCobro.__typename') === 'Cobro' });
}

export function teardown(datos) {
  const cobros = cobrosDePoliza(datos.token, datos.polizaId);
  cobrosCreados.add(cobros.length);
  idempotente.add(cobros.length === 1);
}
