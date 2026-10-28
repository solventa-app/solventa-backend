"""Firma y pago con el asesor o en la sesión del CLIENTE (HU-W03, F-B6)."""

import pytest
from fakes import (
    AuthFalso,
    PolicyFalso,
    RatingFalso,
    TicketsFalso,
    _usuario_publico,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.clientes import Acceso, ErrorServicio, FlujoRechazado
from app.configuracion import CLAVE_CLIENTE_ID_LOCAL, Configuracion
from app.identificadores import cliente_id_desde_documento
from app.main import create_app

DOCUMENTO = "1020304050"
TITULAR = cliente_id_desde_documento(DOCUMENTO, CLAVE_CLIENTE_ID_LOCAL)
TOKEN_ENLACE = "tok-de-enlace-largo-0000001"


def _propuesta(propuesta_id: str = "p-1", actor: str = "u-1", estado: str = "ACEPTADA") -> dict:
    return {
        **PolicyFalso._propuesta_en(propuesta_id, estado),
        "cliente_id": TITULAR,
        "actor_usuario_id": actor,
    }


def _montar(estado: str = "ACEPTADA", **extra):
    """Un AUTH con un CLIENTE verificado (`c-1`) y un POLICY con la propuesta `p-1` de `u-1`."""
    auth = AuthFalso()
    auth.usuarios["c-1"] = _usuario_publico("c-1", "ana.cliente", "CLIENTE")
    auth.documentos["c-1"] = DOCUMENTO
    policy = PolicyFalso()
    policy.propuestas["o-1"] = _propuesta(estado=estado)
    policy.enlaces[TOKEN_ENLACE] = "p-1"
    tickets = TicketsFalso()
    clientes = clientes_falsos(
        auth=auth, policy=policy, tickets=tickets, rating=RatingFalso(), **extra
    )
    return clientes, auth, policy, tickets


def _llamar(clientes, consulta: str, variables=None, rol=None, usuario="u-1", enlace=None):
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    cabeceras = {}
    if rol:
        cabeceras["Authorization"] = f"Bearer {token_de_prueba(rol, usuario)}"
    if enlace:
        cabeceras["X-Enlace-Firma"] = enlace
    with TestClient(app) as client:
        r = client.post(
            "/graphql/web",
            json={"query": consulta, "variables": variables or {}},
            headers=cabeceras,
        )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _pedidos(policy: PolicyFalso, operacion: str) -> list[tuple]:
    return [p for p in policy.pedidos if p[0] == operacion]


# ---------- ASESOR ----------

CODIGO = """mutation($id: ID!, $correo: String) {
  enviarCodigoVerificacion(propuestaId: $id, correo: $correo) {
    __typename
    ... on CodigoEnviado { propuestaId correoEnmascarado venceEn }
    ... on ErrorCodigo { mensaje puedeReintentar motivo intentosRestantes }
  }
}"""


def test_el_asesor_envia_el_codigo_a_su_propuesta() -> None:
    clientes, _, policy, _ = _montar()

    cuerpo = _llamar(clientes, CODIGO, {"id": "p-1", "correo": "nuevo@correo.com"}, "ASESOR")

    enviado = cuerpo["data"]["enviarCodigoVerificacion"]
    assert enviado["__typename"] == "CodigoEnviado"
    assert enviado["correoEnmascarado"] == "a•••@correo.com"
    assert _pedidos(policy, "enviar_codigo") == [("enviar_codigo", "p-1", "nuevo@correo.com")]


def test_el_asesor_no_opera_sobre_la_propuesta_de_otro() -> None:
    clientes, _, policy, _ = _montar()

    cuerpo = _llamar(clientes, CODIGO, {"id": "p-1"}, "ASESOR", usuario="u-otro")

    error = cuerpo["data"]["enviarCodigoVerificacion"]
    assert error["__typename"] == "ErrorCodigo"
    assert error["motivo"] == "PROPUESTA_NO_VALIDA"
    assert _pedidos(policy, "enviar_codigo") == []  # ni siquiera llegó a POLICY


def test_un_codigo_malo_dice_cuantos_intentos_quedan() -> None:
    clientes, _, policy, _ = _montar()
    policy.rechazos["verificar_codigo"] = FlujoRechazado("CODIGO_INVALIDO", intentos_restantes=2)
    consulta = """mutation { verificarCodigo(propuestaId: "p-1", codigo: "000000") {
      ... on ErrorCodigo { motivo intentosRestantes puedeReintentar } } }"""

    cuerpo = _llamar(clientes, consulta, rol="ASESOR")

    assert cuerpo["data"]["verificarCodigo"] == {
        "motivo": "CODIGO_INVALIDO",
        "intentosRestantes": 2,
        "puedeReintentar": True,
    }


def test_el_codigo_correcto_devuelve_la_propuesta_verificada() -> None:
    clientes, *_ = _montar()
    consulta = """mutation { verificarCodigo(propuestaId: "p-1", codigo: "123456") {
      ... on Propuesta { estado estadoVisible } } }"""

    cuerpo = _llamar(clientes, consulta, rol="ASESOR")

    assert cuerpo["data"]["verificarCodigo"] == {
        "estado": "IDENTIDAD_VERIFICADA",
        "estadoVisible": "EMITIENDO",
    }


EMITIR = """mutation { emitirPropuesta(propuestaId: "p-1") {
  __typename
  ... on EnlaceEnviado { correoEnmascarado venceEn propuesta { estado } }
  ... on Propuesta { estado }
  ... on ErrorEmision { mensaje puedeReintentar }
} }"""


def test_el_asesor_emite_y_el_enlace_sale_enmascarado_sin_la_url() -> None:
    clientes, _, policy, _ = _montar()

    cuerpo = _llamar(clientes, EMITIR, rol="ASESOR")

    emitido = cuerpo["data"]["emitirPropuesta"]
    assert emitido["__typename"] == "EnlaceEnviado"
    assert emitido["propuesta"] == {"estado": "EN_FIRMA"}
    assert "http" not in str(emitido)  # la URL del enlace solo va al correo del titular
    assert _pedidos(policy, "emitir") == [("emitir", "p-1", "ASESOR", None)]


def test_el_cliente_firma_con_el_titular_que_sale_de_auth() -> None:
    clientes, _, policy, _ = _montar()

    cuerpo = _llamar(clientes, EMITIR, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["emitirPropuesta"] == {"__typename": "Propuesta", "estado": "EN_FIRMA"}
    assert _pedidos(policy, "emitir") == [("emitir", "p-1", "CLIENTE", TITULAR)]


def test_un_cliente_sin_identidad_verificada_no_emite() -> None:
    clientes, auth, policy, _ = _montar()
    del auth.documentos["c-1"]

    cuerpo = _llamar(clientes, EMITIR, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["emitirPropuesta"]["__typename"] == "ErrorEmision"
    assert _pedidos(policy, "emitir") == []


def test_si_policy_cae_el_asesor_recibe_un_error_con_reintento() -> None:
    clientes, _, policy, _ = _montar()
    policy.caidas.add("emitir")

    cuerpo = _llamar(clientes, EMITIR, rol="ASESOR")

    assert cuerpo["data"]["emitirPropuesta"] == {
        "__typename": "ErrorEmision",
        "mensaje": "No pudimos procesar la solicitud. Intenta de nuevo.",
        "puedeReintentar": True,
    }


def test_reenviar_el_enlace_es_solo_del_asesor_dueno() -> None:
    clientes, _, policy, _ = _montar()
    consulta = """mutation { reenviarEnlaceFirma(propuestaId: "p-1") {
      __typename ... on EnlaceEnviado { correoEnmascarado } } }"""

    propio = _llamar(clientes, consulta, rol="ASESOR")
    ajeno = _llamar(clientes, consulta, rol="ASESOR", usuario="u-otro")
    cliente = _llamar(clientes, consulta, rol="CLIENTE", usuario="c-1")

    assert propio["data"]["reenviarEnlaceFirma"]["__typename"] == "EnlaceEnviado"
    assert ajeno["data"]["reenviarEnlaceFirma"]["__typename"] == "ErrorEmision"
    assert cliente["errors"][0]["message"] == "No tienes permiso para esta operación."
    assert len(_pedidos(policy, "reenviar_enlace")) == 1


# ---------- Página del enlace: token o sesión de CLIENTE (W03-33) ----------

ENLACE = """query($id: ID) { firmaPorEnlace(propuestaId: $id) {
  vigente venceEn documentoUrl propuesta { id estado }
  documento { version resumen } firma { referencia } poliza { numero }
} }"""


def test_el_titular_abre_la_pagina_con_el_token_y_sin_sesion() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, ENLACE, enlace=TOKEN_ENLACE)

    pagina = cuerpo["data"]["firmaPorEnlace"]
    assert pagina["vigente"] is True
    assert pagina["venceEn"] is not None
    assert pagina["propuesta"] == {"id": "p-1", "estado": "EN_FIRMA"}
    assert pagina["documento"]["version"] == "v1"
    assert pagina["documentoUrl"] == "/propuestas/p-1/contrato.pdf"
    assert _pedidos(policy, "estado_enlace")[0][2] == Acceso(enlace_token=TOKEN_ENLACE)


def test_con_el_token_se_ignora_el_propuesta_id_que_mande_el_cliente() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    _llamar(clientes, ENLACE, {"id": "p-ajena"}, enlace=TOKEN_ENLACE)

    assert _pedidos(policy, "estado_enlace")[0][1] == "p-1"


def test_el_token_funciona_aunque_la_autenticacion_sea_obligatoria(monkeypatch) -> None:
    monkeypatch.setenv("AUTENTICACION_USUARIOS", "obligatoria")
    clientes, *_ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, ENLACE, enlace=TOKEN_ENLACE)

    assert cuerpo["data"]["firmaPorEnlace"]["propuesta"]["id"] == "p-1"


def test_un_token_desconocido_responde_como_una_propuesta_inexistente() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, ENLACE, enlace="tok-que-no-existe-000000")

    assert cuerpo["data"] == {"firmaPorEnlace": None}


