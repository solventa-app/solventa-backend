"""Cookie del refresh de la sesión web (T22).

El refresh de la web viaja en una cookie `httpOnly; Secure; SameSite=Strict` que solo pone y lee
el BFF: nunca llega al JavaScript de la página (la respuesta GraphQL de la web no lo trae).
`renovarSesion` y `cerrarSesion` por la web exigen además una cabecera propia y, si hay `Origin`,
que sea un origen autorizado (protección CSRF). En la app el refresh viaja por el cuerpo."""

from typing import Any

NOMBRE_COOKIE_REFRESH = "solventa_refresh"
CABECERA_CSRF = "x-solventa-csrf"
VALOR_CSRF = "1"
CANAL_WEB = "web"


def es_web(contexto: dict[str, Any]) -> bool:
    return contexto.get("canal") == CANAL_WEB


def csrf_valido(contexto: dict[str, Any]) -> bool:
    """Cabecera propia presente y, si el navegador mandó `Origin`, uno autorizado."""
    if contexto.get("cabecera_csrf") != VALOR_CSRF:
        return False
    permitidos = contexto.get("origenes_web") or ()
    origen = contexto.get("origen")
    if permitidos and origen is not None:
        return origen.rstrip("/") in permitidos
    return True


def poner_cookie(contexto: dict[str, Any], refresh: str, max_age: int) -> None:
    contexto["response"].set_cookie(
        NOMBRE_COOKIE_REFRESH,
        refresh,
        max_age=max(max_age, 0),
        path="/",
        httponly=True,
        secure=bool(contexto.get("cookie_refresh_secure", True)),
        samesite="strict",
    )


def borrar_cookie(contexto: dict[str, Any]) -> None:
    contexto["response"].delete_cookie(
        NOMBRE_COOKIE_REFRESH,
        path="/",
        httponly=True,
        secure=bool(contexto.get("cookie_refresh_secure", True)),
        samesite="strict",
    )
