// FC-10 · CA-W05-05 (EC009/EC010 aplicados a cobros): con la pasarela lenta (> 700 ms) el cobro queda
// PENDIENTE sin 5xx y, cuando la pasarela se recupera, el reintento automático lo cobra solo.
//
// Fase 1 (2 min): pasarela en modo `lento`, 2 cobros nuevos por segundo. Se mide que nunca haya 5xx ni
//                 ErrorCobro, y la latencia de `crearCobro` (el ACL corta a 700 ms).
// Fase 2 (hasta 3 min): pasarela `sano`; se espera a que los pendientes pasen a COBRADO por el reintento
//                 automático (en staging, Cloud Tasks; en local, el bucle de PAYMENTS).
//
//   k6 run -e BFF_URL=... -e CLAVE_BACKOFFICE=... -e STUB_PASARELA_URL=... -e TOKEN_PASARELA=... \
//          tests/k6/cobros-pasarela-lenta.js
import { sleep } from 'k6';
import { Rate } from 'k6/metrics';
import { CREAR_COBRO, entradaCobro, graphql, iniciarSesion, modoPasarela } from './lib/cobros.js';

const sinError5xx = new Rate('sin_error_5xx');
const sinErrorCobro = new Rate('sin_error_cobro');
const recuperados = new Rate('cobros_recuperados');

export const options = {
  scenarios: {
    pasarela_lenta: {
      executor: 'constant-arrival-rate',
      rate: 2,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 30,
    },
  },
  thresholds: {
    sin_error_5xx: ['rate==1'], // regla 4: nunca un 5xx crudo por una falla de proveedor
    sin_error_cobro: ['rate==1'],
    'http_req_duration{tipo:crear}': ['p(99)<1500'], // 700 ms de corte + red, con margen
    cobros_recuperados: ['rate>=0.95'],
  },
  teardownTimeout: '4m',
};

export function setup() {
  const marca = Date.now();
  modoPasarela('lento');
  return { token: iniciarSesion(), marca, polizaId: `K6-LENTA-${marca}` };
}

export default function (datos) {
  const id = `k6-lenta-${datos.marca}-${__VU}-${__ITER}`;
  const r = graphql(
    CREAR_COBRO,
    { e: entradaCobro(id, `${datos.polizaId}-${__VU}-${__ITER}`) },
    datos.token,
    'crear',
  );
  sinError5xx.add(r.status < 500);
  sinErrorCobro.add(r.json('data.crearCobro.__typename') === 'Cobro');
}

export function teardown(datos) {
  modoPasarela('sano');
  // Todos los cobros de esta corrida cuelgan de pólizas `K6-LENTA-<marca>-...`.
  const limite = Date.now() + 3 * 60 * 1000;
  let pendientes = [];
  let total = 0;
  while (Date.now() < limite) {
    const todos = cobrosDeLaCorrida(datos);
    total = todos.length;
    pendientes = todos.filter((c) => c.estado === 'PENDIENTE' || c.estado === 'COBRANDO');
    if (pendientes.length === 0) break;
    sleep(10);
  }
  for (let i = 0; i < total; i++) recuperados.add(i >= pendientes.length);
}

function cobrosDeLaCorrida(datos) {
  // `cobros` filtra por póliza exacta o por estado; se piden los dos estados abiertos y los cobrados
  // y se quedan los de esta corrida por el prefijo de la póliza.
  const vistos = [];
  for (const estado of ['PENDIENTE', 'COBRANDO', 'COBRADO']) {
    const r = graphql(
      'query($s: EstadoCobro) { cobros(estado: $s) { id estado polizaId } }',
      { s: estado },
      datos.token,
      'consulta',
    );
    for (const c of r.json('data.cobros') || []) {
      if (c.polizaId.startsWith(datos.polizaId)) vistos.push(c);
    }
  }
  return vistos;
}
