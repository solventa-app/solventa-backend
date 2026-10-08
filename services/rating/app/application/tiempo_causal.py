"""Deserialización del `operationTime` que RISK devuelve (D-02, HA-LAT-003).

Mismo formato que `services/risk/app/application/tiempo_causal.py` (texto
"<time>.<inc>", los dos componentes de `bson.timestamp.Timestamp"): RATING
reconstruye el `Timestamp` exacto y lo usa como punto de corte
(`afterClusterTime`) de su sesión causal, vía
`ClientSession.advance_operation_time`.

Duplicado deliberadamente (no se comparte código entre servicios): RISK
serializa, RATING deserializa; ambos deben coincidir en el formato."""

from bson import Timestamp


def deserializar_operation_time(valor: str) -> Timestamp:
    tiempo_str, inc_str = valor.split(".")
    return Timestamp(int(tiempo_str), int(inc_str))
