"""Correo y teléfono del CLIENTE (HU-W03, bloque 8, W03-16 y W03-17): validación, cifrado por campo
con su propio contexto, solo para CLIENTE y lectura solo por el endpoint privado."""

import logging

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    RepositorioUsuariosMemoria,
    documento_de,
)
from fastapi.testclient import TestClient

from app.cifrado import ErrorDeCifrado
from app.configuracion import Configuracion
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import (
    DatoInvalido,
    ServicioUsuarios,
    contexto_correo,
    contexto_telefono,
    normalizar_correo,
    normalizar_telefono,
)

CLAVE = "clave-segura-1"
CORREO = "maria.gomez@ejemplo.co"
TELEFONO = "+573001234567"


def _servicio(con_cifrador: bool = True) -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    cifrador = CIFRADOR_PRUEBAS if con_cifrador else None
    return ServicioUsuarios(
        repositorio, HASHER_RAPIDO, cifrador, CLAVE_TITULAR_PRUEBAS
    ), repositorio


def _cliente(servicio: ServicioUsuarios, alias: str = "cliente.uno", **contacto) -> dict:
    contacto.setdefault("correo", CORREO)
    return servicio.crear(alias, CLAVE, "CLIENTE", documento=documento_de(alias), **contacto)


def _cliente_anterior(
    servicio: ServicioUsuarios,
    repositorio: RepositorioUsuariosMemoria,
    alias: str = "cliente.uno",
    telefono: str | None = None,
) -> dict:
    """Un CLIENTE creado antes de W03-27, cuando el correo era opcional: hoy ya no se puede crear
    así, pero los que existen siguen funcionando."""
    creado = _cliente(servicio, alias, telefono=telefono)
    repositorio.filas[creado["id"]]["correo_cifrado"] = None
    return servicio.obtener(creado["id"])


# ---------- normalización y validación


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("maria.gomez@ejemplo.co", "maria.gomez@ejemplo.co"),
        ("  María.Gómez@Ejemplo.CO  ", "maría.gómez@ejemplo.co"),
        ("a+etiqueta@sub.dominio.com", "a+etiqueta@sub.dominio.com"),
    ],
)
def test_el_correo_se_limpia_y_va_en_minusculas(entrada: str, esperado: str) -> None:
    assert normalizar_correo(entrada) == esperado


@pytest.mark.parametrize(
    "correo",
    [
        "",
        "sin-arroba",
        "a@b",
        "a@@b.co",
        "con espacio@b.co",
        "@b.co",
        "a@.co",
        "a" * 250 + "@b.co",
        "a\x07@b.co",
    ],
)
def test_correos_invalidos(correo: str) -> None:
    with pytest.raises(DatoInvalido, match="El correo no es válido"):
        normalizar_correo(correo)


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("+573001234567", "+573001234567"),
        ("+57 300-123 4567", "+573001234567"),
        ("(601) 234.5678", "6012345678"),
        ("  3001234567 ", "3001234567"),
    ],
)
def test_el_telefono_queda_solo_con_digitos_y_un_mas(entrada: str, esperado: str) -> None:
    assert normalizar_telefono(entrada) == esperado


@pytest.mark.parametrize(
    "telefono", ["", "abc", "123456", "1" * 16, "+", "30012x4567", "57+3001234567"]
)
def test_telefonos_invalidos(telefono: str) -> None:
    with pytest.raises(DatoInvalido, match="El teléfono no es válido"):
        normalizar_telefono(telefono)


# ---------- crear: cifrado por campo y solo CLIENTE


def test_el_contacto_se_guarda_cifrado_y_no_sale_en_el_usuario() -> None:
    servicio, repositorio = _servicio()

    creado = _cliente(servicio, correo=CORREO, telefono=TELEFONO)
    fila = repositorio.filas[creado["id"]]

    assert CORREO not in str(fila.values()) and "3001234567" not in str(fila.values())
    assert (
        CIFRADOR_PRUEBAS.descifrar(fila["correo_cifrado"], contexto_correo(creado["id"])) == CORREO
    )
    assert (
        CIFRADOR_PRUEBAS.descifrar(fila["telefono_cifrado"], contexto_telefono(creado["id"]))
        == TELEFONO
    )
    assert "correo" not in creado and "telefono" not in creado
    assert creado["tiene_contacto"] is True


def test_un_cliente_anterior_sin_contacto_dice_que_no_lo_tiene() -> None:
    servicio, repositorio = _servicio()

    assert _cliente_anterior(servicio, repositorio)["tiene_contacto"] is False


def test_un_cliente_nuevo_exige_el_correo() -> None:
    servicio, repositorio = _servicio()

    with pytest.raises(DatoInvalido, match="documento de identidad y su correo"):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", telefono=TELEFONO, documento="1234567890")
    assert repositorio.filas == {}


