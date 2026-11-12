import pytest

from app.esquema_evento import EventoInvalido, construir_evento, validar


def test_construye_un_evento_valido() -> None:
    evento = construir_evento("idem-1", "usuario-1", "indemnizacion-liquidada", {"monto": "1"})
    validar(evento)
    assert evento["tipo"] == "notificacion.push-solicitada"
    assert evento["version"] == 1
    assert evento["datos"]["usuarioId"] == "usuario-1"


def test_construye_con_todos_los_campos_opcionales() -> None:
    evento = construir_evento(
        "idem-2",
        "usuario-1",
        "sesion-cerrada",
        variables=None,
        ruta="/siniestros/1",
        ttl_segundos=300,
        silenciosa=True,
    )
    validar(evento)
    assert evento["datos"]["ruta"] == "/siniestros/1"
    assert evento["datos"]["ttlSegundos"] == 300
    assert evento["datos"]["silenciosa"] is True


def test_rechaza_evento_sin_usuario_id() -> None:
    evento = construir_evento("idem-3", "usuario-1", "sesion-cerrada")
    del evento["datos"]["usuarioId"]
    with pytest.raises(EventoInvalido):
        validar(evento)


def test_rechaza_ttl_fuera_de_rango() -> None:
    evento = construir_evento("idem-4", "usuario-1", "sesion-cerrada", ttl_segundos=0)
    with pytest.raises(EventoInvalido):
        validar(evento)


def test_rechaza_propiedad_extra_en_datos() -> None:
    evento = construir_evento("idem-5", "usuario-1", "sesion-cerrada")
    evento["datos"]["extra"] = "no-deberia-estar"
    with pytest.raises(EventoInvalido):
        validar(evento)


def test_rechaza_tipo_incorrecto() -> None:
    evento = construir_evento("idem-6", "usuario-1", "sesion-cerrada")
    evento["tipo"] = "otro.tipo"
    with pytest.raises(EventoInvalido):
        validar(evento)
