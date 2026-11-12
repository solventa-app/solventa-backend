"""Indemnización y cobertura paramétrica por GraphQL (KAN-259, HU-M04 Fase 5, contrato v0.48):
solo CLIENTE; CLAIMS guarda el siniestro paramétrico y POLICY la cobertura."""

from fakes import (
    AuthFalso,
    ClaimsFalso,
    PolicyFalso,
    _usuario_publico,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.configuracion import Configuracion
from app.main import create_app

INDEMNIZACION = """query($id: ID!) { indemnizacion(id: $id) {
  id tipo referenciaEvento detalleEvento fechaEvento pagadoEn estado polizaAnterior
  metodoTransferencia
  monto { valor moneda } } }"""

MI_COBERTURA = """{ miCoberturaParametrica {
  producto numeroPoliza vigente vigenteHasta
  tope { valor moneda }
  movimientos { id tipo referenciaEvento detalleEvento estado polizaAnterior
    monto { valor moneda } } } }"""


def siniestro_parametrico(**over) -> dict:
    base = {
        "id": "sin-par-1",
        "radicado": "SIN-2026-1001",
        "poliza_id": "pol-par-1",
        "estado": "APROBADO",
        "asegurado": {"nombre": "Cobertura paramétrica", "documento": "N/A"},
        "cobertura_aplicada": "Cobertura paramétrica de vuelo",
        "entidad_acreedora": "Por confirmar con el asegurado",
        "ocurrido_en": "2026-11-11T08:00:00+00:00",
        "notificado_en": "2026-11-11T08:00:05+00:00",
        "ubicacion": {"tipo": "VUELO", "clave": "AV9340:2026-11-11"},
        "descripcion": "Siniestro paramétrico automático: vuelo con retraso sobre el umbral.",
        "suma_reclamada": {"valor": "500000", "moneda": "COP"},
        "vence_dictamen_en": "2026-11-11T08:00:05+00:00",
        "evidencias": [],
        "verificaciones": [],
        "tipo_siniestro_auto": None,
        "origen": "PARAMETRICO",
        "evento_parametrico_id": "evt-vuelo-1",
        "evento_valor": 145.0,
        "evento_umbral": 120,
        "evento_variable": None,
        "evento_cancelado": False,
        "vigencia_hasta_cobertura": "2027-01-01T00:00:00+00:00",
        "resolucion": {
            "tipo": "APROBACION",
            "resuelta_en": "2026-11-11T08:00:05+00:00",
            "notificacion": "Enviada al asegurado y a la entidad acreedora",
            "indemnizacion": {"valor": "500000", "moneda": "COP"},
            "metodo_transferencia": "ACH Colombia Interbancario",
            "plazo_dispersion": "1 a 3 días hábiles",
            "perito": None,
            "automatica": True,
        },
    }
    base.update(over)
    return base


def cobertura_vuelo(**over) -> dict:
    base = {
        "id": "cob-1",
        "poliza_id": "pol-par-1",
        "tipo": "VUELO",
        "monto_fijo": {"valor": "500000", "moneda": "COP"},
        "vigencia_desde": "2026-01-01T00:00:00+00:00",
        "vigencia_hasta": "2027-01-01T00:00:00+00:00",
        "estado": "VIGENTE",
    }
    base.update(over)
    return base


def _montar(claims=None, policy=None, documento="1020304050"):
    auth = AuthFalso()
    auth.usuarios["c-1"] = _usuario_publico("c-1", "ana.cliente", "CLIENTE")
    if documento:
        auth.documentos["c-1"] = documento
    claims = claims if claims is not None else ClaimsFalso()
    policy = policy if policy is not None else PolicyFalso()
    clientes = clientes_falsos(auth=auth, claims=claims, policy=policy)
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    return TestClient(app), claims, policy


def _post(client, query, variables=None, rol="CLIENTE", usuario_id="c-1"):
    r = client.post(
        "/graphql/app",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=usuario_id)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _con_propuesta_parametrica(policy: PolicyFalso) -> None:
    policy.respuestas["listar"] = {
        "propuestas": [{"poliza": {"id": "pol-par-1", "numero": "POL-9001"}}],
        "total": 1,
        "pagina": 1,
        "tamano": 50,
    }
    policy.polizas["pol-par-1"] = {"firmante_id": "c-1", "actor_usuario_id": None}


def test_indemnizacion_de_vuelo_con_retraso():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico()
    policy = PolicyFalso()
    policy.polizas["pol-par-1"] = {"firmante_id": "c-1", "actor_usuario_id": None}
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-par-1"})["data"]["indemnizacion"]
    assert datos["tipo"] == "VUELO_RETRASO"
    assert datos["referenciaEvento"] == "AV9340"
    assert datos["detalleEvento"] == "+145min"
    assert datos["estado"] == "EN_PROCESO"
    assert datos["polizaAnterior"] is False
    assert datos["metodoTransferencia"] == "ACH Colombia Interbancario"
    assert datos["monto"] == {"valor": "500000", "moneda": "COP"}


def test_indemnizacion_de_vuelo_cancelado():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico(evento_cancelado=True)
    policy = PolicyFalso()
    policy.polizas["pol-par-1"] = {"firmante_id": "c-1", "actor_usuario_id": None}
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-par-1"})["data"]["indemnizacion"]
    assert datos["tipo"] == "VUELO_CANCELADO"
    assert datos["detalleEvento"] == "Cancelado"


def test_indemnizacion_de_clima():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-2"] = siniestro_parametrico(
        id="sin-par-2",
        poliza_id="pol-par-2",
        ubicacion={"tipo": "CLIMA", "clave": "4.62,-74.09"},
        evento_valor=62.0,
        evento_variable="PRECIPITACION_MM",
        evento_cancelado=None,
    )
    policy = PolicyFalso()
    policy.polizas["pol-par-2"] = {"firmante_id": "c-1", "actor_usuario_id": None}
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-par-2"})["data"]["indemnizacion"]
    assert datos["tipo"] == "CLIMA"
    assert datos["referenciaEvento"] == "4.62,-74.09"
    assert datos["detalleEvento"] == "62.0 mm"


