from bson import Timestamp
from fakes import TimestampFalso

from app.application.tiempo_causal import deserializar_operation_time, serializar_operation_time


def test_serializar_y_deserializar_hacen_round_trip_con_timestamp_real() -> None:
    original = Timestamp(1_733_600_123, 7)

    serializado = serializar_operation_time(original)
    reconstruido = deserializar_operation_time(serializado)

    assert serializado == "1733600123.7"
    assert reconstruido == original
    assert isinstance(reconstruido, Timestamp)


def test_serializar_acepta_cualquier_objeto_con_time_e_inc() -> None:
    assert serializar_operation_time(TimestampFalso(42, 0)) == "42.0"