def test_el_cliente_con_sesion_ve_su_propuesta_sin_vencimiento() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, ENLACE, {"id": "p-1"}, rol="CLIENTE", usuario="c-1")

    pagina = cuerpo["data"]["firmaPorEnlace"]
    assert pagina["vigente"] is True
    assert pagina["venceEn"] is None
    assert _pedidos(policy, "estado_enlace")[0][2] == Acceso(titular_id=TITULAR)


def test_el_cliente_con_sesion_debe_indicar_la_propuesta() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, ENLACE, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"] == {"firmaPorEnlace": None}
    assert _pedidos(policy, "estado_enlace") == []


def test_sin_token_ni_sesion_de_cliente_no_hay_pagina() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")

    anonimo = _llamar(clientes, ENLACE, {"id": "p-1"})
    asesor = _llamar(clientes, ENLACE, {"id": "p-1"}, rol="ASESOR")

    assert anonimo["errors"][0]["message"] == "Debes iniciar sesión."
    assert asesor["errors"][0]["message"] == "No tienes permiso para esta operación."


def test_la_pagina_de_una_propuesta_emitida_trae_la_poliza_completa() -> None:
    clientes, _, policy, _ = _montar(estado="EMITIDA")
    policy.propuestas["o-1"]["poliza_id"] = "pol-1"
    policy.respuestas["estado_enlace"] = {
        "propuesta": policy.propuestas["o-1"],
        "vigente": False,
        "vence_en": None,
        "firma": {"referencia": "ev-1", "firmada_en": "2026-10-28T12:05:00+00:00"},
    }
    policy.polizas["pol-1"] = {
        "id": "pol-1",
        "numero": "SOL-2026-0001",
        "estado": "EMITIDA",
        "vigencia_desde": "2026-10-28T12:06:00+00:00",
        "vigencia_hasta": "2031-10-28T12:06:00+00:00",
        "prima": {"valor": "185000", "moneda": "COP"},
        "cobertura": {"valor": "200000000", "moneda": "COP"},
    }

    pagina = _llamar(clientes, ENLACE, enlace=TOKEN_ENLACE)["data"]["firmaPorEnlace"]

    assert pagina["poliza"] == {"numero": "SOL-2026-0001"}
    assert pagina["firma"] == {"referencia": "ev-1"}
    assert pagina["documento"] is None  # ya no se puede firmar


