"""Idioma de los mensajes que el BFF le devuelve al usuario (español o inglés).

El frontend manda el idioma que el usuario eligió en el selector del header como
`Accept-Language`. Los servicios siguen escribiendo sus mensajes en español; la
traducción se hace aquí, en la frontera, sobre la respuesta ya armada (ver
`docs/evidencias/diseno-i18n-moneda-prima.md`, sección 5). Sin cabecera, o con un
idioma que no sea inglés, todo sale en español como siempre.

Se traduce por coincidencia exacta del texto: un mensaje nuevo que no esté en
`EN` sale en español (nunca vacío ni roto). `tests/test_idioma.py` revisa que los
mensajes conocidos del BFF tengan su traducción."""

import re
from collections.abc import Iterator
from typing import Any, Literal

from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

Idioma = Literal["es", "en"]

EN: dict[str, str] = {
    # Sesión y permisos (app/autenticacion_usuarios.py)
    "Debes iniciar sesión.": "You must sign in.",
    "Tu sesión no es válida o venció. Inicia sesión de nuevo.": (
        "Your session is invalid or has expired. Please sign in again."
    ),
    "No tienes permiso para esta operación.": "You don't have permission for this operation.",
    # Operaciones (app/esquema.py)
    "No pudimos procesar la solicitud. Intenta de nuevo.": (
        "We couldn't process the request. Please try again."
    ),
    "Usuario no encontrado.": "User not found.",
    "No pudimos consultar los cobros. Intenta de nuevo.": (
        "We couldn't load the payments. Please try again."
    ),
    "La oferta ya no está disponible. Vuelve a cotizar.": (
        "The offer is no longer available. Please get a new quote."
    ),
    "Esta oferta es preliminar y no se puede aceptar. Vuelve a calcularla.": (
        "This offer is preliminary and cannot be accepted. Recalculate it."
    ),
    "La oferta venció. Vuelve a cotizar.": "The offer has expired. Please get a new quote.",
    "No hay un consentimiento vigente para esta oferta.": (
        "There is no valid consent for this offer."
    ),
    "No pudimos generar el documento. Intenta de nuevo.": (
        "We couldn't generate the document. Please try again."
    ),
    "Debes autorizar el cobro de la prima para firmar.": (
        "You must authorize the premium charge to sign."
    ),
    "El documento cambió. Revísalo de nuevo antes de firmar.": (
        "The document has changed. Please review it again before signing."
    ),
    "Esta propuesta no se puede firmar.": "This proposal cannot be signed.",
    "No pudimos registrar tu firma. Intenta de nuevo.": (
        "We couldn't record your signature. Please try again."
    ),
    "No pudimos procesar tu suscripción. Intenta de nuevo.": (
        "We couldn't process your subscription. Please try again."
    ),
    "No pudimos registrar el consentimiento. Intenta de nuevo.": (
        "We couldn't record the consent. Please try again."
    ),
    "No pudimos revocar el consentimiento. Intenta de nuevo.": (
        "We couldn't revoke the consent. Please try again."
    ),
    "Consentimiento no encontrado.": "Consent not found.",
    "Cobro no encontrado.": "Payment not found.",
    "No pudimos registrar el cobro. Intenta de nuevo.": (
        "We couldn't register the payment. Please try again."
    ),
    "No pudimos iniciar sesión. Intenta de nuevo.": "We couldn't sign you in. Please try again.",
    "Usuario o clave incorrectos.": "Incorrect username or password.",
    "Recibimos tu solicitud de llamada. Un asesor te contactará para continuar tu cotización.": (
        "We received your call request. An advisor will contact you to continue your quote."
    ),
    "Recibimos tu solicitud de chat. Un asesor te contactará para continuar tu cotización.": (
        "We received your chat request. An advisor will contact you to continue your quote."
    ),
    "Recibimos tu solicitud de llamada agendada. "
    "Un asesor te contactará para continuar tu cotización.": (
        "We received your scheduled call request. "
        "An advisor will contact you to continue your quote."
    ),
    # Oferta (app/servicio_oferta.py)
    "No hay un consentimiento vigente para consultar tus datos.": (
        "There is no valid consent to look up your data."
    ),
    "No pudimos calcular tu oferta en este momento. Intenta de nuevo o habla con un asesor.": (
        "We couldn't calculate your offer right now. Please try again or talk to an advisor."
    ),
    "El valor del crédito no es válido.": "The loan amount is not valid.",
    "El documento de identidad no es válido.": "The ID number is not valid.",
    "El plazo del crédito debe estar entre 1 y 60 meses.": (
        "The loan term must be between 1 and 60 months."
    ),
    "Ingresa la dirección del inmueble.": "Enter the property address.",
    "No pudimos consultar tu historial crediticio en este momento. "
    "Intenta de nuevo o habla con un asesor.": (
        "We couldn't check your credit history right now. Please try again or talk to an advisor."
    ),
    # Factores del desglose del score (RATING, motor_scoring.py)
    "Endeudamiento y DTI": "Debt and DTI",
    "Estabilidad de Ingresos": "Income Stability",
    "Comportamiento Bancario": "Banking Behavior",
    "Historial en Centrales de Riesgo": "Credit Bureau History",
    # Conversión de montos (app/monedas.py)
    "Moneda no soportada.": "Unsupported currency.",
    "El monto no es válido.": "The amount is not valid.",
    "Se pueden convertir hasta 50 montos a la vez.": "Up to 50 amounts can be converted at once.",
    "No pudimos convertir los montos en este momento.": (
        "We couldn't convert the amounts right now."
    ),
    # Preferencias (AUTH, app/usuarios.py)
    "Idioma no válido. Usa uno de: ES, EN.": "Invalid language. Use one of: ES, EN.",
    "Moneda no válida. Usa una de: COP, MXN, BRL, CLP, PEN, USD, EUR.": (
        "Invalid currency. Use one of: COP, MXN, BRL, CLP, PEN, USD, EUR."
    ),
    # Detalles que PAYMENTS y AUTH devuelven tal cual (app/clientes.py)
    "Solicitud inválida.": "Invalid request.",
    "No se puede reintentar.": "This payment can't be retried.",
    "Cobro en conflicto.": "Conflicting payment.",
    "Los datos del cobro no son válidos.": "The payment details are not valid.",
    "Ese id_intento_cobro ya se usó con otros datos: genera uno nuevo para una orden distinta.": (
        "That id_intento_cobro was already used with other data: "
        "generate a new one for a different order."
    ),
}

