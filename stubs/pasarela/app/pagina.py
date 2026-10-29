# ruff: noqa: E501  (plantilla HTML: las líneas largas se leen mejor enteras)
"""La página de pago alojada del stub (`GET /checkout/{id}`, W03-28), en español o inglés.

HTML armado aquí con todo escapado; el CSS y el JS son archivos propios en `app/estaticos/`, sin
CDN, y la respuesta lleva una CSP estricta (`CSP`). El JS envía la tarjeta a
`POST /checkout/{id}/pagar` del mismo origen, muestra «Pagando…» y luego el resultado con
«Regresar al comercio». El estado del pago **no viaja en la URL de regreso**: Solventa lo pregunta
de servidor a servidor."""

import json
from html import escape

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)

TEXTOS: dict[str, dict[str, str]] = {
    "es": {
        "titulo": "Pago seguro",
        "aviso_stub": "Pasarela simulada: no es parte de Solventa. Usa solo las tarjetas de prueba.",
        "comercio": "Comercio",
        "concepto": "Concepto",
        "monto": "Monto",
        "vence": "La sesión vence en",
        "tipo": "Tipo de tarjeta",
        "credito": "Crédito",
        "debito": "Débito",
        "numero": "Número de la tarjeta",
        "nombre": "Nombre como aparece en la tarjeta",
        "vencimiento": "Vencimiento (MM/AA)",
        "cvv": "CVV",
        "pagar": "Pagar",
        "pagando": "Pagando…",
        "aprobado": "Pago aprobado",
        "aprobado_texto": "Tu pago fue aprobado. Regresa al comercio para terminar.",
        "rechazado": "Pago rechazado",
        "fallido": "No pudimos procesar el pago",
        "fallido_texto": "Hubo un error al procesar el pago. Regresa al comercio para reintentar.",
        "vencida": "La sesión venció",
        "vencida_texto": "El tiempo para pagar terminó. Regresa al comercio para abrir otra.",
        "ya_procesada": "Esta sesión ya se procesó.",
        "regresar": "Regresar al comercio",
        "pruebas": "Tarjetas de prueba",
        "motivo.fondos_insuficientes": "Fondos insuficientes.",
        "motivo.tarjeta_vencida": "La tarjeta está vencida.",
        "motivo.error_procesamiento": "Error de procesamiento.",
        "campo.numero": "Revisa el número de la tarjeta.",
        "campo.nombre": "Escribe el nombre que aparece en la tarjeta.",
        "campo.vencimiento": "Revisa la fecha de vencimiento.",
        "campo.cvv": "El CVV tiene 3 o 4 dígitos.",
        "campo.tipo": "Elige crédito o débito.",
        "error_red": "No pudimos comunicarnos con la pasarela. Intenta de nuevo.",
        "resultado.aprobado": "Aprobado",
        "resultado.rechazado": "Rechazado",
        "resultado.fallido": "Error",
    },
    "en": {
        "titulo": "Secure payment",
        "aviso_stub": "Simulated gateway: not part of Solventa. Use only the test cards.",
        "comercio": "Merchant",
        "concepto": "Description",
        "monto": "Amount",
        "vence": "Session expires in",
        "tipo": "Card type",
        "credito": "Credit",
        "debito": "Debit",
        "numero": "Card number",
        "nombre": "Name on card",
        "vencimiento": "Expiry (MM/YY)",
        "cvv": "CVV",
        "pagar": "Pay",
        "pagando": "Paying…",
        "aprobado": "Payment approved",
        "aprobado_texto": "Your payment was approved. Return to the merchant to finish.",
        "rechazado": "Payment declined",
        "fallido": "We couldn't process the payment",
        "fallido_texto": "There was an error processing the payment. Return to the merchant to retry.",
        "vencida": "The session expired",
        "vencida_texto": "The time to pay is over. Return to the merchant to start a new one.",
        "ya_procesada": "This session was already processed.",
        "regresar": "Return to merchant",
        "pruebas": "Test cards",
        "motivo.fondos_insuficientes": "Insufficient funds.",
        "motivo.tarjeta_vencida": "The card has expired.",
        "motivo.error_procesamiento": "Processing error.",
        "campo.numero": "Check the card number.",
        "campo.nombre": "Enter the name on the card.",
        "campo.vencimiento": "Check the expiry date.",
        "campo.cvv": "The CVV has 3 or 4 digits.",
        "campo.tipo": "Choose credit or debit.",
        "error_red": "We couldn't reach the gateway. Please try again.",
        "resultado.aprobado": "Approved",
        "resultado.rechazado": "Declined",
        "resultado.fallido": "Error",
    },
}