SUBIDA = """mutation($id: ID) { solicitarSubidaFirma(tipoContenido: "image/png", tamanoBytes: 5000,
  propuestaId: $id) { __typename
  ... on SubidaArchivo { url metodo archivo { id } encabezados { nombre valor } }
  ... on ErrorArchivo { codigo mensaje } } }"""


def test_el_titular_pide_la_url_para_subir_la_imagen_de_su_firma() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, SUBIDA, enlace=TOKEN_ENLACE)

    subida = cuerpo["data"]["solicitarSubidaFirma"]
    assert subida["__typename"] == "SubidaArchivo"
    assert subida["metodo"] == "PUT"
    assert subida["encabezados"] == [{"nombre": "Content-Type", "valor": "image/png"}]
    assert _pedidos(policy, "subida_firma")[0][3:] == ("image/png", 5000)


def test_sin_acceso_no_se_pide_ninguna_url_de_subida() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, SUBIDA, enlace="tok-que-no-existe-000000")

    assert cuerpo["data"]["solicitarSubidaFirma"]["__typename"] == "ErrorArchivo"
    assert _pedidos(policy, "subida_firma") == []


FIRMAR = """mutation($e: FirmarPorEnlaceInput!) { firmarPorEnlace(entrada: $e) {
  __typename
  ... on FirmaRegistrada { propuestaId referencia estado }
  ... on ErrorFirma { mensaje puedeReintentar motivo }
} }"""
ENTRADA_FIRMA = {
    "versionDocumento": "v1",
    "aceptaTerminos": True,
    "autorizaCobro": True,
    "modo": "IMAGEN",
    "imagenArchivoId": "arch-1",
}


def test_el_titular_firma_con_el_token_y_la_imagen_ya_subida() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(clientes, FIRMAR, {"e": ENTRADA_FIRMA}, enlace=TOKEN_ENLACE)

    assert cuerpo["data"]["firmarPorEnlace"] == {
        "__typename": "FirmaRegistrada",
        "propuestaId": "p-1",
        "referencia": "ev-1",
        "estado": "FIRMADA",
    }
    _, propuesta_id, acceso, entrada = _pedidos(policy, "firmar_enlace")[0]
    assert (propuesta_id, acceso) == ("p-1", Acceso(enlace_token=TOKEN_ENLACE))
    assert entrada == {
        "version_documento": "v1",
        "acepta_terminos": True,
        "autoriza_cobro": True,
        "modo": "IMAGEN",
        "imagen_archivo_id": "arch-1",
        "nombre_firma": None,
    }


