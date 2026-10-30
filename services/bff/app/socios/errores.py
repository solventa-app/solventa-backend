"""Errores de la API de socios: siempre `ErrorPublico` con un `codigo` estable (contrato
`socios-v1.json`) y el mensaje en el idioma de `Accept-Language`. Nunca un 5xx crudo (regla 4): una
falla interna responde 503 con `puedeReintentar` y `Retry-After`."""

from dataclasses import dataclass

from fastapi.responses import JSONResponse

from app.idioma import Idioma, traducir

MENSAJE_CREDENCIALES_INVALIDAS = "Credenciales inválidas."
MENSAJE_TOKEN_INVALIDO = "Falta el token de acceso o no es válido."
MENSAJE_SOLO_PRODUCCION = "Esta operación requiere un token de producción."
MENSAJE_NO_ENCONTRADO = "No existe o no pertenece a este socio."
MENSAJE_DATOS_INVALIDOS = "Revisa los datos de la solicitud."
MENSAJE_SIN_CONSENTIMIENTO = "No hay un consentimiento vigente de este socio para el titular."
MENSAJE_PRESUPUESTO_AGOTADO = "Se agotó el presupuesto de cotizaciones de este entorno."
MENSAJE_NO_DISPONIBLE = "El servicio no está disponible en este momento. Intenta de nuevo."

SEGUNDOS_REINTENTO = 5


@dataclass
class ErrorApiSocio(Exception):
    estado: int
    codigo: str
    mensaje: str
    puede_reintentar: bool = False
    intentos_restantes: int | None = None
    reintentar_en: int | None = None


def no_disponible() -> ErrorApiSocio:
    return ErrorApiSocio(503, "SERVICIO_NO_DISPONIBLE", MENSAJE_NO_DISPONIBLE, True, None, 5)


def no_encontrado() -> ErrorApiSocio:
    return ErrorApiSocio(404, "NO_ENCONTRADO", MENSAJE_NO_ENCONTRADO)


def datos_invalidos(mensaje: str = MENSAJE_DATOS_INVALIDOS) -> ErrorApiSocio:
    return ErrorApiSocio(422, "DATOS_INVALIDOS", mensaje)


def respuesta_de_error(error: ErrorApiSocio, idioma: Idioma) -> JSONResponse:
    cuerpo = {
        "codigo": error.codigo,
        "mensaje": traducir(error.mensaje, idioma),
        "puedeReintentar": error.puede_reintentar,
    }
    if error.intentos_restantes is not None:
        cuerpo["intentosRestantes"] = error.intentos_restantes
    cabeceras = {"Retry-After": str(error.reintentar_en)} if error.reintentar_en else None
    if error.estado == 401:
        cabeceras = {**(cabeceras or {}), "WWW-Authenticate": "Bearer"}
    return JSONResponse(cuerpo, status_code=error.estado, headers=cabeceras)
