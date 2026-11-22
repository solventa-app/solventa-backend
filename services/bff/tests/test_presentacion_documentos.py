"""KAN-26: los documentos y los correos salen en el idioma (y la moneda) que tiene activos la web
de quien hace la acción. El BFF los toma de `Accept-Language` y `X-Moneda` y se los pasa a POLICY y
al ACL; la tasa de referencia sale del ACL (`/tasas-cambio`)."""

from datetime import UTC, datetime

from fakes import AclFalso, token_de_prueba
from fastapi.testclient import TestClient
from test_firma_pago import CODIGO, EMITIR, ENTRADA_FIRMA, FIRMAR, TOKEN_ENLACE, _montar, _pedidos

from app.configuracion import Configuracion
from app.main import create_app

HOY = datetime.now(UTC).date().isoformat()


def _cliente(clientes) -> TestClient:
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes))


def _gql(clientes, consulta: str, variables=None, rol=None, enlace=None, **cabeceras) -> dict:
    encabezados = dict(cabeceras)
    if rol:
        encabezados["Authorization"] = f"Bearer {token_de_prueba(rol, 'u-1')}"
    if enlace:
        encabezados["X-Enlace-Firma"] = enlace
    with _cliente(clientes) as client:
        r = client.post(
            "/graphql/web",
            json={"query": consulta, "variables": variables or {}},
            headers=encabezados,
        )
    assert r.status_code == 200, r.text
    return r.json()


def test_al_firmar_en_ingles_y_dolares_el_contrato_queda_con_esa_presentacion() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    _gql(
        clientes,
        FIRMAR,
        {"e": ENTRADA_FIRMA},
        enlace=TOKEN_ENLACE,
        **{"Accept-Language": "en", "X-Moneda": "USD"},
    )

    entrada = _pedidos(policy, "firmar_enlace")[0][3]
    assert entrada["presentacion"] == {
        "idioma": "en",
        "moneda": "USD",
        "cop_por_unidad": "4000",
        "fecha_tasa": HOY,
    }


def test_sin_tasas_el_contrato_sale_en_cop_y_la_firma_no_falla() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA", acl=AclFalso(fallan={"tasas"}))

    cuerpo = _gql(
        clientes,
        FIRMAR,
        {"e": ENTRADA_FIRMA},
        enlace=TOKEN_ENLACE,
        **{"Accept-Language": "en", "X-Moneda": "USD"},
    )

    assert cuerpo["data"]["firmarPorEnlace"]["__typename"] == "FirmaRegistrada"
    entrada = _pedidos(policy, "firmar_enlace")[0][3]
    assert entrada["presentacion"] == {"idioma": "en", "moneda": "COP"}


def test_una_moneda_desconocida_sale_en_cop() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    _gql(clientes, FIRMAR, {"e": ENTRADA_FIRMA}, enlace=TOKEN_ENLACE, **{"X-Moneda": "XYZ"})

    assert _pedidos(policy, "firmar_enlace")[0][3]["presentacion"] == {
        "idioma": "es",
        "moneda": "COP",
    }


def test_el_codigo_y_el_enlace_salen_en_el_idioma_del_asesor() -> None:
    clientes, _, policy, _ = _montar()
    _gql(clientes, CODIGO, {"id": "p-1"}, rol="ASESOR", **{"Accept-Language": "en-US"})
    assert policy.idiomas["enviar_codigo"] == "en"

    clientes, _, policy, _ = _montar(estado="IDENTIDAD_VERIFICADA")
    _gql(clientes, EMITIR, rol="ASESOR", **{"Accept-Language": "en"})
    assert policy.idiomas["emitir"] == "en"

    clientes, _, policy, _ = _montar()
    _gql(clientes, CODIGO, {"id": "p-1"}, rol="ASESOR")
    assert policy.idiomas["enviar_codigo"] == "es"


def test_el_contrato_en_pdf_pide_el_borrador_en_el_idioma_y_la_moneda_de_quien_lo_abre() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    with _cliente(clientes) as client:
        r = client.get(
            "/propuestas/p-1/contrato.pdf",
            headers={"X-Enlace-Firma": TOKEN_ENLACE, "Accept-Language": "en", "X-Moneda": "EUR"},
        )

    assert r.status_code == 200
    assert policy.presentacion_pdf == {
        "idioma": "en",
        "moneda": "EUR",
        "cop_por_unidad": "4400",
        "fecha_tasa": HOY,
    }


def test_el_codigo_de_registro_sale_en_el_idioma_de_la_app() -> None:
    acl = AclFalso()
    clientes, *_ = _montar(acl=acl)

    _gql(
        clientes,
        'mutation { enviarCodigoOnboarding(correo: "ana.prueba@ejemplo.com") { __typename } }',
        **{"Accept-Language": "en"},
    )

    assert [n["idioma"] for n in acl.notificaciones] == ["en"]
