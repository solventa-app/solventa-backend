"""Onboarding del móvil por GraphQL (HU-M01, contrato v0.20): token del recorrido, consentimiento
antes de capturar, cuenta pendiente con scope limitado, login por canal y `actualizarSesion`."""

from fakes import AuthFalso, OnboardingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

ONB = (
    "... on Onboarding { id estado motivo intento intentosRestantes } "
    "... on ErrorOnboarding { codigo mensaje }"
)
CONFIRMAR = (
    "mutation($o: ID!, $a: ID!) { confirmarSubidaOnboarding(onboardingId: $o, archivoId: $a) { "
    + ONB
    + " } }"
)
CONSENTIR = (
    'mutation($o: ID!) { registrarConsentimientoOnboarding(onboardingId: $o, versionTexto: "v1")'
    + " { "
    + ONB
    + " } }"
)


def _pila():
    auth, onboarding = AuthFalso(), OnboardingFalso()
    cliente = TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(auth=auth, onboarding=onboarding),
        )
    )
    cliente.__enter__()  # corre el lifespan, que instala los clientes
    return cliente, auth, onboarding


def _gql(cliente, query, variables=None, canal="app", token=None, onb=None):
    cabeceras = {}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    if onb:
        cabeceras["X-Onboarding-Token"] = onb
    r = cliente.post(
        f"/graphql/{canal}", json={"query": query, "variables": variables or {}}, headers=cabeceras
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _iniciar(cliente) -> tuple[str, str]:
    datos = _gql(
        cliente,
        """mutation { iniciarOnboarding(entrada: {tipoDocumento: CC}) {
            ... on InicioOnboarding { token tokenVenceEn onboarding { id estado } } } }""",
    )["data"]["iniciarOnboarding"]
    assert datos["onboarding"]["estado"] == "INICIADO"
    return datos["onboarding"]["id"], datos["token"]


def _subir_todo(cliente, oid, tok) -> None:
    for paso in ("ANVERSO", "REVERSO", "ROSTRO", "VIDEO"):
        subida = _gql(
            cliente,
            """mutation($o: ID!, $p: PasoOnboarding!) {
                solicitarSubidaOnboarding(onboardingId: $o, paso: $p, tipoContenido: "image/jpeg",
                                          tamanoBytes: 100) {
                    ... on SubidaArchivo { archivo { id } url metodo encabezados { nombre valor } }
                    ... on ErrorOnboarding { codigo } } }""",
            {"o": oid, "p": paso},
            onb=tok,
        )["data"]["solicitarSubidaOnboarding"]
        assert subida["metodo"] == "PUT"
        _gql(
            cliente,
            CONFIRMAR,
            {"o": oid, "a": subida["archivo"]["id"]},
            onb=tok,
        )


def test_recorrido_completo_con_consentimiento_antes_de_capturar() -> None:
    cliente, auth, onboarding = _pila()
    oid, tok = _iniciar(cliente)
    # Sin consentimiento no hay subida (regla 7).
    sin = _gql(
        cliente,
        """mutation($o: ID!) { solicitarSubidaOnboarding(onboardingId: $o, paso: ANVERSO,
            tipoContenido: "image/jpeg", tamanoBytes: 1) { ... on ErrorOnboarding { codigo } } }""",
        {"o": oid},
        onb=tok,
    )["data"]["solicitarSubidaOnboarding"]
    assert sin["codigo"] == "SIN_CONSENTIMIENTO"
    _gql(
        cliente,
        CONSENTIR,
        {"o": oid},
        onb=tok,
    )
    registro = auth.registros_consentimiento[-1]
    assert (registro["finalidad"], registro["canal"]) == ("onboarding-biometrico", "APP")
    assert onboarding.sesiones[oid]["consentimiento_id"] == registro["id"]
    _subir_todo(cliente, oid, tok)
    enviado = _gql(
        cliente,
        f"mutation($o: ID!) {{ enviarVerificacion(onboardingId: $o) {{ {ONB} }} }}",
        {"o": oid},
        onb=tok,
    )["data"]["enviarVerificacion"]
    assert enviado["estado"] == "EN_VERIFICACION"
    leido = _gql(cliente, "query($o: ID!) { onboarding(id: $o) { estado } }", {"o": oid}, onb=tok)
    assert leido["data"]["onboarding"]["estado"] == "EN_VERIFICACION"


def test_token_del_recorrido_ajeno_o_ausente() -> None:
    cliente, _, _ = _pila()
    oid, _ = _iniciar(cliente)
    consulta = "query($o: ID!) { onboarding(id: $o) { estado } }"
    assert _gql(cliente, consulta, {"o": oid})["data"]["onboarding"] is None
    assert _gql(cliente, consulta, {"o": oid}, onb="otro")["data"]["onboarding"] is None
    error = _gql(
        cliente,
        f"mutation($o: ID!) {{ enviarVerificacion(onboardingId: $o) {{ {ONB} }} }}",
        {"o": oid},
        onb="otro",
    )["data"]["enviarVerificacion"]
    assert error["codigo"] == "TOKEN_INVALIDO" and error["mensaje"]


def test_servicio_caido_responde_error_con_reintento() -> None:
    cliente, _, onboarding = _pila()
    oid, tok = _iniciar(cliente)
    onboarding.falla = True
    error = _gql(
        cliente,
        f"mutation($o: ID!) {{ enviarVerificacion(onboardingId: $o) {{ {ONB} }} }}",
        {"o": oid},
        onb=tok,
    )["data"]["enviarVerificacion"]
    assert error["codigo"] == "NO_DISPONIBLE"


CREAR = """mutation($o: ID!) { crearCuentaOnboarding(onboardingId: $o,
    entrada: {correo: "ana@example.com", clave: "clave-segura-123"}) {
    ... on CuentaOnboarding { sesion { token usuario { id estado rol } } onboarding { id } }
    ... on ErrorOnboarding { codigo } } }"""


def test_cuenta_pendiente_solo_entra_a_su_verificacion() -> None:
    cliente, auth, onboarding = _pila()
    oid, tok = _iniciar(cliente)
    cuenta = _gql(cliente, CREAR, {"o": oid}, onb=tok)["data"]["crearCuentaOnboarding"]
    usuario = cuenta["sesion"]["usuario"]
    assert (usuario["rol"], usuario["estado"]) == ("CLIENTE", "PENDIENTE_VERIFICACION")
    assert onboarding.sesiones[oid]["usuario_id"] == usuario["id"]
    assert auth.canales[-1] == "app"
    sesion = cuenta["sesion"]["token"]
    # Con el scope pendiente: `yo` y `miVerificacion` sí; lo demás no.
    assert _gql(cliente, "{ yo { estado } }", token=sesion)["data"]["yo"]["estado"] == (
        "PENDIENTE_VERIFICACION"
    )
    mia = _gql(cliente, "{ miVerificacion { id estado } }", token=sesion)["data"]["miVerificacion"]
    assert mia["id"] == oid
    bloqueada = _gql(cliente, "{ propuestas { total } }", token=sesion)
    assert "verificando tu identidad" in bloqueada["errors"][0]["message"]
    # Una segunda cuenta con el mismo correo se rechaza con código.
    oid2, tok2 = _iniciar(cliente)
    repetida = _gql(cliente, CREAR, {"o": oid2}, onb=tok2)["data"]["crearCuentaOnboarding"]
    assert repetida["codigo"] == "CORREO_YA_REGISTRADO"


def test_segunda_cuenta_en_la_misma_sesion_no_deja_una_cuenta_huerfana_en_auth() -> None:
    cliente, auth, onboarding = _pila()
    oid, tok = _iniciar(cliente)
    _gql(cliente, CREAR, {"o": oid}, onb=tok)
    cuentas = len(auth.usuarios)
    otra = CREAR.replace("ana@example.com", "luis@example.com")
    repetida = _gql(cliente, otra, {"o": oid}, onb=tok)["data"]["crearCuentaOnboarding"]
    assert repetida["codigo"] == "SESION_CON_CUENTA"
    assert len(auth.usuarios) == cuentas  # no se creó nada en AUTH: el correo sigue libre


LOGIN = """mutation { iniciarSesion(
    entrada: {usuario: "ana@example.com", clave: "clave-segura-123"}) {
    ... on Sesion { usuario { estado } } ... on ErrorAutenticacion { mensaje codigo } } }"""


def test_login_de_cuenta_pendiente_depende_del_canal() -> None:
    cliente, _, _ = _pila()
    oid, tok = _iniciar(cliente)
    _gql(cliente, CREAR, {"o": oid}, onb=tok)
    web = _gql(cliente, LOGIN, canal="web")["data"]["iniciarSesion"]
    assert web["codigo"] == "VERIFICACION_PENDIENTE"
    app = _gql(cliente, LOGIN, canal="app")["data"]["iniciarSesion"]
    assert app["usuario"]["estado"] == "PENDIENTE_VERIFICACION"
    incorrecta = _gql(cliente, LOGIN.replace("clave-segura-123", "otra"), canal="web")
    assert incorrecta["data"]["iniciarSesion"]["codigo"] is None


def test_actualizar_sesion_conserva_la_expiracion_y_trae_el_estado_nuevo() -> None:
    cliente, auth, _ = _pila()
    oid, tok = _iniciar(cliente)
    cuenta = _gql(cliente, CREAR, {"o": oid}, onb=tok)["data"]["crearCuentaOnboarding"]
    usuario_id = cuenta["sesion"]["usuario"]["id"]
    auth.usuarios[usuario_id]["estado"] = "ACTIVO"  # el onboarding cerró la verificación
    nueva = _gql(
        cliente,
        "mutation { actualizarSesion { ... on Sesion { token expiraEn usuario { estado } } } }",
        token=cuenta["sesion"]["token"],
    )["data"]["actualizarSesion"]
    assert nueva["usuario"]["estado"] == "ACTIVO"
    assert auth.renovaciones[-1][0] == usuario_id
    # El token nuevo ya entra a lo demás.
    assert "errors" not in _gql(cliente, "{ yo { estado } }", token=nueva["token"])


def test_actualizar_sesion_exige_sesion() -> None:
    cliente, _, _ = _pila()
    respuesta = _gql(cliente, "mutation { actualizarSesion { ... on Sesion { token } } }")
    assert respuesta["errors"][0]["message"] == "Debes iniciar sesión."


def test_token_pendiente_no_crea_ni_consulta_ofertas() -> None:
    cliente, _, _ = _pila()
    token = token_de_prueba("CLIENTE", "u-x", scopes=["verificacion-pendiente"])
    respuesta = _gql(cliente, '{ oferta(id: "o-1") { id } }', token=token)
    assert "verificando tu identidad" in respuesta["errors"][0]["message"]


def test_reintentar_crear_cuenta_en_el_mismo_recorrido_es_idempotente() -> None:
    cliente, _, onboarding = _pila()
    oid, tok = _iniciar(cliente)
    primera = _gql(cliente, CREAR, {"o": oid}, onb=tok)["data"]["crearCuentaOnboarding"]
    segunda = _gql(cliente, CREAR, {"o": oid}, onb=tok)["data"]["crearCuentaOnboarding"]
    assert primera["sesion"]["usuario"]["id"] == segunda["sesion"]["usuario"]["id"]
    assert onboarding.sesiones[oid]["usuario_id"] == primera["sesion"]["usuario"]["id"]


def test_enviar_y_verificar_codigo_onboarding() -> None:
    from fakes import AclFalso

    acl = AclFalso()
    auth, onboarding = AuthFalso(), OnboardingFalso()
    cliente = TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(auth=auth, acl=acl, onboarding=onboarding),
        )
    )
    cliente.__enter__()
    q_envio = (
        'mutation { enviarCodigoOnboarding(correo: "ana.prueba@ejemplo.com") { '
        "... on CodigoOnboardingEnviado { correoEnmascarado venceEn } "
        "... on ErrorOnboarding { codigo } } }"
    )
    envio = _gql(cliente, q_envio)["data"]["enviarCodigoOnboarding"]
    assert envio["correoEnmascarado"] == "a•••@ejemplo.com"
    assert len(acl.notificaciones) == 1
    codigo = acl.notificaciones[0]["datos"]["codigo"]
    assert len(codigo) == 6

    q_verif = (
        "mutation($c: String!) { verificarCodigoOnboarding("
        'correo: "ana.prueba@ejemplo.com", codigo: $c) { '
        "... on CodigoOnboardingEnviado { correoEnmascarado venceEn } "
        "... on ErrorOnboarding { codigo mensaje } } }"
    )
    malo = _gql(
        cliente,
        q_verif,
        {"c": "000000" if codigo != "000000" else "999999"},
    )["data"]["verificarCodigoOnboarding"]
    assert malo["codigo"] == "CODIGO_INVALIDO"

    ok = _gql(cliente, q_verif, {"c": codigo})["data"]["verificarCodigoOnboarding"]
    assert ok["correoEnmascarado"] == "a•••@ejemplo.com"


