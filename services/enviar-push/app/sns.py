"""Envío por AWS SNS (KAN-259, D1/D2/D7): el port a Python de `proceso.js` del zip de referencia,
pero sin temas ni endpoints que mantener sincronizados con una base — `create_platform_endpoint`
es idempotente por token (AWS devuelve el mismo `EndpointArn` si ya existe con los mismos
atributos), así que no hace falta guardar el ARN en ninguna parte: se resuelve en cada envío.

D7 (GCP -> AWS sin llaves guardadas): la función pide a GCP un token de identidad para la
audiencia `sts.amazonaws.com` (igual que `autenticacion_servicios.py` pide uno para otro
servicio de Cloud Run) y lo cambia por credenciales temporales de AWS con
`sts:AssumeRoleWithWebIdentity`. Nada de `aws_access_key_id` en el entorno ni en Secret Manager."""

import json
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("enviar_push.sns")

AUDIENCIA_STS = "sts.amazonaws.com"
VIGENCIA_CREDENCIALES_SEGUNDOS = 50 * 60


class TokenInvalido(Exception):
    """SNS dice que el endpoint está deshabilitado o no existe: el token ya no sirve."""


class ErrorEnvio(Exception):
    """Fallo transitorio (red, cuota, 5xx de SNS): vale la pena reintentar."""


class PuertoEnviadorPush(Protocol):
    def enviar(self, destino: dict[str, Any], mensaje: dict[str, Any]) -> None: ...


def _payload_gcm(mensaje: dict[str, Any]) -> dict[str, Any]:
    """Mismo payload GCM del zip (`proceso.js`): `notification` para el banner, `data` para la
    ruta y el id de correlación; `tag`/`collapse_key` para que una reentrega reemplace, no
    duplique (idempotencia por `idempotencia`, ver el evento)."""
    datos: dict[str, Any] = {"ruta": mensaje.get("ruta") or "", "eventId": mensaje["evento_id"]}
    cuerpo: dict[str, Any] = {
        "data": datos,
        "android": {
            "ttl": f"{mensaje.get('ttl_segundos', 86400)}s",
            "priority": "high",
            "collapse_key": mensaje["idempotencia"],
        },
    }
    if not mensaje.get("silenciosa"):
        cuerpo["notification"] = {
            "title": mensaje["titulo"],
            "body": mensaje["cuerpo"],
            "tag": mensaje["idempotencia"],
        }
    return cuerpo


class EnviadorStub:
    """Local, CI y demo sin AWS real: registra en memoria lo "enviado", como `stub-notificaciones`.
    `fallos` simula tokens que SNS reportaría como `EndpointDisabled` (para probar ese camino)."""

    def __init__(self, fallos: frozenset[str] = frozenset()) -> None:
        self.enviados: list[dict[str, Any]] = []
        self._fallos = fallos

    def enviar(self, destino: dict[str, Any], mensaje: dict[str, Any]) -> None:
        if destino["token"] in self._fallos:
            raise TokenInvalido()
        self.enviados.append({"destino": destino, "mensaje": mensaje, "gcm": _payload_gcm(mensaje)})
        logger.info(
            "push (stub) idempotencia=%s plataforma=%s silenciosa=%s",
            mensaje["idempotencia"],
            destino["plataforma"],
            bool(mensaje.get("silenciosa")),
        )


def _pedir_token_identidad_gcp(audiencia: str) -> str:
    import httpx

    url = (
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/identity"
    )
    respuesta = httpx.get(
        url,
        params={"audience": audiencia, "format": "full"},
        headers={"Metadata-Flavor": "Google"},
        timeout=2.0,
    )
    respuesta.raise_for_status()
    return respuesta.text


