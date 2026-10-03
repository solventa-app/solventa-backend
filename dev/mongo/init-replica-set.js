// Inicializa el replica set "rs0" (1 primario + 1 secundario) del Experimento 2 (copiado de solventa-arquitectura@11e4be6).
// Se ejecuta con mongosh contra mongo1 desde el contenedor `mongo-init`.
// Idempotente: si el set ya está inicializado, no lo vuelve a inicializar.

const NOMBRE_SET = "rs0";
const CONFIG = {
  _id: NOMBRE_SET,
  members: [
    // priority 2 hace que mongo1 sea el primario de forma determinista.
    { _id: 0, host: "mongo1:27017", priority: 2 },
    { _id: 1, host: "mongo2:27017", priority: 1 },
  ],
};
const TIMEOUT_MS = 60000;

function dormir(ms) {
  sleep(ms);
}

function estadoOInexistente() {
  try {
    // Ojo: asignar a una variable antes de retornar. mongosh devuelve un
    // objeto asíncrono; con `return rs.status()` el rechazo escaparía del
    // try/catch y el error 94 (NotYetInitialized) no se capturaría.
    const estado = rs.status();
    return estado;
  } catch (e) {
    // 94 = NotYetInitialized
    if (e.code === 94 || /no replset config/i.test(e.message)) return null;
    throw e;
  }
}

if (estadoOInexistente() === null) {
  print("Inicializando replica set " + NOMBRE_SET + "...");
  printjson(rs.initiate(CONFIG));
} else {
  print("El replica set " + NOMBRE_SET + " ya estaba inicializado, se omite rs.initiate().");
}

// Espera a que mongo1 (el nodo al que estamos conectados) sea el PRIMARY y a
// que mongo2 sea SECONDARY. Comprobar solo "hay 1 primario" no basta: durante
// el arranque el primario puede ser transitoriamente mongo2 antes de que la
// prioridad 2 de mongo1 provoque el traspaso, y los comandos de escritura
// siguientes fallarían con "not primary".
const inicio = Date.now();
let listo = false;
while (Date.now() - inicio < TIMEOUT_MS) {
  const miembros = rs.status().members;
  const secundarios = miembros.filter((m) => m.stateStr === "SECONDARY").length;
  const yoSoyPrimario = db.hello().isWritablePrimary === true;
  if (yoSoyPrimario && secundarios === 1) {
    listo = true;
    break;
  }
  dormir(1000);
}
if (!listo) {
  print("ERROR: el replica set no alcanzó mongo1=PRIMARY + 1 SECONDARY en " + TIMEOUT_MS / 1000 + "s");
  printjson(rs.status().members.map((m) => ({ name: m.name, state: m.stateStr })));
  quit(1);
}

// Write concern por defecto = { w: 1 }. Desde MongoDB 5.0 el default implícito
// es "majority", que en un set de 2 miembros de datos obliga a que la
// escritura ya esté en el secundario antes de devolver ack: eso ocultaría
// justamente el lag que este experimento quiere medir. El experimento modela
// a RISK escribiendo a la primaria con ack de la primaria (w:1) y a RATING
// leyendo de la réplica asíncrona.
const wc = db.adminCommand({
  setDefaultRWConcern: 1,
  defaultWriteConcern: { w: 1 },
});
if (wc.ok !== 1) {
  print("ERROR: no se pudo fijar el write concern por defecto");
  printjson(wc);
  quit(1);
}

print("Replica set " + NOMBRE_SET + " listo (PRIMARY + SECONDARY, write concern por defecto w:1).");
printjson(rs.status().members.map((m) => ({ name: m.name, state: m.stateStr })));