def test_indemnizacion_vencida_marca_poliza_anterior():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico(
        vigencia_hasta_cobertura="2020-01-01T00:00:00+00:00"
    )
    policy = PolicyFalso()
    policy.polizas["pol-par-1"] = {"firmante_id": "c-1", "actor_usuario_id": None}
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-par-1"})["data"]["indemnizacion"]
    assert datos["polizaAnterior"] is True


def test_indemnizacion_siniestro_no_parametrico_es_null():
    """Un siniestro normal (reportado o de la semilla, sin `origen == "PARAMETRICO"`) nunca sale
    por esta query."""
    client, _, _ = _montar()  # el ClaimsFalso por defecto trae "sin-01", no paramétrico
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-01"})["data"]["indemnizacion"]
    assert datos is None


def test_indemnizacion_inexistente_es_null():
    client, _, _ = _montar()
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "no-existe"})["data"]["indemnizacion"]
    assert datos is None


def test_indemnizacion_de_otro_cliente_es_null():
    """La póliza del siniestro no es del CLIENTE del token: nunca se filtra el expediente de
    otro cliente."""
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico()
    policy = PolicyFalso()
    policy.polizas["pol-par-1"] = {"firmante_id": "otro-cliente", "actor_usuario_id": "otro-act"}
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, INDEMNIZACION, {"id": "sin-par-1"})["data"]["indemnizacion"]
    assert datos is None


def test_indemnizacion_exige_rol_cliente():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico()
    client, _, _ = _montar(claims=claims)
    with client:
        respuesta = _post(client, INDEMNIZACION, {"id": "sin-par-1"}, rol="BACKOFFICE")
    assert respuesta["errors"][0]["message"] == MENSAJE_SIN_PERMISO


def test_mi_cobertura_parametrica_con_movimientos():
    claims = ClaimsFalso()
    claims.siniestros["sin-par-1"] = siniestro_parametrico()
    policy = PolicyFalso()
    _con_propuesta_parametrica(policy)
    policy.coberturas_parametricas["pol-par-1"] = [cobertura_vuelo()]
    client, _, _ = _montar(claims=claims, policy=policy)
    with client:
        datos = _post(client, MI_COBERTURA)["data"]["miCoberturaParametrica"]
    assert datos["producto"] == "Cobertura paramétrica de vuelo"
    assert datos["numeroPoliza"] == "POL-9001"
    assert datos["vigente"] is True
    assert datos["tope"] == {"valor": "500000", "moneda": "COP"}
    assert len(datos["movimientos"]) == 1
    assert datos["movimientos"][0]["id"] == "sin-par-1"


def test_mi_cobertura_parametrica_sin_coberturas_es_null():
    policy = PolicyFalso()
    _con_propuesta_parametrica(policy)  # tiene póliza, pero sin cobertura paramétrica
    client, _, _ = _montar(policy=policy)
    with client:
        datos = _post(client, MI_COBERTURA)["data"]["miCoberturaParametrica"]
    assert datos is None


def test_mi_cobertura_parametrica_sin_polizas_es_null():
    policy = PolicyFalso()
    policy.respuestas["listar"] = {"propuestas": [], "total": 0, "pagina": 1, "tamano": 50}
    client, _, _ = _montar(policy=policy)
    with client:
        datos = _post(client, MI_COBERTURA)["data"]["miCoberturaParametrica"]
    assert datos is None


def test_mi_cobertura_parametrica_exige_rol_cliente():
    client, _, _ = _montar()
    with client:
        respuesta = _post(client, MI_COBERTURA, rol="BACKOFFICE")
    assert respuesta["errors"][0]["message"] == MENSAJE_SIN_PERMISO
