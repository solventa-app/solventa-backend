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
    # API de socios (HU-W07, contracts/openapi/socios-v1.json)
    "Credenciales inválidas.": (
        "Invalid credentials."
    ),
    "Falta el token de acceso o no es válido.": (
        "The access token is missing or invalid."
    ),
    "Esta operación requiere un token de producción.": (
        "This operation requires a production token."
    ),
    "No existe o no pertenece a este socio.": (
        "It does not exist or does not belong to this partner."
    ),
    "Revisa los datos de la solicitud.": (
        "Check the request data."
    ),
    "No hay un consentimiento vigente de este socio para el titular.": (
        "This partner has no valid consent for the holder."
    ),
    "Se agotó el presupuesto de cotizaciones de este entorno.": (
        "The quote budget for this environment is used up."
    ),
    "El servicio no está disponible en este momento. Intenta de nuevo.": (
        "The service is not available right now. Please try again."
    ),
    # Onboarding del móvil (HU-M01, contrato v0.20)
    "Estamos verificando tu identidad. Podrás usar esto al terminar.": (
        "We are verifying your identity. You can use this once it is done."
    ),
    "Tu identidad aún no está verificada. Ingresa desde la app Solventa.": (
        "Your identity is not verified yet. Sign in from the Solventa app."
    ),
    "No pudimos actualizar tu sesión. Intenta de nuevo.": (
        "We could not refresh your session. Please try again."
    ),
    "Tu sesión ya no es válida. Inicia sesión de nuevo.": (
        "Your session is no longer valid. Please sign in again."
    ),
    "Tu verificación venció o no es válida. Empieza de nuevo.": (
        "Your verification expired or is not valid. Please start again."
    ),
    "Primero acepta el tratamiento de tus datos biométricos.": (
        "First accept the processing of your biometric data."
    ),
    "Este paso ya no aplica a tu verificación.": (
        "This step no longer applies to your verification."
    ),
    "Faltan fotos o el video. Complétalos e intenta de nuevo.": (
        "Some photos or the video are missing. Complete them and try again."
    ),
    "Ya no quedan intentos de verificación.": ("There are no verification attempts left."),
    "Esta verificación ya tiene una cuenta.": ("This verification already has an account."),
    "Ya existe una cuenta con ese correo.": ("An account with that email already exists."),
    "Aún no verificamos documentos de ese país.": (
        "We do not verify documents from that country yet."
    ),
    "Ese tipo de documento no se admite.": ("That document type is not supported."),
    "Ese paso no existe.": ("That step does not exist."),
    "No encontramos ese archivo. Súbelo de nuevo.": (
        "We could not find that file. Upload it again."
    ),
    "No pudimos continuar tu verificación. Intenta de nuevo.": (
        "We could not continue your verification. Please try again."
    ),
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
    # Archivos (KAN-250)
    "Este tipo de archivo no se admite.": "This file type isn't supported.",
    "El formato del archivo no se admite.": "This file format isn't supported.",
    "El archivo supera el tamaño permitido.": "The file exceeds the allowed size.",
    "No tienes permiso para este archivo.": "You don't have permission for this file.",
    "Aún no recibimos el archivo. Súbelo e intenta de nuevo.": (
        "We haven't received the file yet. Upload it and try again."
    ),
    "Archivo no encontrado.": "File not found.",
    "El archivo aún no está disponible.": "The file isn't available yet.",
    "Indica a quién pertenece el archivo.": "Indicate who the file belongs to.",
    "Este archivo requiere tu autorización vigente.": (
        "This file requires your current authorization."
    ),
    "No pudimos procesar el archivo.": "We couldn't process the file.",
    "No pudimos procesar el archivo. Intenta de nuevo.": (
        "We couldn't process the file. Please try again."
    ),
    "Indica el documento de identidad del titular.": (
        "Enter the policyholder's identity document."
    ),
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
    # Estado de la propuesta (app/servicio_suscripcion.py, `explicar_estado`)
    "No pudimos cobrar tu primera cuota porque la cuenta no tiene fondos suficientes. "
    "Habla con un asesor para resolverlo.": (
        "We couldn't charge your first installment because the account has insufficient funds. "
        "Talk to an advisor to sort it out."
    ),
    "No pudimos cobrar tu primera cuota. Habla con un asesor para resolverlo.": (
        "We couldn't charge your first installment. Talk to an advisor to sort it out."
    ),
    "Tu pago está en proceso. Emitiremos tu póliza cuando se confirme y te avisaremos.": (
        "Your payment is being processed. We'll issue your policy once it is confirmed "
        "and let you know."
    ),
    "Estamos revisando tu solicitud. Te avisamos cuando esté lista.": (
        "We're reviewing your request. We'll let you know when it's ready."
    ),
    "No pudimos aprobar tu solicitud. Habla con un asesor para conocer tus opciones.": (
        "We couldn't approve your request. Talk to an advisor to learn about your options."
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
    # Firma y pago (app/servicio_firma_pago.py; HU-W03, bloque 9)
    "Propuesta no encontrada.": "Proposal not found.",
    "La propuesta no está en un estado que permita esta operación.": (
        "The proposal isn't in a state that allows this operation."
    ),
    "El código no es correcto. Revísalo e intenta de nuevo.": (
        "The code isn't correct. Check it and try again."
    ),
    "El código venció. Pide uno nuevo.": "The code has expired. Request a new one.",
    "Agotaste los intentos. Pide un código nuevo.": (
        "You've used all your attempts. Request a new code."
    ),
    "Revisa los datos e intenta de nuevo.": "Check the details and try again.",
    "El enlace venció. Pide a tu asesor que te envíe uno nuevo.": (
        "The link has expired. Ask your advisor to send you a new one."
    ),
    "La imagen de la firma no es válida. Sube otra.": (
        "The signature image isn't valid. Upload another one."
    ),
    "La pasarela rechazó el pago. Prueba con otra tarjeta.": (
        "The payment gateway declined the payment. Try another card."
    ),
    "La tarjeta no tiene fondos suficientes. Prueba con otra.": (
        "The card has insufficient funds. Try another one."
    ),
    "La tarjeta está vencida. Prueba con otra.": "The card has expired. Try another one.",
    "La pasarela no responde. Intenta de nuevo en un momento.": (
        "The payment gateway isn't responding. Please try again in a moment."
    ),
    "No pudimos cobrar tras varios intentos. Usa otra tarjeta.": (
        "We couldn't charge the card after several attempts. Use another card."
    ),
    "La sesión de pago venció. Empieza de nuevo.": "The payment session has expired. Start again.",
    "Aún no recibimos tu pago. Intenta de nuevo en unos segundos.": (
        "We haven't received your payment yet. Please try again in a few seconds."
    ),
    "Tu póliza ya fue emitida.": "Your policy has already been issued.",
    "El documento no coincide con el de tu cuenta.": (
        "The document doesn't match the one on your account."
    ),
    "No pudimos consultar las propuestas. Intenta de nuevo.": (
        "We couldn't load the proposals. Please try again."
    ),
    "No pudimos abrir los avisos. Intenta de nuevo.": (
        "We couldn't open the notifications. Please try again."
    ),
    "Revisa el nombre y el correo del titular.": (
        "Check the policyholder's name and email address."
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
    # Contacto del cliente (AUTH, app/usuarios.py; HU-W03, bloque 8)
    "El correo no es válido.": "The email address is not valid.",
    "El teléfono no es válido: usa entre 7 y 15 dígitos, con un + opcional al inicio.": (
        "The phone number is not valid: use 7 to 15 digits, with an optional + at the start."
    ),
    "Indica un correo o un teléfono.": "Provide an email address or a phone number.",
    "El contacto solo se guarda para clientes.": "Contact details are only stored for clients.",
    # Detalles que PAYMENTS y AUTH devuelven tal cual (app/clientes.py)
    "Solicitud inválida.": "Invalid request.",
    "No se puede reintentar.": "This payment can't be retried.",
    "Cobro en conflicto.": "Conflicting payment.",
    "Los datos del cobro no son válidos.": "The payment details are not valid.",
    "Ese id_intento_cobro ya se usó con otros datos: genera uno nuevo para una orden distinta.": (
        "That id_intento_cobro was already used with other data: "
        "generate a new one for a different order."
    ),
    # Mesa de siniestros (HU-W04): mensajes del BFF y de CLAIMS, que llegan tal cual
    "No pudimos consultar los siniestros. Intenta de nuevo.": (
        "We couldn't load the claims. Please try again."
    ),
    "La ventana debe ser 15m, 1h, 24h o 7d.": (
        "The window must be 15m, 1h, 24h or 7d."
    ),
    "No pudimos calcular el estado del sistema. Intenta de nuevo.": (
        "We couldn't compute the system status. Please try again."
    ),
    "No pudimos consultar la póliza del siniestro. Intenta de nuevo.": (
        "We couldn't load the claim's policy. Please try again."
    ),
    "No pudimos completar la acción sobre el siniestro. Intenta de nuevo.": (
        "We couldn't complete the action on the claim. Please try again."
    ),
    "Revisa los datos enviados e intenta de nuevo.": "Check the data you sent and try again.",
    "No encontramos el siniestro.": "We couldn't find the claim.",
    "El siniestro ya fue resuelto y no admite cambios.": (
        "The claim has already been resolved and can't be changed."
    ),
    "El siniestro ya tiene un perito asignado.": "The claim already has an assigned adjuster.",
    "El perito seleccionado no está disponible.": "The selected adjuster isn't available.",
    "El texto supera los 500 caracteres.": "The text exceeds 500 characters.",
    "El motivo de rechazo no es válido.": "The rejection reason isn't valid.",
    "No pudimos registrar la orden de pago de la indemnización. Intenta de nuevo.": (
        "We couldn't register the indemnity payment order. Please try again."
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
