"""Autenticación y autorización de usuarios en el BFF (HU-M02, CA-W01-11).

AUTH emite el JWT (HS256) al iniciar sesión; aquí se verifica con el mismo secreto
(`JWT_SECRETO`, secreto `clave-jwt`) en cada petición.

Con el API Gateway delante del BFF (HA-LAT-004, decisión 17), ESPv2 usa `Authorization`
para su propio token de identidad (`gateway-bff`) y mueve la cabecera original del usuario
a `X-Forwarded-Authorization`. Por eso `extraer_cabecera_autorizacion` lee primero
`X-Forwarded-Authorization` cuando existe y, si no, `Authorization` (local y CI con Envoy).
Pasar la validación criptográfica del JWT al gateway (ES256 + JWKS) queda en el Sprint 2.

Modo de la bandera `AUTENTICACION_USUARIOS`:
- `opcional` (valor actual): las operaciones existentes aceptan peticiones sin token,
  como antes, para no romper al frontend. Si llega un token, se valida y se aplica el rol.
- `obligatoria`: toda operación protegida exige token.
La gestión de usuarios y `yo` exigen token siempre.

Nunca se loguea el token."""

from collections.abc import Mapping
from dataclasses import dataclass

import jwt

ALGORITMO_JWT = "HS256"
MODOS = ("opcional", "obligatoria")

MENSAJE_SIN_SESION = "Debes iniciar sesión."
MENSAJE_TOKEN_INVALIDO = "Tu sesión no es válida o venció. Inicia sesión de nuevo."
MENSAJE_SIN_PERMISO = "No tienes permiso para esta operación."
MENSAJE_VERIFICACION_PENDIENTE = "Estamos verificando tu identidad. Podrás usar esto al terminar."
SCOPE_VERIFICACION_PENDIENTE = "verificacion-pendiente"


@dataclass(frozen=True)
class UsuarioActual:
    id: str
    rol: str
    scopes: tuple[str, ...]
    # `exp` del token (segundos Unix): `actualizarSesion` conserva la misma expiración (HU-M01).
    expira: int | None = None

    @property
    def verificacion_pendiente(self) -> bool:
        """Cuenta del onboarding sin identidad verificada (HU-M01): solo entra a su verificación."""
        return SCOPE_VERIFICACION_PENDIENTE in self.scopes


class TokenInvalido(Exception):
    """La cabecera trae un token, pero no es válido: firma, formato o vencimiento."""


def extraer_cabecera_autorizacion(cabeceras: Mapping[str, str]) -> str | None:
    """Lee el JWT del usuario de `X-Forwarded-Authorization` (cuando la petición
    pasó por GCP API Gateway / ESPv2) y, si no viene o está vacía, de `Authorization`."""
    reenviada = cabeceras.get("x-forwarded-authorization")
    if reenviada and reenviada.strip():
        return reenviada
    return cabeceras.get("authorization")


def leer_usuario(autorizacion: str | None, secreto: str) -> UsuarioActual | None:
    """`None` si no hay cabecera `Authorization`; `TokenInvalido` si la hay y no sirve."""
    if not autorizacion:
        return None
    esquema, _, token = autorizacion.partition(" ")
    if esquema.lower() != "bearer" or not token:
        raise TokenInvalido(MENSAJE_TOKEN_INVALIDO)
    try:
        claims = jwt.decode(
            token,
            secreto,
            algorithms=[ALGORITMO_JWT],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError as error:
        # Incluye firma alterada, token vencido y claims faltantes.
        raise TokenInvalido(MENSAJE_TOKEN_INVALIDO) from error
    if claims.get("rol") not in ("CLIENTE", "ASESOR", "BACKOFFICE"):
        raise TokenInvalido(MENSAJE_TOKEN_INVALIDO)
    return UsuarioActual(
        id=claims["sub"],
        rol=claims["rol"],
        scopes=tuple(claims.get("scopes", [])),
        expira=int(claims["exp"]),
    )