def test_registrar_cuenta_sin_token_con_otp() -> None:
    from fakes import AclFalso

    acl = AclFalso()
    auth, onboarding = AuthFalso(), OnboardingFalso()
    cliente = TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(auth=auth, acl=acl, onboarding=onboarding),
        )
    )
    cliente.__enter__()
    # 1. Envía el código al correo
    q_envio = (
        'mutation { enviarCodigoOnboarding(correo: "carlos.nuevo@ejemplo.com") { '
        "... on CodigoOnboardingEnviado { correoEnmascarado venceEn } } }"
    )
    _gql(cliente, q_envio)
    codigo = acl.notificaciones[0]["datos"]["codigo"]

    # 2. Código incorrecto
    q_reg = (
        "mutation($c: String!) { registrarCuenta("
        'correo: "carlos.nuevo@ejemplo.com", clave: "ClaveSegura123!", codigo: $c) { '
        "... on Sesion { token usuario { id rol estado } } "
        "... on ErrorAutenticacion { codigo mensaje } } }"
    )
    malo = _gql(cliente, q_reg, {"c": "000000" if codigo != "000000" else "999999"})
    assert malo["data"]["registrarCuenta"]["codigo"] == "CODIGO_INVALIDO"

    # 3. Código correcto crea la cuenta y devuelve sesión PENDIENTE_VERIFICACION
    bueno = _gql(cliente, q_reg, {"c": codigo})
    sesion = bueno["data"]["registrarCuenta"]
    assert sesion["usuario"]["rol"] == "CLIENTE"
    assert sesion["usuario"]["estado"] == "PENDIENTE_VERIFICACION"
    assert sesion["token"] is not None

    # 4. Iniciar onboarding autenticado vincula la cuenta inmediatamente
    token_jwt = sesion["token"]
    q_onb = (
        "mutation { iniciarOnboarding(entrada: { tipoDocumento: CC }) { "
        "... on InicioOnboarding { onboarding { id estado } token } } }"
    )
    res_onb = _gql(cliente, q_onb, token=token_jwt)["data"]["iniciarOnboarding"]
    assert res_onb["onboarding"]["estado"] == "INICIADO"
    oid = res_onb["onboarding"]["id"]
    assert onboarding.sesiones[oid]["usuario_id"] == sesion["usuario"]["id"]