@pytest.mark.parametrize(
    ("motivo", "esperado"),
    [
        ("ENLACE_VENCIDO", "ENLACE_VENCIDO"),
        ("IMAGEN_INVALIDA", "IMAGEN_INVALIDA"),
        ("COBRO_NO_AUTORIZADO", "COBRO_NO_AUTORIZADO"),
        ("VERSION_DOCUMENTO_DESACTUALIZADA", "VERSION_DOCUMENTO_DESACTUALIZADA"),
        ("PROPUESTA_NO_VALIDA", "PROPUESTA_NO_FIRMABLE"),
        ("MOTIVO_INTERNO_DE_UNDER", "SERVICIO_FIRMA_NO_DISPONIBLE"),
    ],
)
def test_cada_rechazo_de_la_firma_sale_con_su_motivo_del_contrato(motivo, esperado) -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")
    policy.rechazos["firmar_enlace"] = FlujoRechazado(motivo)

    cuerpo = _llamar(clientes, FIRMAR, {"e": ENTRADA_FIRMA}, enlace=TOKEN_ENLACE)

    error = cuerpo["data"]["firmarPorEnlace"]
    assert error["__typename"] == "ErrorFirma"
    assert error["motivo"] == esperado
    assert "UNDER" not in error["mensaje"]  # un código interno nunca sale


def test_el_cliente_firma_en_su_sesion() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")

    cuerpo = _llamar(
        clientes,
        FIRMAR,
        {"e": {**ENTRADA_FIRMA, "propuestaId": "p-1"}},
        rol="CLIENTE",
        usuario="c-1",
    )

    assert cuerpo["data"]["firmarPorEnlace"]["__typename"] == "FirmaRegistrada"
    assert _pedidos(policy, "firmar_enlace")[0][2] == Acceso(titular_id=TITULAR)


# ---------- Pago ----------

INICIAR = """mutation { iniciarPago {
  __typename ... on SesionPago { sesionId urlPago venceEn }
  ... on ErrorPago { mensaje puedeReintentar motivo fallosSeguidos } } }"""
CONFIRMAR = """mutation { confirmarPago(sesionId: "s-1") {
  __typename ... on Poliza { id numero estado prima { valor } documentoUrl }
  ... on ErrorPago { mensaje puedeReintentar motivo fallosSeguidos } } }"""


def _poliza_completa(policy: PolicyFalso) -> None:
    policy.polizas["pol-1"] = {
        "id": "pol-1",
        "numero": "SOL-2026-0001",
        "estado": "EMITIDA",
        "vigencia_desde": "2026-10-28T12:06:00+00:00",
        "vigencia_hasta": "2031-10-28T12:06:00+00:00",
        "prima": {"valor": "185000", "moneda": "COP"},
        "cobertura": {"valor": "200000000", "moneda": "COP"},
    }


def test_iniciar_el_pago_devuelve_la_pagina_de_la_pasarela_y_el_retorno_al_front() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")

    cuerpo = _llamar(clientes, INICIAR, enlace=TOKEN_ENLACE)

    sesion = cuerpo["data"]["iniciarPago"]
    assert sesion["__typename"] == "SesionPago"
    assert sesion["urlPago"] == "https://pasarela.example/checkout/s-1"
    _, propuesta_id, acceso, url_retorno, idioma = _pedidos(policy, "iniciar_pago")[0]
    assert (propuesta_id, idioma) == ("p-1", "es")
    assert url_retorno == "http://localhost:5173/pago/retorno/p-1"
    assert TOKEN_ENLACE not in url_retorno  # el token no viaja por la pasarela


def test_el_idioma_del_usuario_viaja_a_la_pagina_de_pago() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    with TestClient(app) as client:
        client.post(
            "/graphql/web",
            json={"query": INICIAR},
            headers={"X-Enlace-Firma": TOKEN_ENLACE, "Accept-Language": "en"},
        )

    assert _pedidos(policy, "iniciar_pago")[0][4] == "en"


def test_confirmar_el_pago_devuelve_la_poliza_emitida_en_la_misma_respuesta() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    _poliza_completa(policy)

    cuerpo = _llamar(clientes, CONFIRMAR, enlace=TOKEN_ENLACE)

    assert cuerpo["data"]["confirmarPago"] == {
        "__typename": "Poliza",
        "id": "pol-1",
        "numero": "SOL-2026-0001",
        "estado": "EMITIDA",
        "prima": {"valor": "185000"},
        "documentoUrl": "/polizas/pol-1/documento",
    }
    assert _pedidos(policy, "confirmar_pago")[0][3] == "s-1"