def test_el_correo_o_el_telefono_pueden_faltar_en_un_cliente() -> None:
    servicio, repositorio = _servicio()

    solo_correo = _cliente(servicio, "uno.correo", correo=CORREO)
    solo_telefono = _cliente_anterior(servicio, repositorio, "uno.telefono", telefono=TELEFONO)

    assert servicio.contacto_de(solo_correo["id"]) == {"correo": CORREO, "telefono": None}
    assert servicio.contacto_de(solo_telefono["id"]) == {"correo": None, "telefono": TELEFONO}


def test_el_cifrado_de_un_campo_no_se_descifra_en_el_otro() -> None:
    """Cada campo lleva su propio contexto: mover un cifrado de columna no lo vuelve legible."""
    servicio, repositorio = _servicio()
    creado = _cliente(servicio, correo=CORREO, telefono=TELEFONO)
    fila = repositorio.filas[creado["id"]]

    with pytest.raises(ErrorDeCifrado):
        CIFRADOR_PRUEBAS.descifrar(fila["correo_cifrado"], contexto_telefono(creado["id"]))
    with pytest.raises(ErrorDeCifrado):
        CIFRADOR_PRUEBAS.descifrar(fila["telefono_cifrado"], contexto_correo(creado["id"]))
    # ni con el contexto del nombre, que es el id a secas
    with pytest.raises(ErrorDeCifrado):
        CIFRADOR_PRUEBAS.descifrar(fila["correo_cifrado"], creado["id"])


def test_un_cifrado_de_otro_usuario_no_sirve() -> None:
    servicio, repositorio = _servicio()
    uno = _cliente(servicio, "cliente.uno", correo=CORREO)
    dos = _cliente(servicio, "cliente.dos", correo="otra@ejemplo.co")
    # Se copia el cifrado de uno en la fila de dos: no debe leerse.
    repositorio.filas[dos["id"]]["correo_cifrado"] = repositorio.filas[uno["id"]]["correo_cifrado"]

    assert servicio.contacto_de(dos["id"]) == {"correo": None, "telefono": None}


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_los_clientes_tienen_contacto(rol: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido, match="solo se guarda para clientes"):
        servicio.crear("otro.rol", CLAVE, rol, correo=CORREO)


def test_un_contacto_invalido_no_crea_el_usuario() -> None:
    servicio, repositorio = _servicio()

    with pytest.raises(DatoInvalido):
        _cliente(servicio, correo="no-es-un-correo")

    assert repositorio.filas == {}


def test_sin_cifrador_no_se_guarda_contacto() -> None:
    servicio, _ = _servicio(con_cifrador=False)

    with pytest.raises(DatoInvalido, match="no está configurado para guardar el contacto"):
        _cliente(servicio, correo=CORREO)


# ---------- actualizar_contacto


def test_actualizar_solo_cambia_lo_que_llega() -> None:
    servicio, _ = _servicio()
    creado = _cliente(servicio, correo=CORREO)

    servicio.actualizar_contacto(creado["id"], None, TELEFONO)
    assert servicio.contacto_de(creado["id"]) == {"correo": CORREO, "telefono": TELEFONO}

    servicio.actualizar_contacto(creado["id"], "nuevo@ejemplo.co", None)
    assert servicio.contacto_de(creado["id"]) == {
        "correo": "nuevo@ejemplo.co",
        "telefono": TELEFONO,
    }


def test_actualizar_devuelve_que_ya_tiene_contacto_pero_no_cual() -> None:
    servicio, repositorio = _servicio()
    creado = _cliente_anterior(servicio, repositorio)

    actualizado = servicio.actualizar_contacto(creado["id"], CORREO, None)

    assert actualizado["tiene_contacto"] is True
    assert CORREO not in str(actualizado)


def test_actualizar_sin_nada_pide_al_menos_uno() -> None:
    servicio, _ = _servicio()
    creado = _cliente(servicio)

    with pytest.raises(DatoInvalido, match="Indica un correo o un teléfono"):
        servicio.actualizar_contacto(creado["id"], None, None)


def test_actualizar_un_usuario_inexistente_es_none() -> None:
    servicio, _ = _servicio()

    assert servicio.actualizar_contacto("no-existe", CORREO, None) is None


def test_actualizar_el_contacto_de_un_asesor_se_rechaza() -> None:
    servicio, _ = _servicio()
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR")

    with pytest.raises(DatoInvalido, match="solo se guarda para clientes"):
        servicio.actualizar_contacto(asesor["id"], CORREO, None)


def test_un_dato_invalido_al_actualizar_no_cambia_nada() -> None:
    servicio, _ = _servicio()
    creado = _cliente(servicio, correo=CORREO)

    with pytest.raises(DatoInvalido):
        servicio.actualizar_contacto(creado["id"], "malo", TELEFONO)

    assert servicio.contacto_de(creado["id"]) == {"correo": CORREO, "telefono": None}


# ---------- contacto_de


def test_contacto_de_un_cliente_sin_contacto_trae_ambos_vacios() -> None:
    servicio, repositorio = _servicio()
    creado = _cliente_anterior(servicio, repositorio)

    assert servicio.contacto_de(creado["id"]) == {"correo": None, "telefono": None}


