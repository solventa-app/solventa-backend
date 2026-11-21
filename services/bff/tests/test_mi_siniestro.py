"""Detalle de un caso para el CLIENTE que lo reportó (v0.64, HU-M05, KAN-35, pantalla M05-07)."""

import pytest
from fakes import AlmacenamientoFalso, ClaimsFalso, PolicyFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.clientes import ArchivoRechazado, ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

DETALLE = """query($id: ID!) { miSiniestro(id: $id) {
  id radicado estado ocurridoEn notificadoEn venceDictamenEn tipoSiniestroAuto descripcion
  ubicacion { descripcion ciudad latitud longitud }
  poliza { numero producto }
  fotos { id url registradaEn }
  historial { estado en }
  resolucion { tipo indemnizacion { valor moneda } }
  apruebaAlVencer } }"""

POLIZA = {
    "id": "pol-01",
    "numero": "SOL-2026-000007",
    "propuesta_id": "p-1",
    "cliente_id": "cliente-opaco",
    "estado": "EMITIDA",
    "vigencia_desde": "2026-10-22T12:00:00+00:00",
    "vigencia_hasta": "2027-10-22T12:00:00+00:00",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "118500000", "moneda": "COP"},
    "actor_usuario_id": "c-1",
    "firmante_id": "c-1",
    "producto": "AUTO",
}


def _siniestro(**cambios) -> dict:
    return {
        "id": "sin-77",
        "radicado": "SIN-2026-1077",
        "estado": "PENDIENTE",
        "poliza_id": "pol-01",
        "ocurrido_en": "2026-11-04T13:00:00+00:00",
        "notificado_en": "2026-11-04T15:00:00+00:00",
        "vence_dictamen_en": "2026-11-06T17:00:00-05:00",
        "reportado_por": "c-1",
        "tipo_siniestro_auto": "PERDIDA_TOTAL_DANOS",
        "descripcion": "Me chocaron por detrás en un semáforo.",
        "ubicacion": {
            "descripcion": "Cra 15 con Calle 93",
            "ciudad": "Bogotá",
            "latitud": 4.6767,
            "longitud": -74.0483,
        },
        "asegurado": {"nombre": "Ana Pérez", "documento": "1020304050"},
        "suma_reclamada": {"valor": "9000000", "moneda": "COP"},
        "comentario_interno": "no debe salir",
        "evidencias": [
            {
                "id": "ev-1",
                "tipo": "FOTO",
                "archivo_id": "arc-1",
                "registrada_en": "2026-11-04T13:05:00+00:00",
            },
            {
                "id": "ev-2",
                "tipo": "DOCUMENTO",
                "archivo_id": "arc-9",
                "registrada_en": "2026-11-04T16:00:00+00:00",
            },
        ],
        "resolucion": None,
        **cambios,
    }


def _montar(siniestro: dict | None = None, claims_falla: bool = False):
    claims = ClaimsFalso()
    claims.siniestros = {"sin-77": siniestro or _siniestro()}
    if claims_falla:

        async def caido(_id):
            raise ErrorServicio("claims")

        claims.obtener = caido
    policy = PolicyFalso()
    policy.polizas["pol-01"] = POLIZA
    almacen = AlmacenamientoFalso()
    almacen.archivos["arc-1"] = {
        "id": "arc-1",
        "estado": "DISPONIBLE",
        "propietario_id": "c-1",
        "tipo": "siniestro-foto",
    }
    clientes = clientes_falsos(claims=claims, policy=policy, almacenamiento=almacen)
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    return TestClient(app), almacen