class EnviadorSnsBoto3:
    """Modo `real` (`SNS_MODO=real`): `boto3` + SNS, con las credenciales de AWS por federación de
    identidad (D7). `pedir_token_gcp` y `reloj` son inyectables solo para pruebas; en producción
    usan GCP y el tiempo real."""

    def __init__(
        self,
        platform_application_arn: str,
        role_arn: str,
        region: str,
        pedir_token_gcp: Any = _pedir_token_identidad_gcp,
        cliente_sts: Any = None,
        cliente_sns_fabrica: Any = None,
        reloj: Any = time.monotonic,
    ) -> None:
        self._platform_application_arn = platform_application_arn
        self._role_arn = role_arn
        self._region = region
        self._pedir_token_gcp = pedir_token_gcp
        self._cliente_sts = cliente_sts
        self._cliente_sns_fabrica = cliente_sns_fabrica
        self._reloj = reloj
        self._credenciales: dict[str, Any] | None = None
        self._credenciales_expiran_en = 0.0

    def _sts(self) -> Any:
        if self._cliente_sts is not None:
            return self._cliente_sts
        import boto3

        return boto3.client("sts", region_name=self._region)

    def _credenciales_vigentes(self) -> dict[str, Any]:
        if self._credenciales is None or self._reloj() >= self._credenciales_expiran_en:
            token_gcp = self._pedir_token_gcp(AUDIENCIA_STS)
            respuesta = self._sts().assume_role_with_web_identity(
                RoleArn=self._role_arn,
                RoleSessionName="enviar-push",
                WebIdentityToken=token_gcp,
            )
            credenciales = respuesta["Credentials"]
            self._credenciales = {
                "aws_access_key_id": credenciales["AccessKeyId"],
                "aws_secret_access_key": credenciales["SecretAccessKey"],
                "aws_session_token": credenciales["SessionToken"],
            }
            # Más corto entre lo que dura la asunción del rol y nuestro propio margen: nunca se
            # cachea una credencial más de lo que AWS la deja vivir.
            self._credenciales_expiran_en = self._reloj() + VIGENCIA_CREDENCIALES_SEGUNDOS
            logger.info("credenciales de AWS renovadas por federación (rol=%s)", self._role_arn)
        return self._credenciales

    def _sns(self) -> Any:
        if self._cliente_sns_fabrica is not None:
            return self._cliente_sns_fabrica(self._credenciales_vigentes())
        import boto3

        return boto3.client("sns", region_name=self._region, **self._credenciales_vigentes())

    def _endpoint_arn(self, sns: Any, token: str) -> str:
        """`create_platform_endpoint` es idempotente por token: si ya existe con los mismos
        atributos, AWS devuelve el mismo ARN en vez de fallar. Si existe con otro token
        (reinstalación con el mismo endpoint lógico no aplica aquí: el token identifica el
        dispositivo), el mensaje de error de AWS trae el ARN existente; se reutiliza y se
        actualiza el token."""
        try:
            respuesta = sns.create_platform_endpoint(
                PlatformApplicationArn=self._platform_application_arn, Token=token
            )
            return str(respuesta["EndpointArn"])
        except Exception as error:  # botocore.exceptions.ClientError sin importar el paquete aquí
            mensaje = str(error)
            if "already exists with the same Token" in mensaje:
                raise
            import re

            coincidencia = re.search(r"Endpoint (arn:aws:sns:[^ ]+) already exists", mensaje)
            if coincidencia is None:
                raise
            arn = coincidencia.group(1)
            sns.set_endpoint_attributes(
                EndpointArn=arn, Attributes={"Token": token, "Enabled": "true"}
            )
            return arn

    def enviar(self, destino: dict[str, Any], mensaje: dict[str, Any]) -> None:
        sns = self._sns()
        try:
            arn = self._endpoint_arn(sns, destino["token"])
            cuerpo = _payload_gcm(mensaje)
            sns.publish(
                TargetArn=arn,
                MessageStructure="json",
                Message=json.dumps(
                    {"default": mensaje.get("cuerpo") or "push", "GCM": json.dumps(cuerpo)}
                ),
            )
        except Exception as error:
            mensaje_error = str(error)
            if "EndpointDisabled" in mensaje_error or "is disabled" in mensaje_error:
                raise TokenInvalido() from error
            raise ErrorEnvio(type(error).__name__) from error
        logger.info(
            "push (SNS) idempotencia=%s plataforma=%s [PUBLISH]",
            mensaje["idempotencia"],
            destino["plataforma"],
        )


def construir_enviador(config: Any) -> PuertoEnviadorPush:
    if config.sns_modo == "real":
        return EnviadorSnsBoto3(
            config.sns_platform_application_arn, config.aws_role_arn, config.sns_region
        )
    return EnviadorStub()
