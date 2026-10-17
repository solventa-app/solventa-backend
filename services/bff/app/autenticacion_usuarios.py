"""Autenticación y autorización de usuarios en el BFF (HU-M02, CA-W01-11).

AUTH emite el JWT (HS256) al iniciar sesión; aquí se verifica con el mismo secreto
(`JWT_SECRETO`, secreto `clave-jwt`) en cada petición.

Este módulo es **el único punto** que se movería al API Gateway si llega (Sprint 2):
entonces se pasaría a ES256 + JWKS y el gateway validaría el token en el borde (ver
`docs/autenticacion-usuarios.md`). Los resolutores solo llaman a `exigir_rol`.

Modo de la bandera `AUTENTICACION_USUARIOS`:
- `opcional` (valor actual): las operaciones existentes aceptan peticiones sin token,
  como antes, para no romper al frontend. Si llega un token, se valida y se aplica el rol.
- `obligatoria`: toda operación protegida exige token.
La gestión de usuarios y `yo` exigen token siempre.

Nunca se loguea el token."""

from dataclasses import dataclass

import jwt

ALGORITMO_JWT = "HS256"
MODOS = ("opcional", "obligatoria")

MENSAJE_SIN_SESION = "Debes iniciar sesión."
MENSAJE_TOKEN_INVALIDO = "Tu sesión no es válida o venció. Inicia sesión de nuevo."
MENSAJE_SIN_PERMISO = "No tienes permiso para esta operación."


@dataclass(frozen=True)
class UsuarioActual:
    id: str
    rol: str
    scopes: tuple[str, ...]


class TokenInvalido(Exception):
    """La cabecera trae un token, pero no es válido: firma, formato o vencimiento."""


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
        id=claims["sub"], rol=claims["rol"], scopes=tuple(claims.get("scopes", []))
    )
