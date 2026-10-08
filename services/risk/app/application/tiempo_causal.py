"""Serialización reversible del `operationTime` de MongoDB (D-02, HA-LAT-003).

RISK devuelve el `operationTime` de su escritura (BSON `Timestamp`, con los
campos `.time` -segundos del oplog- y `.inc` -contador de orden dentro de ese
segundo-) en la respuesta HTTP de `POST /perfiles` y en el evento
`perfil.actualizado`. RATING lo deserializa y lo usa como punto de corte
(`afterClusterTime`) de su sesión causal.

Formato elegido (decisión de diseño no escrita en el plan): texto
"<time>.<inc>", p. ej. "1733600000.1" — el formato más simple y explícito
que reconstruye el mismo `Timestamp` exacto sin pérdida de precisión. No se
usa la representación BSON binaria ni JSON extendido de Mongo para no atar el
contrato del evento (que declara `operationTime` como `string` sin más
estructura) a un formato que el consumidor tendría que parsear como BSON.

`serializar_operation_time` no exige que el argumento sea un `bson.Timestamp`
de verdad: solo que tenga los atributos `.time`/`.inc` (duck typing), para
poder probarlo con un doble de prueba sin depender de pymongo."""

from typing import Protocol

from bson import Timestamp


class ComponentesTiempo(Protocol):
    time: int
    inc: int


def serializar_operation_time(operation_time: ComponentesTiempo) -> str:
    return f"{operation_time.time}.{operation_time.inc}"


def deserializar_operation_time(valor: str) -> Timestamp:
    tiempo_str, inc_str = valor.split(".")
    return Timestamp(int(tiempo_str), int(inc_str))