def _sesion(estado: str, motivo: str | None = None, fallos: int = 1) -> dict:
    return {
        "sesion": {
            "sesion_id": "s-1",
            "estado": estado,
            "motivo": motivo,
            "fallos_seguidos": fallos,
            "url_pago": None,
            "vence_en": None,
        },
        "poliza": None,
    }


@pytest.mark.parametrize(
    ("sesion", "motivo"),
    [
        (_sesion("rechazada", "fondos_insuficientes"), "FONDOS_INSUFICIENTES"),
        (_sesion("rechazada", "tarjeta_vencida"), "TARJETA_VENCIDA"),
        (_sesion("rechazada", "motivo_raro_de_la_pasarela"), "PAGO_RECHAZADO"),
        (_sesion("no_disponible", "pasarela_no_disponible"), "PASARELA_NO_DISPONIBLE"),
        (_sesion("no_disponible", "requiere_otra_tarjeta", 3), "REQUIERE_OTRA_TARJETA"),
        (_sesion("fallida"), "PASARELA_NO_DISPONIBLE"),
        # Sin respuesta al confirmar: la sesión sigue abierta, pero el titular debe saber por qué.
        (_sesion("abierta", "pasarela_no_disponible", 1), "PASARELA_NO_DISPONIBLE"),
        (_sesion("abierta", "requiere_otra_tarjeta", 3), "REQUIERE_OTRA_TARJETA"),
        (_sesion("vencida", "sesion_vencida", 0), "SESION_VENCIDA"),
    ],
)
def test_un_pago_que_no_se_cobro_explica_que_hacer(sesion, motivo) -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    policy.respuestas["confirmar_pago"] = sesion

    error = _llamar(clientes, CONFIRMAR, enlace=TOKEN_ENLACE)["data"]["confirmarPago"]

    assert error["__typename"] == "ErrorPago"
    assert error["motivo"] == motivo
    assert error["puedeReintentar"] is True
    assert error["fallosSeguidos"] == sesion["sesion"]["fallos_seguidos"]


def test_volver_de_la_pasarela_sin_pagar_deja_la_sesion_abierta() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    policy.respuestas["confirmar_pago"] = _sesion("abierta", None, 0)

    error = _llamar(clientes, CONFIRMAR, enlace=TOKEN_ENLACE)["data"]["confirmarPago"]

    assert error["__typename"] == "ErrorPago"
    assert error["motivo"] is None
    assert error["puedeReintentar"] is True


def test_si_se_cobro_pero_no_se_puede_leer_la_poliza_se_pide_reintentar() -> None:
    clientes, *_ = _montar(estado="FIRMADA")  # la póliza resumida existe, la completa no

    error = _llamar(clientes, CONFIRMAR, enlace=TOKEN_ENLACE)["data"]["confirmarPago"]

    assert error["__typename"] == "ErrorPago"
    assert error["puedeReintentar"] is True


def test_iniciar_el_pago_de_algo_ya_emitido_no_abre_otra_sesion() -> None:
    clientes, _, policy, _ = _montar(estado="EMITIDA")
    _poliza_completa(policy)
    policy.respuestas["iniciar_pago"] = {"sesion": None, "poliza": {"id": "pol-1"}}

    error = _llamar(clientes, INICIAR, enlace=TOKEN_ENLACE)["data"]["iniciarPago"]

    assert error["__typename"] == "ErrorPago"
    assert error["puedeReintentar"] is False


def test_si_policy_cae_al_pagar_no_hay_5xx_y_se_puede_reintentar() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    policy.caidas.add("iniciar_pago")

    error = _llamar(clientes, INICIAR, enlace=TOKEN_ENLACE)["data"]["iniciarPago"]

    assert error["__typename"] == "ErrorPago"
    assert error["puedeReintentar"] is True


def test_un_enlace_vencido_al_pagar_tiene_su_motivo() -> None:
    clientes, _, policy, _ = _montar(estado="FIRMADA")
    policy.rechazos["iniciar_pago"] = FlujoRechazado("ENLACE_VENCIDO")

    error = _llamar(clientes, INICIAR, enlace=TOKEN_ENLACE)["data"]["iniciarPago"]

    assert error["motivo"] == "ENLACE_VENCIDO"
    assert error["puedeReintentar"] is False


# ---------- «Mis pólizas»: la lista sale del rol del token (W03-35) ----------

LISTA = """query($estado: EstadoVisible) { propuestas(estado: $estado, pagina: 1, tamano: 5) {
  total pagina tamano
  propuestas { id estadoVisible nombreTitular nombreAsesor poliza { numero prima { valor } } }
} }"""


