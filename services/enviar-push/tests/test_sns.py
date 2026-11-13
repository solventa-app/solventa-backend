import pytest

from app.configuracion import Configuracion
from app.sns import EnviadorSnsBoto3, EnviadorStub, TokenInvalido, construir_enviador


def _destino(token: str = "tok-1") -> dict:
    return {"dispositivo_id": "d1", "token": token, "plataforma": "android"}


def _mensaje(**extra) -> dict:
    base = {
        "titulo": "Hola",
        "cuerpo": "Mundo",
        "idempotencia": "idem-1",
        "ruta": "/siniestros/1",
        "evento_id": "e1",
    }
    base.update(extra)
    return base


def test_enviador_stub_registra_el_envio() -> None:
    enviador = EnviadorStub()
    enviador.enviar(_destino(), _mensaje())
    assert len(enviador.enviados) == 1
    assert enviador.enviados[0]["gcm"]["notification"]["title"] == "Hola"


def test_enviador_stub_falla_con_token_marcado() -> None:
    enviador = EnviadorStub(fallos=frozenset({"tok-malo"}))
    with pytest.raises(TokenInvalido):
        enviador.enviar(_destino("tok-malo"), _mensaje())


def test_enviador_stub_push_silenciosa_sin_notification() -> None:
    enviador = EnviadorStub()
    enviador.enviar(_destino(), _mensaje(silenciosa=True))
    assert "notification" not in enviador.enviados[0]["gcm"]


def test_construir_enviador_stub_por_defecto() -> None:
    config = Configuracion.desde_entorno()
    assert isinstance(construir_enviador(config), EnviadorStub)


class _ClienteStsFalso:
    def __init__(self) -> None:
        self.llamadas = 0

    def assume_role_with_web_identity(
        self, RoleArn: str, RoleSessionName: str, WebIdentityToken: str
    ) -> dict:
        self.llamadas += 1
        return {
            "Credentials": {
                "AccessKeyId": "AKIA",
                "SecretAccessKey": "secreta",
                "SessionToken": f"sesion-{self.llamadas}",
            }
        }


class _ClienteSnsFalso:
    def __init__(self, credenciales: dict) -> None:
        self.credenciales = credenciales
        self.endpoints: dict[str, str] = {}
        self.publicados: list[dict] = []
        self.atributos_actualizados: list[tuple[str, dict]] = []

    def create_platform_endpoint(self, PlatformApplicationArn: str, Token: str) -> dict:
        arn = self.endpoints.setdefault(Token, f"arn:aws:sns:us-east-1:1:endpoint/APNS/app/{Token}")
        return {"EndpointArn": arn}

    def set_endpoint_attributes(self, EndpointArn: str, Attributes: dict) -> None:
        self.atributos_actualizados.append((EndpointArn, Attributes))

    def publish(self, TargetArn: str, MessageStructure: str, Message: str) -> dict:
        self.publicados.append({"TargetArn": TargetArn, "Message": Message})
        return {"MessageId": "m1"}


def _enviador_real(cliente_sts: _ClienteStsFalso, fabrica_sns) -> EnviadorSnsBoto3:
    return EnviadorSnsBoto3(
        platform_application_arn="arn:aws:sns:us-east-1:1:app/GCM/solventa",
        role_arn="arn:aws:iam::1:role/enviar-push",
        region="us-east-1",
        pedir_token_gcp=lambda audiencia: "token-gcp-falso",
        cliente_sts=cliente_sts,
        cliente_sns_fabrica=fabrica_sns,
        reloj=lambda: 0.0,
    )


def test_enviador_sns_pide_credenciales_por_federacion_y_publica() -> None:
    sts = _ClienteStsFalso()
    sns_fake = _ClienteSnsFalso({})
    enviador = _enviador_real(sts, lambda credenciales: sns_fake)
    enviador.enviar(_destino(), _mensaje())
    assert sts.llamadas == 1
    assert len(sns_fake.publicados) == 1


def test_enviador_sns_cachea_credenciales_dentro_de_la_vigencia() -> None:
    sts = _ClienteStsFalso()
    sns_fake = _ClienteSnsFalso({})
    enviador = _enviador_real(sts, lambda credenciales: sns_fake)
    enviador.enviar(_destino(), _mensaje())
    enviador.enviar(_destino(), _mensaje())
    assert sts.llamadas == 1


def test_enviador_sns_token_deshabilitado_levanta_token_invalido() -> None:
    sts = _ClienteStsFalso()

    class _SnsQueFalla(_ClienteSnsFalso):
        def publish(self, TargetArn: str, MessageStructure: str, Message: str) -> dict:
            raise RuntimeError("EndpointDisabled: Endpoint is disabled")

    enviador = _enviador_real(sts, lambda credenciales: _SnsQueFalla({}))
    with pytest.raises(TokenInvalido):
        enviador.enviar(_destino(), _mensaje())


def test_construir_enviador_real_con_sns_modo_real(monkeypatch) -> None:
    monkeypatch.setenv("SNS_MODO", "real")
    config = Configuracion.desde_entorno()
    assert isinstance(construir_enviador(config), EnviadorSnsBoto3)