# Mensajes con un número dentro (PAYMENTS, `mensaje_reintentos_agotados`).
EN_PATRONES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"Se agotaron los (\d+) reintentos: el cobro requiere otro medio de pago\."),
        r"All \1 retries were used: the payment needs another payment method.",
    ),
]


def idioma_de(accept_language: str | None) -> Idioma:
    """`en` si el primer idioma preferido (por `q`) es inglés; si no, `es`."""
    if not accept_language:
        return "es"
    preferidos: list[tuple[float, int, str]] = []
    for orden, parte in enumerate(accept_language.split(",")):
        etiqueta, _, parametros = parte.strip().partition(";")
        calidad = 1.0
        if parametros.strip().startswith("q="):
            try:
                calidad = float(parametros.strip()[2:])
            except ValueError:
                calidad = 0.0
        if etiqueta:
            # A igual calidad gana el que viene primero.
            preferidos.append((-calidad, orden, etiqueta.lower()))
    if not preferidos:
        return "es"
    primero = min(preferidos)[2]
    return "en" if primero == "en" or primero.startswith("en-") else "es"


def traducir(texto: str, idioma: Idioma) -> str:
    if idioma != "en":
        return texto
    if texto in EN:
        return EN[texto]
    for patron, reemplazo in EN_PATRONES:
        if patron.fullmatch(texto):
            return patron.sub(reemplazo, texto)
    return texto


def _traducir_datos(valor: Any, idioma: Idioma) -> Any:
    if isinstance(valor, str):
        return traducir(valor, idioma)
    if isinstance(valor, dict):
        return {clave: _traducir_datos(v, idioma) for clave, v in valor.items()}
    if isinstance(valor, list):
        return [_traducir_datos(v, idioma) for v in valor]
    return valor


class TraducirMensajes(SchemaExtension):
    """Traduce la respuesta al idioma de `contexto["idioma"]`: los textos de `data` que
    estén en el catálogo y los errores públicos. "Unexpected error." no se toca: el
    frontend lo reconoce por ese texto."""

    def on_operation(self) -> Iterator[None]:
        yield
        contexto = self.execution_context.context
        idioma = contexto.get("idioma", "es") if isinstance(contexto, dict) else "es"
        resultado = self.execution_context.result
        if idioma == "es" or resultado is None:
            return
        if getattr(resultado, "data", None):
            resultado.data = _traducir_datos(resultado.data, idioma)
        for error in getattr(resultado, "errors", None) or []:
            if isinstance(error, GraphQLError):
                error.message = traducir(error.message, idioma)
