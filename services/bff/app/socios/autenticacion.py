"""Token del socio (HU-W07, W07-4): JWT **ES256** de AUTH, validado con su JWKS.

El API Gateway ya lo valida en el borde; el BFF lo vuelve a validar (defensa en profundidad) porque
en local Envoy puede no hacerlo y porque es lo que decide quién es el socio. Lo lee de
`X-Forwarded-Authorization` (detrás de GCP API Gateway) o de `Authorization`, como el de usuarios.

El JWKS se guarda 10 minutos. Si llega un `kid` desconocido (AUTH rotó la clave) se vuelve a pedir
una vez antes de rechazar. Nunca se loguea el token."""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from app.autenticacion_usuarios import UsuarioActual, extraer_cabecera_autorizacion
from app.clientes import ErrorServicio, PuertoSocios
from app.socios.errores import (
    MENSAJE_SIN_PERMISO,
    MENSAJE_TOKEN_INVALIDO,
    ErrorApiSocio,
    no_disponible,
)

logger = logging.getLogger("bff.socios.autenticacion")

EMISOR = "solventa-auth"
AUDIENCIA = "solventa-socios"
TTL_JWKS_SEGUNDOS = 600
ENTORNOS = ("PRODUCCION", "PRUEBAS")


@dataclass(frozen=True)
class SocioActual:
    id: str
    entorno: str
    # Los del claim `scopes` del token (HA-SEG-004): cada ruta `/v1` exige el suyo con `exigir`.
    scopes: frozenset[str] = frozenset()

    @property
    def es_prueba(self) -> bool:
        return self.entorno == "PRUEBAS"

    def como_usuario(self) -> UsuarioActual:
        """El socio como actor de los servicios de W03 (W07-20): un intermediario con rol SOCIO."""
        # `UsuarioActual.scopes` solo decide `verificacion-pendiente`, que el socio nunca trae.
        return UsuarioActual(id=self.id, rol="SOCIO", scopes=tuple(sorted(self.scopes)))

    def exigir(self, scope: str) -> None:
        """403 `PROHIBIDO` si el token no trae el scope de la ruta (EC025)."""
        if scope not in self.scopes:
            raise ErrorApiSocio(403, "PROHIBIDO", MENSAJE_SIN_PERMISO)


def _token_invalido() -> ErrorApiSocio:
    return ErrorApiSocio(401, "TOKEN_INVALIDO", MENSAJE_TOKEN_INVALIDO)


class VerificadorTokensSocio:
    def __init__(
        self,
        socios: PuertoSocios,
        ttl_segundos: int = TTL_JWKS_SEGUNDOS,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socios = socios
        self._ttl = ttl_segundos
        self._reloj = reloj
        self._jwks: dict | None = None
        self._leido_en = 0.0

    async def jwks(self, forzar: bool = False) -> dict:
        vigente = self._jwks is not None and self._reloj() - self._leido_en < self._ttl
        if forzar or not vigente:
            try:
                self._jwks = await self._socios.jwks()
                self._leido_en = self._reloj()
            except ErrorServicio as error:
                if self._jwks is None:
                    logger.warning("AUTH no entregó el JWKS de socios error=%s", error)
                    raise no_disponible() from error
                logger.warning("AUTH no entregó el JWKS; se usa el guardado error=%s", error)
        assert self._jwks is not None  # nosec B101
        return self._jwks

    async def _clave(self, kid: str | None):
        for intento in (False, True):
            for jwk in (await self.jwks(forzar=intento)).get("keys", []):
                if kid is None or jwk.get("kid") == kid:
                    return jwt.PyJWK(jwk).key
        raise _token_invalido()

    async def verificar(self, cabeceras: Mapping[str, str]) -> SocioActual:
        autorizacion = extraer_cabecera_autorizacion(cabeceras)
        esquema, _, token = (autorizacion or "").partition(" ")
        if esquema.lower() != "bearer" or not token:
            raise _token_invalido()
        try:
            cabecera = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise _token_invalido() from error
        if cabecera.get("alg") != "ES256":
            raise _token_invalido()
        clave = await self._clave(cabecera.get("kid"))
        try:
            claims = jwt.decode(
                token,
                clave,
                algorithms=["ES256"],
                audience=AUDIENCIA,
                issuer=EMISOR,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise _token_invalido() from error
        if claims.get("rol") != "SOCIO" or claims.get("entorno") not in ENTORNOS:
            raise _token_invalido()
        # Sin el claim o mal formado no es un token de AUTH: 401. Bien formado pero sin el scope
        # de la ruta: 403 (`SocioActual.exigir`).
        scopes = claims.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise _token_invalido()
        return SocioActual(
            id=str(claims["sub"]), entorno=claims["entorno"], scopes=frozenset(scopes)
        )