def _pagina_de_policy(policy: PolicyFalso) -> None:
    propuesta = {
        **_propuesta(estado="EMITIDA"),
        "poliza_id": "pol-1",
        "actor_rol": "ASESOR",
        "nombre_titular": "Ana Pérez",
        "poliza": {
            "id": "pol-1",
            "numero": "SOL-2026-0001",
            "vigencia_desde": "2026-10-28T12:06:00+00:00",
            "vigencia_hasta": "2031-10-28T12:06:00+00:00",
        },
    }
    policy.respuestas["listar"] = {"propuestas": [propuesta], "total": 1, "pagina": 1, "tamano": 5}


def test_el_cliente_ve_las_de_su_titular_y_no_ve_datos_personales() -> None:
    clientes, _, policy, _ = _montar()
    _pagina_de_policy(policy)

    cuerpo = _llamar(clientes, LISTA, rol="CLIENTE", usuario="c-1")

    fila = cuerpo["data"]["propuestas"]["propuestas"][0]
    assert fila["estadoVisible"] == "EMITIDA"
    assert fila["poliza"] == {"numero": "SOL-2026-0001", "prima": {"valor": "185000"}}
    assert fila["nombreTitular"] is None and fila["nombreAsesor"] is None
    assert _pedidos(policy, "listar") == [("listar", TITULAR, None, None, 1, 5)]


def test_el_asesor_ve_las_que_acepto_con_el_nombre_del_titular() -> None:
    clientes, _, policy, _ = _montar()
    _pagina_de_policy(policy)

    cuerpo = _llamar(clientes, LISTA, {"estado": "EMITIDA"}, rol="ASESOR", usuario="u-1")

    fila = cuerpo["data"]["propuestas"]["propuestas"][0]
    assert fila["nombreTitular"] == "Ana Pérez"
    assert fila["nombreAsesor"] is None  # solo para el BACKOFFICE
    assert _pedidos(policy, "listar") == [("listar", None, "u-1", "EMITIDA", 1, 5)]


def test_el_backoffice_las_ve_todas_con_el_nombre_del_asesor() -> None:
    clientes, auth, policy, _ = _montar()
    auth.nombres["u-1"] = "Luis Asesor"
    _pagina_de_policy(policy)

    cuerpo = _llamar(clientes, LISTA, rol="BACKOFFICE", usuario="u-bo")

    fila = cuerpo["data"]["propuestas"]["propuestas"][0]
    assert (fila["nombreTitular"], fila["nombreAsesor"]) == ("Ana Pérez", "Luis Asesor")
    assert _pedidos(policy, "listar") == [("listar", None, None, None, 1, 5)]