def _pedir(client, sid="sin-77", rol="CLIENTE", uid="c-1"):
    r = client.post(
        "/graphql/app",
        json={"query": DETALLE, "variables": {"id": sid}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_el_cliente_ve_el_detalle_de_su_caso_con_poliza_ubicacion_y_fotos():
    client, almacen = _montar()
    with client:
        cuerpo = _pedir(client)
    assert "errors" not in cuerpo, cuerpo
    d = cuerpo["data"]["miSiniestro"]
    assert d["radicado"] == "SIN-2026-1077" and d["estado"] == "PENDIENTE"
    assert d["descripcion"] == "Me chocaron por detrás en un semáforo."
    assert d["ubicacion"]["ciudad"] == "Bogotá"
    assert d["poliza"] == {"numero": "SOL-2026-000007", "producto": "AUTO"}
    # Solo las fotos (no los documentos del operador), firmadas como el CLIENTE que consulta.
    assert [f["id"] for f in d["fotos"]] == ["ev-1"]
    assert d["fotos"][0]["url"] == "https://almacen.prueba/arc-1?firma=descarga"
    assert ("descarga", "CLIENTE", "c-1", "arc-1") in almacen.llamadas
    assert d["historial"] == [{"estado": "PENDIENTE", "en": "2026-11-04T15:00:00+00:00"}]
    assert d["resolucion"] is None
    assert d["apruebaAlVencer"] is True


def test_un_caso_ajeno_o_inexistente_es_null():
    client, _ = _montar()
    with client:
        assert _pedir(client, uid="otro-cliente")["data"]["miSiniestro"] is None
        assert _pedir(client, sid="no-existe")["data"]["miSiniestro"] is None


def test_un_caso_de_la_semilla_sin_cliente_no_se_le_muestra_a_nadie():
    client, _ = _montar(_siniestro(reportado_por=None))
    with client:
        assert _pedir(client)["data"]["miSiniestro"] is None


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_el_cliente(rol):
    client, _ = _montar()
    with client:
        cuerpo = _pedir(client, rol=rol)
    assert cuerpo["errors"][0]["message"] == MENSAJE_SIN_PERMISO


@pytest.mark.parametrize(
    ("tipo", "aprueba"),
    [
        ("PERDIDA_TOTAL_DANOS", True),
        ("HURTO_TOTAL", True),
        ("CHOQUE_PARCIAL", False),
        ("HURTO_PARCIAL", False),
        ("RESPONSABILIDAD_CIVIL", False),
        (None, True),  # póliza de vida: sin tipo de auto
    ],
)
def test_aprueba_al_vencer_salvo_cuando_el_operador_valora_el_dano(tipo, aprueba):
    client, _ = _montar(_siniestro(tipo_siniestro_auto=tipo))
    with client:
        assert _pedir(client)["data"]["miSiniestro"]["apruebaAlVencer"] is aprueba


def test_la_linea_de_tiempo_incluye_la_revision_del_perito_y_el_desenlace():
    resolucion = {
        "tipo": "APROBACION",
        "resuelta_en": "2026-11-08T10:00:00+00:00",
        "derivada_en": "2026-11-05T09:00:00+00:00",
        "indemnizacion": {"valor": "106650000.00", "moneda": "COP"},
        "plazo_dispersion": "3 días hábiles",
        "comentario": "Nota interna",
    }
    client, _ = _montar(_siniestro(estado="APROBADO", resolucion=resolucion))
    with client:
        d = _pedir(client)["data"]["miSiniestro"]
    assert [h["estado"] for h in d["historial"]] == ["PENDIENTE", "EN_REVISION", "APROBADO"]
    assert d["resolucion"]["indemnizacion"] == {"valor": "106650000.00", "moneda": "COP"}


def test_derivado_sin_resolver_queda_en_revision():
    resolucion = {
        "tipo": "DERIVACION",
        "resuelta_en": "2026-11-05T09:00:00+00:00",
        "derivada_en": "2026-11-05T09:00:00+00:00",
    }
    client, _ = _montar(_siniestro(estado="EN_REVISION", resolucion=resolucion))
    with client:
        d = _pedir(client)["data"]["miSiniestro"]
    assert [h["estado"] for h in d["historial"]] == ["PENDIENTE", "EN_REVISION"]


def test_foto_no_disponible_llega_con_url_vacia_sin_error():
    client, almacen = _montar()

    async def no_disponible(*_args):
        raise ArchivoRechazado("ARCHIVO_NO_DISPONIBLE")

    almacen.descarga = no_disponible
    with client:
        cuerpo = _pedir(client)
    assert "errors" not in cuerpo
    assert cuerpo["data"]["miSiniestro"]["fotos"][0]["url"] == ""


@pytest.mark.parametrize(
    "campo", ["sumaReclamada { valor }", "verificaciones { cumple }", "asegurado { nombre }"]
)
def test_el_cliente_no_puede_pedir_datos_del_operador(campo):
    client, _ = _montar()
    with client:
        r = client.post(
            "/graphql/app",
            json={"query": f'{{ miSiniestro(id: "sin-77") {{ {campo} }} }}'},
            headers={"Authorization": f"Bearer {token_de_prueba('CLIENTE', usuario_id='c-1')}"},
        )
    assert "Cannot query field" in r.json()["errors"][0]["message"]


def test_si_claims_no_responde_es_un_error_con_reintento_no_un_5xx():
    client, _ = _montar(claims_falla=True)
    with client:
        cuerpo = _pedir(client)
    assert cuerpo["data"] is None or cuerpo["data"]["miSiniestro"] is None
    assert cuerpo["errors"]
