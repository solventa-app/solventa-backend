"""T20 (KAN-26): tipo de documento y país del CLIENTE y de la cuenta. El número se valida con el
formato del catálogo del país, el tipo y el país entran al `titular_id` (salvo la cédula
colombiana, que conserva el de siempre) y el país es una preferencia más."""

import hashlib
import hmac

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    RepositorioUsuariosMemoria,
)

from app.identificadores import cliente_id_desde_documento, valor_titular
from app.usuarios import DatoInvalido, ServicioUsuarios, UsuarioDuplicado

CLAVE = "Clave-segura-1"
CORREO = "ana.perez@ejemplo.co"


def _servicio() -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    servicio = ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS)
    return servicio, repositorio


def _hmac(valor: str) -> str:
    return hmac.new(CLAVE_TITULAR_PRUEBAS.encode(), valor.encode(), hashlib.sha256).hexdigest()


# ---------- titular


def test_la_cedula_colombiana_conserva_el_titular_de_antes() -> None:
    assert valor_titular("1020304050") == "1020304050"
    assert valor_titular("1020304050", "CO", "CC") == "1020304050"
    assert cliente_id_desde_documento("1020304050", CLAVE_TITULAR_PRUEBAS, "co", "cc") == _hmac(
        "1020304050"
    )


@pytest.mark.parametrize(
    ("pais", "tipo"), [("CO", "CE"), ("CO", "PASAPORTE"), ("PE", "DNI"), ("ES", "DNI_NIE")]
)
def test_otro_tipo_o_pais_es_otro_titular(pais: str, tipo: str) -> None:
    titular = cliente_id_desde_documento("12345678", CLAVE_TITULAR_PRUEBAS, pais, tipo)
    assert titular == _hmac(f"{pais}:{tipo}:12345678")
    assert titular != cliente_id_desde_documento("12345678", CLAVE_TITULAR_PRUEBAS)


# ---------- alta del CLIENTE


@pytest.mark.parametrize(
    ("pais", "tipo", "numero", "guardado"),
    [
        ("CO", "CC", "1.020.304.050", "1020304050"),
        ("CO", "CE", "998877", "998877"),
        ("MX", "CURP", "gomp800101hdfrrr09", "GOMP800101HDFRRR09"),
        ("BR", "CPF", "123.456.789-09", "12345678909"),
        ("CL", "RUT", "12.345.678-5", "123456785"),
        ("PE", "DNI", "12345678", "12345678"),
        ("US", "SSN", "123-45-6789", "123456789"),
        ("ES", "DNI_NIE", "12345678Z", "12345678Z"),
        ("ES", "PASAPORTE", "PAA123456", "PAA123456"),
    ],
)
def test_crea_un_cliente_de_cada_pais(pais: str, tipo: str, numero: str, guardado: str) -> None:
    servicio, repositorio = _servicio()

    creado = servicio.crear(
        "cliente.uno",
        CLAVE,
        "CLIENTE",
        correo=CORREO,
        documento=numero,
        tipo_documento=tipo,
        pais=pais,
    )

    assert (creado["tipo_documento"], creado["pais"]) == (tipo, pais)
    assert repositorio.filas[creado["id"]]["titular_id"] == cliente_id_desde_documento(
        guardado, CLAVE_TITULAR_PRUEBAS, pais, tipo
    )
    identidad = servicio.identidad_de(creado["id"])
    assert identidad == {
        "titular_id": repositorio.filas[creado["id"]]["titular_id"],
        "documento": guardado,
        "tipo_documento": tipo,
        "pais": pais,
    }


def test_sin_tipo_toma_el_nacional_del_pais() -> None:
    servicio, _ = _servicio()

    creado = servicio.crear(
        "cliente.uno", CLAVE, "CLIENTE", correo=CORREO, documento="12345678", pais="pe"
    )

    assert (creado["tipo_documento"], creado["pais"]) == ("DNI", "PE")


@pytest.mark.parametrize(
    ("pais", "tipo", "numero"),
    [
        ("CO", "CURP", "1020304050"),  # tipo de otro país
        ("XX", "CC", "1020304050"),  # país no soportado
        ("MX", "CURP", "1020304050"),  # no cumple el formato
        ("PE", "DNI", "1234"),
    ],
)
def test_rechaza_un_documento_que_no_es_del_pais(pais: str, tipo: str, numero: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear(
            "cliente.uno",
            CLAVE,
            "CLIENTE",
            correo=CORREO,
            documento=numero,
            tipo_documento=tipo,
            pais=pais,
        )


def test_mismo_numero_en_otro_tipo_no_es_duplicado() -> None:
    servicio, _ = _servicio()
    servicio.crear("cliente.uno", CLAVE, "CLIENTE", correo=CORREO, documento="998877")

    otro = servicio.crear(
        "cliente.dos",
        CLAVE,
        "CLIENTE",
        correo=CORREO,
        documento="998877",
        tipo_documento="CE",
        pais="CO",
    )

    assert otro["tipo_documento"] == "CE"
    with pytest.raises(UsuarioDuplicado):
        servicio.crear(
            "cliente.tres",
            CLAVE,
            "CLIENTE",
            correo=CORREO,
            documento="998877",
            tipo_documento="CC",
            pais="CO",
        )


def test_un_asesor_puede_tener_pais_sin_documento() -> None:
    servicio, _ = _servicio()

    creado = servicio.crear("asesor.mx", CLAVE, "ASESOR", pais="MX")

    assert (creado["tipo_documento"], creado["pais"]) == (None, "MX")


# ---------- preferencias y onboarding


def test_el_pais_es_una_preferencia_y_si_no_llega_se_conserva() -> None:
    servicio, _ = _servicio()
    creado = servicio.crear("asesor.017", CLAVE, "ASESOR")

    cambiado = servicio.actualizar_preferencias(creado["id"], "EN", "USD", "us")
    assert cambiado["pais"] == "US"

    sin_pais = servicio.actualizar_preferencias(creado["id"], "ES", "COP")
    assert sin_pais["pais"] == "US"

    with pytest.raises(DatoInvalido):
        servicio.actualizar_preferencias(creado["id"], "ES", "COP", "AR")


def test_el_onboarding_cierra_con_tipo_y_pais() -> None:
    servicio, repositorio = _servicio()
    cuenta = servicio.crear_cuenta_onboarding(CORREO, CLAVE)

    cerrada = servicio.cerrar_verificacion(
        cuenta["id"], True, "Ana Pérez", "998877", tipo_documento="CE", pais="CO"
    )

    assert (cerrada["tipo_documento"], cerrada["pais"]) == ("CE", "CO")
    assert repositorio.filas[cuenta["id"]]["titular_id"] == _hmac("CO:CE:998877")