TARJETAS_PANTALLA = (
    ("4242 4242 4242 4242", "Visa", "aprobado", None),
    ("5555 5555 5555 4444", "Mastercard", "aprobado", None),
    ("4000 0000 0000 0002", "Visa", "rechazado", "fondos_insuficientes"),
    ("4000 0000 0000 0069", "Visa", "rechazado", "tarjeta_vencida"),
    ("4000 0000 0000 0119", "Visa", "fallido", "error_procesamiento"),
)


def idioma_valido(idioma: str | None) -> str:
    return "en" if (idioma or "").lower().startswith("en") else "es"


def _monto(valor: str, moneda: str, idioma: str) -> str:
    entero, _, decimales = valor.partition(".")
    miles, decimal = (",", ".") if idioma == "en" else (".", ",")
    texto = f"{int(entero):,}".replace(",", miles)
    if decimales and int(decimales):
        texto += decimal + decimales
    return f"{moneda} {texto}"


def _tabla_pruebas(t: dict[str, str]) -> str:
    filas = "".join(
        f"<tr><td><code>{numero}</code></td><td>{marca}</td>"
        f"<td>{escape(t['resultado.' + resultado])}"
        f"{' · ' + escape(t['motivo.' + motivo]) if motivo else ''}</td></tr>"
        for numero, marca, resultado, motivo in TARJETAS_PANTALLA
    )
    return (
        f'<details class="pruebas"><summary>{escape(t["pruebas"])}</summary>'
        f"<table><tbody>{filas}</tbody></table></details>"
    )


def render(sesion: dict, idioma: str, url_regreso: str, vencida: bool) -> str:
    t = TEXTOS[idioma]
    datos_js = escape(
        json.dumps({"textos": t, "venceEn": sesion["vence_en"].isoformat()}), quote=True
    )
    regresar = (
        f'<a class="boton secundario" id="regresar" href="{escape(url_regreso, quote=True)}">'
        f"{escape(t['regresar'])}</a>"
    )
    resumen = (
        '<dl class="resumen">'
        f"<dt>{escape(t['comercio'])}</dt><dd>Solventa</dd>"
        f"<dt>{escape(t['concepto'])}</dt><dd>{escape(sesion['descripcion'])}</dd>"
        f"<dt>{escape(t['monto'])}</dt>"
        f'<dd class="monto">{escape(_monto(sesion["monto"], sesion["moneda"], idioma))}</dd>'
        "</dl>"
    )
    if vencida or sesion["estado"] != "ABIERTA":
        titulo, texto = (
            (t["vencida"], t["vencida_texto"]) if vencida else (t["titulo"], t["ya_procesada"])
        )
        cuerpo = f'<section class="resultado"><h2>{escape(titulo)}</h2><p>{escape(texto)}</p>{regresar}</section>'
    else:
        cuerpo = f"""
<p class="cuenta">{escape(t["vence"])} <span id="cuenta" aria-live="polite"></span></p>
<form id="formulario" novalidate autocomplete="off">
  <fieldset class="tipo"><legend>{escape(t["tipo"])}</legend>
    <label><input type="radio" name="tipo" value="CREDITO" checked> {escape(t["credito"])}</label>
    <label><input type="radio" name="tipo" value="DEBITO"> {escape(t["debito"])}</label>
  </fieldset>
  <label>{escape(t["numero"])}<input name="numero" inputmode="numeric" autocomplete="cc-number" maxlength="23" required></label>
  <label>{escape(t["nombre"])}<input name="nombre" autocomplete="cc-name" maxlength="100" required></label>
  <div class="fila">
    <label>{escape(t["vencimiento"])}<input name="vencimiento" inputmode="numeric" autocomplete="cc-exp" maxlength="7" placeholder="MM/AA" required></label>
    <label>{escape(t["cvv"])}<input name="cvv" inputmode="numeric" autocomplete="cc-csc" maxlength="4" required></label>
  </div>
  <p class="error" id="error" role="alert" hidden></p>
  <button class="boton" type="submit">{escape(t["pagar"])}</button>
</form>
<section class="pagando" id="pagando" hidden aria-live="polite">
  <div class="giro" aria-hidden="true"></div><p>{escape(t["pagando"])}</p>
</section>
<section class="resultado" id="resultado" hidden>
  <h2 id="resultado-titulo"></h2><p id="resultado-texto"></p>{regresar}
</section>
{_tabla_pruebas(t)}"""
    return f"""<!doctype html>
<html lang="{idioma}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{escape(t["titulo"])} · Pasarela simulada</title>
<link rel="stylesheet" href="/checkout-estaticos/checkout.css">
</head>
<body data-pagina="{datos_js}">
<main class="tarjeta">
  <p class="aviso">{escape(t["aviso_stub"])}</p>
  <h1>{escape(t["titulo"])}</h1>
  {resumen}
  {cuerpo}
</main>
<script src="/checkout-estaticos/checkout.js"></script>
</body>
</html>"""
