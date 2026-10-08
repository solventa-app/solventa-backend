import pytest
from bson import Timestamp

from app.application.tiempo_causal import deserializar_operation_time


def test_deserializar_reconstruye_el_timestamp_exacto() -> None:
    assert deserializar_operation_time("1733600123.7") == Timestamp(1_733_600_123, 7)


def test_deserializar_con_formato_invalido_lanza_value_error() -> None:
    with pytest.raises(ValueError):
        deserializar_operation_time("no-es-un-timestamp")