def test_un_cliente_sin_identidad_verificada_no_ve_ninguna() -> None:
    clientes, auth, policy, _ = _montar()
    del auth.documentos["c-1"]
    _pagina_de_policy(policy)

    cuerpo = _llamar(clientes, LISTA, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["propuestas"]["propuestas"] == []
    assert _pedidos(policy, "listar") == []


def test_el_rol_manda_aunque_el_cliente_pida_otra_cosa() -> None:
    """No existe un argumento para elegir de quién son: el filtro sale solo del token."""
    clientes, *_ = _montar()

    cuerpo = _llamar(
        clientes,
        '{ propuestas(clienteId: "otro") { total } }',
        rol="CLIENTE",
        usuario="c-1",
    )

    assert "clienteId" in cuerpo["errors"][0]["message"]


def test_si_policy_cae_la_lista_da_un_error_publico_con_mensaje() -> None:
    clientes, _, policy, _ = _montar()
    policy.caidas.add("listar")

    cuerpo = _llamar(clientes, LISTA, rol="ASESOR")

    assert (
        cuerpo["errors"][0]["message"] == "No pudimos consultar las propuestas. Intenta de nuevo."
    )


def test_la_lista_exige_sesion() -> None:
    clientes, *_ = _montar()

    assert _llamar(clientes, LISTA)["errors"][0]["message"] == "Debes iniciar sesión."


# ---------- Avisos en vivo ----------

TICKET = "mutation { ticketAvisos { ticket url vence_en: venceEn } }"


def test_el_ticket_lleva_quien_escucha_y_el_titular_del_cliente() -> None:
    clientes, _, _, tickets = _montar()

    cuerpo = _llamar(clientes, TICKET, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["ticketAvisos"]["ticket"] == "ticket-1"
    assert cuerpo["data"]["ticketAvisos"]["url"] == "http://localhost:8011/avisos/stream"
    assert tickets.emitidos == [("CLIENTE", "c-1", TITULAR)]


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_el_asesor_y_el_backoffice_piden_ticket_sin_titular(rol: str) -> None:
    clientes, _, _, tickets = _montar()

    _llamar(clientes, TICKET, rol=rol, usuario="u-1")

    assert tickets.emitidos == [(rol, "u-1", None)]


def test_el_ticket_exige_sesion() -> None:
    clientes, *_ = _montar()

    assert _llamar(clientes, TICKET)["errors"][0]["message"] == "Debes iniciar sesión."


def test_sin_redis_el_ticket_da_un_error_publico_y_no_un_5xx() -> None:
    clientes, *_ = _montar()
    clientes.tickets = TicketsFalso(falla=True)

    cuerpo = _llamar(clientes, TICKET, rol="ASESOR")

    assert cuerpo["errors"][0]["message"] == "No pudimos abrir los avisos. Intenta de nuevo."


# ---------- La cédula del CLIENTE sale de AUTH (W03-34) ----------

OFERTA = """mutation($doc: String) { solicitarOferta(entrada: {consentimientoId: "c-1",
  valorCredito: "100000000", plazoMeses: 60, direccionInmueble: "Calle 1",
  documentoIdentidad: $doc}) {
  __typename ... on Oferta { id } ... on ErrorOferta { mensaje motivo } } }"""


def _cotizar(doc: str | None, rol: str, usuario: str, auth_falla: bool = False):
    clientes, auth, *_ = _montar()
    auth.falla = auth_falla
    cuerpo = _llamar(clientes, OFERTA, {"doc": doc}, rol=rol, usuario=usuario)
    return cuerpo["data"]["solicitarOferta"], clientes


def test_un_cliente_cotiza_sin_escribir_su_cedula() -> None:
    resultado, _ = _cotizar(None, "CLIENTE", "c-1")

    assert resultado["__typename"] == "Oferta"


def test_un_cliente_puede_escribir_su_propia_cedula() -> None:
    resultado, _ = _cotizar(DOCUMENTO, "CLIENTE", "c-1")

    assert resultado["__typename"] == "Oferta"


def test_un_cliente_no_cotiza_con_la_cedula_de_otro() -> None:
    resultado, clientes = _cotizar("9988776655", "CLIENTE", "c-1")

    assert resultado == {
        "__typename": "ErrorOferta",
        "mensaje": "El documento no coincide con el de tu cuenta.",
        "motivo": "DATOS_INSUFICIENTES",
    }
    assert clientes.acl.llamadas == []  # no se consultó ninguna fuente


def test_el_asesor_sin_cedula_recibe_el_error() -> None:
    resultado, _ = _cotizar(None, "ASESOR", "u-1")

    assert resultado["__typename"] == "ErrorOferta"
    assert resultado["motivo"] == "DATOS_INSUFICIENTES"


def test_el_asesor_cotiza_con_la_cedula_que_escribe() -> None:
    resultado, _ = _cotizar("9988776655", "ASESOR", "u-1")

    assert resultado["__typename"] == "Oferta"


def test_si_auth_cae_el_cliente_sin_cedula_recibe_el_error_de_siempre() -> None:
    resultado, _ = _cotizar(None, "CLIENTE", "c-1", auth_falla=True)

    assert resultado["__typename"] == "ErrorOferta"
    assert resultado["motivo"] == "DATOS_INSUFICIENTES"


CONSENTIR = """mutation($doc: String) { registrarConsentimiento(entrada: {finalidad: "cotizacion",
  versionTexto: "v1", documentoIdentidad: $doc}) { id } }"""


def test_el_consentimiento_del_cliente_queda_a_nombre_de_su_titular() -> None:
    clientes, auth, *_ = _montar()

    cuerpo = _llamar(clientes, CONSENTIR, {"doc": None}, rol="CLIENTE", usuario="c-1")

    assert "errors" not in cuerpo
    assert auth.registros_consentimiento[0]["titular_id"] == TITULAR


def test_el_cliente_no_da_consentimiento_a_nombre_de_otro() -> None:
    clientes, auth, *_ = _montar()

    cuerpo = _llamar(clientes, CONSENTIR, {"doc": "9988776655"}, rol="CLIENTE", usuario="c-1")

    assert cuerpo["errors"][0]["message"] == "El documento no coincide con el de tu cuenta."
    assert auth.registros_consentimiento == []


# ---------- Revocación (W01-A2) ----------

REVOCAR = 'mutation { revocarConsentimiento(id: "c-1") { estado } }'


def _con_titular(auth: AuthFalso) -> None:
    auth.consentimientos["c-1"] = {**auth.consentimientos["c-1"], "titular_id": TITULAR}


def test_revocar_avisa_a_policy_y_al_acl_en_paralelo() -> None:
    clientes, auth, policy, _ = _montar()
    _con_titular(auth)

    cuerpo = _llamar(clientes, REVOCAR, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["revocarConsentimiento"] == {"estado": "REVOCADO"}
    assert policy.revocaciones == [(TITULAR, "c-1")]
    assert clientes.acl.invalidaciones == [TITULAR]


def test_si_policy_cae_la_revocacion_no_falla() -> None:
    clientes, auth, policy, _ = _montar()
    _con_titular(auth)
    policy.caidas.add("consentimiento_revocado")

    cuerpo = _llamar(clientes, REVOCAR, rol="CLIENTE", usuario="c-1")

    assert cuerpo["data"]["revocarConsentimiento"] == {"estado": "REVOCADO"}
    assert clientes.acl.invalidaciones == [TITULAR]  # lo demás se hizo


def test_una_revocacion_sin_titular_no_llama_a_policy() -> None:
    clientes, _, policy, _ = _montar()

    _llamar(clientes, REVOCAR, rol="CLIENTE", usuario="c-1")

    assert _pedidos(policy, "consentimiento_revocado") == []


# ---------- aceptarOferta con contacto ----------

ACEPTAR = """mutation($c: ContactoTitularInput) { aceptarOferta(ofertaId: "o-1", contacto: $c) {
  __typename ... on Propuesta { id } ... on ErrorAceptacion { mensaje puedeReintentar } } }"""
CONTACTO = {"nombre": "Ana Pérez", "correo": "ana@correo.com"}


def _aceptable() -> tuple:
    clientes, auth, policy, tickets = _montar()
    policy.propuestas.clear()
    clientes.rating = RatingFalso(
        guardadas={
            "o-1": {
                "id": "o-1",
                "tipo": "COMPLETA",
                "clienteId": TITULAR,
                "consentimientoId": "c-1",
            }
        }
    )
    return clientes, policy


def test_el_asesor_acepta_con_el_contacto_del_titular() -> None:
    clientes, policy = _aceptable()

    cuerpo = _llamar(clientes, ACEPTAR, {"c": CONTACTO}, rol="ASESOR")

    assert cuerpo["data"]["aceptarOferta"]["__typename"] == "Propuesta"
    assert policy.contactos_recibidos == [CONTACTO]


def test_el_contacto_que_manda_un_cliente_se_ignora() -> None:
    """El contacto de un CLIENTE sale de AUTH al usarlo (W03-25), no de lo que escriba."""
    clientes, policy = _aceptable()

    _llamar(clientes, ACEPTAR, {"c": CONTACTO}, rol="CLIENTE", usuario="c-1")

    assert policy.contactos_recibidos == [None]


@pytest.mark.parametrize(
    "contacto",
    [
        {"nombre": "A", "correo": "ana@correo.com"},
        {"nombre": "Ana Pérez", "correo": "no-es-un-correo"},
    ],
)
def test_un_contacto_invalido_se_rechaza_antes_de_llegar_a_policy(contacto) -> None:
    clientes, policy = _aceptable()

    error = _llamar(clientes, ACEPTAR, {"c": contacto}, rol="ASESOR")["data"]["aceptarOferta"]

    assert error["__typename"] == "ErrorAceptacion"
    assert error["mensaje"] == "Revisa el nombre y el correo del titular."
    assert policy.llamadas == []


# ---------- El contrato en PDF ----------


def _pdf(clientes, propuesta: str = "p-1", enlace=None, rol=None, usuario="u-1"):
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    cabeceras = {}
    if enlace:
        cabeceras["X-Enlace-Firma"] = enlace
    if rol:
        cabeceras["Authorization"] = f"Bearer {token_de_prueba(rol, usuario)}"
    with TestClient(app) as client:
        return client.get(f"/propuestas/{propuesta}/contrato.pdf", headers=cabeceras)


def test_el_contrato_en_pdf_se_entrega_con_el_token_del_enlace() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")

    r = _pdf(clientes, enlace=TOKEN_ENLACE)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"%PDF")


def test_el_contrato_en_pdf_se_entrega_al_cliente_con_sesion() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")

    assert _pdf(clientes, rol="CLIENTE", usuario="c-1").status_code == 200


def test_sin_acceso_el_contrato_responde_404() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")

    assert _pdf(clientes).status_code == 404
    assert _pdf(clientes, enlace="tok-que-no-existe-000000").status_code == 404
    assert _pdf(clientes, rol="ASESOR").status_code == 404


def test_si_policy_cae_el_contrato_responde_503_con_reintento() -> None:
    clientes, _, policy, _ = _montar(estado="EN_FIRMA")
    policy.caidas.add("contrato_pdf")

    r = _pdf(clientes, enlace=TOKEN_ENLACE)

    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"


def test_un_token_de_sesion_invalido_en_el_contrato_responde_401() -> None:
    clientes, *_ = _montar(estado="EN_FIRMA")
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes)
    with TestClient(app) as client:
        r = client.get("/propuestas/p-1/contrato.pdf", headers={"Authorization": "Bearer basura"})

    assert r.status_code == 401


def test_error_de_servicio_no_se_traga_el_tipo() -> None:
    """`ErrorServicio` de la lista sube como error público, no como 5xx (cubierto arriba); aquí
    solo se fija que el doble de POLICY lo lance, para que esas pruebas midan algo."""
    policy = PolicyFalso()
    policy.caidas.add("listar")

    with pytest.raises(ErrorServicio):
        import asyncio

        asyncio.run(policy.listar_propuestas(None, None, None, 1, 5))
