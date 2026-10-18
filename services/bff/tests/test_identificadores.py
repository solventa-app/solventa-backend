from app.identificadores import cliente_id_desde_documento, id_opaco


def test_cliente_id_es_determinista() -> None:
    primero = cliente_id_desde_documento("CC-123", "k")
    segundo = cliente_id_desde_documento("CC-123", "k")

    assert primero == segundo


def test_cliente_id_ignora_espacios_y_mayusculas() -> None:
    assert cliente_id_desde_documento(" cc-123 ", "k") == cliente_id_desde_documento("CC-123", "k")


def test_la_clave_cambia_el_cliente_id() -> None:
    assert cliente_id_desde_documento("CC-123", "a") != cliente_id_desde_documento("CC-123", "b")


def test_el_cliente_id_no_contiene_el_documento() -> None:
    assert "123" not in cliente_id_desde_documento("CC-123", "k")


def test_id_opaco_es_corto() -> None:
    assert len(id_opaco("cualquier-cosa")) == 12