def test_contacto_de_un_usuario_inexistente_o_que_no_es_cliente_es_none() -> None:
    servicio, _ = _servicio()
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR")

    assert servicio.contacto_de("no-existe") is None
    assert servicio.contacto_de(asesor["id"]) is None


def test_un_campo_danado_sale_vacio_sin_tumbar_el_otro(caplog) -> None:
    servicio, repositorio = _servicio()
    creado = _cliente(servicio, correo=CORREO, telefono=TELEFONO)
    repositorio.filas[creado["id"]]["correo_cifrado"] = "esto-no-es-un-cifrado"

    with caplog.at_level(logging.WARNING):
        contacto = servicio.contacto_de(creado["id"])

    assert contacto == {"correo": None, "telefono": TELEFONO}
    assert TELEFONO not in caplog.text and "esto-no-es" not in caplog.text


# ---------- HTTP


def _http() -> TestClient:
    repositorio = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", "secreto-de-prueba-con-32-bytes-o-mas", 60)
    app = create_app(
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, config.jwt_secreto, 60),
        config=config,
    )
    return TestClient(app)


def _crear(client: TestClient, alias: str = "cliente.uno", rol: str = "CLIENTE", **extra):
    if rol == "CLIENTE":
        extra.setdefault("documento_identidad", documento_de(alias))
        extra.setdefault("correo", CORREO)
    return client.post("/usuarios", json={"usuario": alias, "clave": CLAVE, "rol": rol, **extra})


def test_http_crear_con_contacto_no_lo_devuelve() -> None:
    with _http() as client:
        r = _crear(client, correo=CORREO, telefono=TELEFONO)

    assert r.status_code == 200
    assert r.json()["tiene_contacto"] is True
    assert CORREO not in r.text and "3001234567" not in r.text


def test_http_el_contacto_se_lee_por_el_endpoint_privado() -> None:
    with _http() as client:
        creado = _crear(client, correo=CORREO, telefono=TELEFONO).json()
        r = client.get(f"/usuarios/{creado['id']}/contacto")

    assert r.status_code == 200
    assert r.json() == {"correo": CORREO, "telefono": TELEFONO}


def test_http_el_contacto_no_sale_por_ningun_otro_camino() -> None:
    with _http() as client:
        creado = _crear(client, correo=CORREO, telefono=TELEFONO).json()
        sesion = client.post("/sesiones", json={"usuario": "cliente.uno", "clave": CLAVE})
        propio = client.get(f"/usuarios/{creado['id']}")
        lista = client.get("/usuarios")
        nombres = client.post("/usuarios/nombres", json={"ids": [creado["id"]]})

    for respuesta in (sesion, propio, lista, nombres):
        assert CORREO not in respuesta.text and "3001234567" not in respuesta.text
    assert sesion.json()["usuario"]["tiene_contacto"] is True


def test_http_actualizar_el_contacto_del_propio_cliente() -> None:
    with _http() as client:
        creado = _crear(client).json()
        r = client.post(f"/usuarios/{creado['id']}/contacto", json={"correo": "nuevo@ejemplo.co"})
        contacto = client.get(f"/usuarios/{creado['id']}/contacto").json()

    assert r.status_code == 200 and r.json()["tiene_contacto"] is True
    assert "nuevo@ejemplo.co" not in r.text
    assert contacto == {"correo": "nuevo@ejemplo.co", "telefono": None}


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"correo": "malo"}, {"telefono": "123"}, {"correo": CORREO, "telefono": "abc"}],
)
def test_http_actualizar_con_datos_invalidos_da_400(cuerpo: dict) -> None:
    with _http() as client:
        creado = _crear(client).json()
        r = client.post(f"/usuarios/{creado['id']}/contacto", json=cuerpo)

    assert r.status_code == 400


def test_http_crear_un_asesor_con_contacto_da_400() -> None:
    with _http() as client:
        r = _crear(client, "asesor.uno", "ASESOR", correo=CORREO)

    assert r.status_code == 400


def test_http_usuario_inexistente_da_404() -> None:
    with _http() as client:
        leer = client.get("/usuarios/no-existe/contacto")
        escribir = client.post("/usuarios/no-existe/contacto", json={"correo": CORREO})

    assert leer.status_code == 404 and escribir.status_code == 404


def test_http_el_contacto_de_un_asesor_no_se_expone() -> None:
    with _http() as client:
        asesor = _crear(client, "asesor.uno", "ASESOR").json()
        r = client.get(f"/usuarios/{asesor['id']}/contacto")

    assert r.status_code == 404


def test_los_logs_no_llevan_el_contacto(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _http() as client:
        creado = _crear(client, correo=CORREO, telefono=TELEFONO).json()
        client.post(f"/usuarios/{creado['id']}/contacto", json={"correo": "otro@ejemplo.co"})
        client.get(f"/usuarios/{creado['id']}/contacto")
        client.post(f"/usuarios/{creado['id']}/contacto", json={"correo": "esto-es-invalido"})

    for dato in (CORREO, "maria.gomez", "otro@ejemplo.co", "3001234567", "esto-es-invalido"):
        assert dato not in caplog.text
