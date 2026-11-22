"""El PDF de la póliza (HU-W03, KAN-215, CA-W03-08).

Un documento formal con el membrete, el asegurado, el crédito, la cobertura, la prima, la vigencia
y el sello de la firma electrónica (la referencia de la evidencia, cuándo se firmó y la huella del
documento que se firmó). Se genera a partir de los datos guardados. Desde KAN-26 (T6, D-1) el primer
PDF que se genera de una póliza, y el contrato firmado, se **guardan cifrados** en ALMACENAMIENTO
(`servicio_documentos.py`) para que el documento que se entregó no cambie; este módulo solo los
genera.

KAN-26 (títulos, idioma y moneda): el título visible y el título interno del PDF llevan el nombre
del producto, y cada documento sale en el idioma (es o en) y la moneda que el usuario tenía al
generarlo (`Presentacion`). La prima se calcula y se cobra en COP: en otra moneda, cada monto lleva
debajo su equivalente en COP y el documento dice la tasa de referencia y su fecha. El contrato
firmado y la póliza se guardan así, de modo que no cambian aunque el usuario cambie de idioma o de
moneda después.

Regla #6: el nombre del asegurado llega solo en el cuerpo de la petición y no se guarda ni se
loguea. Sin él, el documento identifica al asegurado por la referencia opaca de su cliente.

Las fuentes base del PDF solo cubren Latin-1 (el español entra completo); cualquier otro carácter
se reemplaza en vez de romper el documento."""

import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from fpdf import FPDF

IDIOMAS = ("es", "en")
MONEDA_BASE = "COP"
# Decimales ISO 4217 de las monedas del selector de la web (los mismos de `bff/app/monedas.py`).
DECIMALES: dict[str, int] = {
    "COP": 2,
    "USD": 2,
    "EUR": 2,
    "MXN": 2,
    "BRL": 2,
    "PEN": 2,
    "CLP": 0,
}

# El nombre del producto, igual en el título del PDF, en la franja y en las pantallas de la web.
NOMBRE_PRODUCTO = {
    "es": {"VIDA_DEUDOR": "Seguro de Vida Hipotecario", "AUTO": "Seguro Vehicular"},
    "en": {"VIDA_DEUDOR": "Mortgage Life Insurance", "AUTO": "Vehicle Insurance"},
}

# Textos fijos del documento por idioma.
TEXTOS: dict[str, dict[str, str]] = {
    "es": {
        "titulo_borrador": "Contrato de {producto}",
        "interno_borrador": "Contrato de {producto} (borrador)",
        "titulo_firmado": "Contrato de {producto}",
        "firmado_el": "Firmado el {fecha}",
        "interno_firmado": "Contrato firmado · {producto}",
        "titulo_poliza": "Póliza de {producto} No. {numero}",
        "interno_poliza": "Póliza {numero} · {producto}",
        "asegurado": "Asegurado",
        "titular": "Titular",
        "nombre": "Nombre",
        "referencia_titular": "Referencia del titular",
        "credito_asegurado": "Crédito asegurado",
        "plazo_credito": "Plazo del crédito",
        "meses": "{n} meses",
        "valor_asegurado": "Valor asegurado",
        "vehiculo_asegurado": "Vehículo asegurado",
        "placa": "Placa",
        "vehiculo": "Vehículo",
        "modelo": "Modelo",
        "uso": "Uso",
        "ciudad": "Ciudad de circulación",
        "valor_comercial": "Valor comercial",
        "coberturas": "Coberturas",
        "incluida": "Incluida",
        "deducible": "deducible {d}",
        "prima": "Prima",
        "prima_y_cobro": "Prima y autorización de cobro",
        "prima_mensual": "Prima mensual",
        "forma_cobro": "Forma de cobro",
        "cobro_credito": "Con la cuota de tu crédito",
        "cobro_tarjeta": "Recurrente, con tu tarjeta",
        "cobro": "Cobro",
        "cobro_recurrente": "Recurrente, con la tarjeta que registres al pagar",
        "vigencia": "Vigencia",
        "desde": "Desde",
        "hasta": "Hasta",
        "estado": "Estado",
        "firma_electronica": "Firma electrónica",
        "referencia_firma": "Referencia de la firma",
        "firmada_el": "Firmada el",
        "version_documento": "Versión del documento",
        "huella_documento": "Huella del documento",
        "explicacion_huella": (
            "La huella (SHA-256) identifica el documento exacto que el titular firmó: si cambia "
            "una sola letra, la huella cambia."
        ),
        "condiciones_generales": "Condiciones generales",
        "equivale": "Equivale a {monto}",
        "tasa": (
            "Montos en {moneda} con la tasa de referencia del {fecha}: 1 {moneda} = {tasa}. "
            "La prima se calcula y se cobra en COP."
        ),
        "pie": "Solventa · Página {pagina} de {{nb}}",
        "aviso_pruebas": (
            "Documento generado en un ambiente de pruebas: la firma electrónica y el cobro son "
            "simulados y esta póliza no tiene validez legal."
        ),
        "hora_utc": "%d/%m/%Y %H:%M UTC",
    },
    "en": {
        "titulo_borrador": "{producto} Contract",
        "interno_borrador": "{producto} Contract (draft)",
        "titulo_firmado": "{producto} Contract",
        "firmado_el": "Signed on {fecha}",
        "interno_firmado": "Signed contract · {producto}",
        "titulo_poliza": "{producto} Policy No. {numero}",
        "interno_poliza": "Policy {numero} · {producto}",
        "asegurado": "Insured",
        "titular": "Policyholder",
        "nombre": "Name",
        "referencia_titular": "Policyholder reference",
        "credito_asegurado": "Insured loan",
        "plazo_credito": "Loan term",
        "meses": "{n} months",
        "valor_asegurado": "Sum insured",
        "vehiculo_asegurado": "Insured vehicle",
        "placa": "License plate",
        "vehiculo": "Vehicle",
        "modelo": "Model year",
        "uso": "Use",
        "ciudad": "City of use",
        "valor_comercial": "Market value",
        "coberturas": "Coverages",
        "incluida": "Included",
        "deducible": "deductible {d}",
        "prima": "Premium",
        "prima_y_cobro": "Premium and payment authorization",
        "prima_mensual": "Monthly premium",
        "forma_cobro": "Payment method",
        "cobro_credito": "With your loan installment",
        "cobro_tarjeta": "Recurring, with your card",
        "cobro": "Payment",
        "cobro_recurrente": "Recurring, with the card you register at checkout",
        "vigencia": "Term",
        "desde": "From",
        "hasta": "To",
        "estado": "Status",
        "firma_electronica": "Electronic signature",
        "referencia_firma": "Signature reference",
        "firmada_el": "Signed on",
        "version_documento": "Document version",
        "huella_documento": "Document fingerprint",
        "explicacion_huella": (
            "The fingerprint (SHA-256) identifies the exact document the policyholder signed: if "
            "a single letter changes, the fingerprint changes."
        ),
        "condiciones_generales": "General terms and conditions",
        "equivale": "Equivalent to {monto}",
        "tasa": (
            "Amounts in {moneda} at the reference rate of {fecha}: 1 {moneda} = {tasa}. "
            "The premium is calculated and charged in COP."
        ),
        "pie": "Solventa · Page {pagina} of {{nb}}",
        "aviso_pruebas": (
            "Document generated in a test environment: the electronic signature and the payment "
            "are simulated and this policy has no legal validity."
        ),
        "hora_utc": "%b %d, %Y %H:%M UTC",
    },
}
# Compatibilidad: el aviso en español que ya usaban las pruebas.
AVISO_AMBIENTE_PRUEBAS = TEXTOS["es"]["aviso_pruebas"]

CONDICIONES = {
    "es": {
        "VIDA_DEUDOR": (
            "Al firmar aceptas las condiciones del seguro de vida para tu crédito hipotecario y "
            "autorizas el cobro recurrente de la prima. La póliza se emite cuando se confirme el "
            "primer pago."
        ),
        "AUTO": (
            "Al firmar aceptas las condiciones del seguro vehicular y autorizas el cobro "
            "recurrente de la prima. La póliza se emite cuando se confirme el primer pago."
        ),
    },
    "en": {
        "VIDA_DEUDOR": (
            "By signing you accept the terms of the life insurance for your mortgage loan and "
            "authorize the recurring charge of the premium. The policy is issued once the first "
            "payment is confirmed."
        ),
        "AUTO": (
            "By signing you accept the terms of the vehicle insurance and authorize the recurring "
            "charge of the premium. The policy is issued once the first payment is confirmed."
        ),
    },
}
# Condiciones generales del contrato (borrador y firmado): texto de un contrato de seguro
# colombiano con las normas que lo rigen (Código de Comercio, Ley 527 de 1999, Ley 1581 de
# 2012). Es la base del producto del proyecto, no un clausulado registrado ante la
# Superintendencia Financiera.
CLAUSULAS_COMUNES = [
    (
        "Declaración del estado del riesgo",
        "El tomador declara que la información entregada para cotizar es veraz y completa. La "
        "reticencia o inexactitud sobre hechos que, conocidos por la aseguradora, la hubieran "
        "retraído de celebrar el contrato o la hubieran llevado a estipular condiciones más "
        "onerosas, produce su nulidad relativa (artículo 1058 del Código de Comercio).",
    ),
    (
        "Prima y forma de pago",
        "La prima se paga mensualmente en la forma indicada en este documento. La mora en el pago "
        "de la prima o de los certificados que se expidan con fundamento en ella produce la "
        "terminación automática del contrato (artículo 1068 del Código de Comercio). La póliza se "
        "emite cuando se confirma el primer pago.",
    ),
    (
        "Vigencia y renovación",
        "La vigencia es la que figura en la póliza. Se renueva por períodos iguales si ninguna de "
        "las partes manifiesta lo contrario y la prima se encuentra al día.",
    ),
    (
        "Aviso del siniestro",
        "El asegurado debe dar aviso del siniestro dentro de los tres (3) días hábiles "
        "siguientes a la fecha en que lo haya conocido o debido conocer (artículo 1075 del "
        "Código de Comercio), por la aplicación o la página web de Solventa, con los soportes "
        "que se le soliciten.",
    ),
    (
        "Pago de la indemnización",
        "La aseguradora paga la indemnización dentro del mes siguiente a la fecha en que el "
        "asegurado o beneficiario acredite la ocurrencia y la cuantía de la pérdida (artículo 1080 "
        "del Código de Comercio).",
    ),
    (
        "Revocación",
        "El tomador puede revocar el contrato en cualquier momento mediante aviso escrito; la "
        "aseguradora, con aviso escrito enviado con no menos de diez (10) días hábiles de "
        "antelación (artículo 1071 del Código de Comercio). La prima no devengada se devuelve.",
    ),
    (
        "Tratamiento de datos personales",
        "Los datos personales se tratan conforme a la Ley 1581 de 2012 y la autorización otorgada "
        "por el titular, solo para cotizar, emitir, administrar el seguro y atender siniestros. "
        "El titular puede conocer, actualizar, rectificar y suprimir sus datos.",
    ),
    (
        "Validez de la firma electrónica",
        "Las partes reconocen la validez de la firma electrónica de este contrato (Ley 527 de 1999 "
        "y Decreto 2364 de 2012). La evidencia de la firma y la huella del documento permiten "
        "verificar que su contenido no cambió.",
    ),
    (
        "Defensor del consumidor financiero",
        "El asegurado puede presentar quejas ante el defensor del consumidor financiero o ante la "
        "Superintendencia Financiera de Colombia.",
    ),
]
CLAUSULAS_PRODUCTO = {
    "VIDA_DEUDOR": [
        (
            "Objeto del seguro",
            "Ampara el saldo insoluto del crédito hipotecario en caso de muerte o incapacidad "
            "total y permanente del deudor asegurado. El beneficiario a título oneroso es la "
            "entidad acreedora, hasta el saldo de la deuda; el excedente, si lo hay, es de los "
            "beneficiarios que designe el asegurado.",
        ),
        (
            "Exclusiones",
            "No hay cobertura por suicidio o tentativa durante el primer año de vigencia, por "
            "enfermedades preexistentes no declaradas, ni por actos de guerra o participación en "
            "actos delictivos del asegurado.",
        ),
    ],
    "AUTO": [
        (
            "Objeto del seguro",
            "Ampara el vehículo descrito contra pérdida total o parcial por daños y por hurto, y "
            "la responsabilidad civil extracontractual del conductor autorizado, hasta los valores "
            "asegurados y con los deducibles de esta póliza. Incluye asistencia en carretera.",
        ),
        (
            "Exclusiones",
            "No hay cobertura si el conductor no tiene licencia vigente, conduce bajo el efecto "
            "del alcohol o de sustancias psicoactivas, si el vehículo se usa para un fin "
            "distinto al declarado, ni por daños mecánicos o eléctricos que no provengan de un "
            "accidente.",
        ),
        (
            "Cobertura paramétrica de clima",
            "La póliza incluye una cobertura paramétrica en la ciudad de circulación: si la fuente "
            "oficial de clima registra un evento que supera el umbral pactado, la indemnización se "
            "liquida sin necesidad de reclamación.",
        ),
    ],
}

# Traducción al inglés de las condiciones generales, para el documento en inglés. El texto que rige
# es el colombiano; la traducción conserva las mismas normas citadas.
CLAUSULAS_COMUNES_EN = [
    (
        "Disclosure of the risk",
        "The policyholder declares that the information provided for the quote is true and "
        "complete. Concealment or inaccuracy about facts that, had the insurer known them, would "
        "have kept it from entering into the contract or led it to set more onerous terms, makes "
        "the contract relatively void (article 1058 of the Colombian Commercial Code).",
    ),
    (
        "Premium and payment",
        "The premium is paid monthly as stated in this document. Late payment of the premium or "
        "of the certificates issued on its basis automatically terminates the contract (article "
        "1068 of the Colombian Commercial Code). The policy is issued once the first payment is "
        "confirmed.",
    ),
    (
        "Term and renewal",
        "The term is the one stated in the policy. It renews for equal periods unless either "
        "party states otherwise and the premium is up to date.",
    ),
    (
        "Notice of claim",
        "The insured must give notice of the claim within three (3) business days after the date "
        "on which they learned or should have learned of it (article 1075 of the Colombian "
        "Commercial Code), through the Solventa app or website, with the supporting documents "
        "requested.",
    ),
    (
        "Payment of the indemnity",
        "The insurer pays the indemnity within the month following the date on which the insured "
        "or beneficiary proves the occurrence and the amount of the loss (article 1080 of the "
        "Colombian Commercial Code).",
    ),
    (
        "Revocation",
        "The policyholder may revoke the contract at any time by written notice; the insurer, by "
        "written notice sent no less than ten (10) business days in advance (article 1071 of the "
        "Colombian Commercial Code). The unearned premium is refunded.",
    ),
    (
        "Personal data processing",
        "Personal data is processed under Law 1581 of 2012 and the authorization given by the "
        "data subject, only to quote, issue and manage the insurance and handle claims. The data "
        "subject may access, update, correct and delete their data.",
    ),
    (
        "Validity of the electronic signature",
        "The parties acknowledge the validity of the electronic signature of this contract (Law "
        "527 of 1999 and Decree 2364 of 2012). The signature evidence and the document "
        "fingerprint make it possible to verify that its content did not change.",
    ),
    (
        "Financial consumer ombudsman",
        "The insured may file complaints with the financial consumer ombudsman or with the "
        "Financial Superintendence of Colombia.",
    ),
]
CLAUSULAS_PRODUCTO_EN = {
    "VIDA_DEUDOR": [
        (
            "Purpose of the insurance",
            "Covers the outstanding balance of the mortgage loan in the event of death or total "
            "and permanent disability of the insured borrower. The lending institution is the "
            "beneficiary for value, up to the balance of the debt; any surplus goes to the "
            "beneficiaries designated by the insured.",
        ),
        (
            "Exclusions",
            "There is no coverage for suicide or attempted suicide during the first year of the "
            "term, for undisclosed pre-existing conditions, or for acts of war or the insured's "
            "participation in criminal acts.",
        ),
    ],
    "AUTO": [
        (
            "Purpose of the insurance",
            "Covers the described vehicle against total or partial loss due to damage and theft, "
            "and the third-party liability of the authorized driver, up to the sums insured and "
            "with the deductibles of this policy. Includes roadside assistance.",
        ),
        (
            "Exclusions",
            "There is no coverage if the driver does not hold a valid license, drives under the "
            "influence of alcohol or psychoactive substances, if the vehicle is used for a "
            "purpose other than the one declared, or for mechanical or electrical damage not "
            "caused by an accident.",
        ),
        (
            "Parametric weather coverage",
            "The policy includes a parametric coverage in the city of use: if the official "
            "weather source records an event that exceeds the agreed threshold, the indemnity is "
            "paid without the need for a claim.",
        ),
    ],
}
CLAUSULAS = {
    "es": (CLAUSULAS_PRODUCTO, CLAUSULAS_COMUNES),
    "en": (CLAUSULAS_PRODUCTO_EN, CLAUSULAS_COMUNES_EN),
}
NOMBRE_COBERTURA = {
    "es": {
        "RESPONSABILIDAD_CIVIL": "Responsabilidad civil extracontractual",
        "PERDIDA_TOTAL_DANOS": "Pérdida total por daños",
        "PERDIDA_PARCIAL_DANOS": "Pérdida parcial por daños",
        "PERDIDA_TOTAL_HURTO": "Pérdida total por hurto",
        "PERDIDA_PARCIAL_HURTO": "Pérdida parcial por hurto",
        "ASISTENCIA": "Asistencia (grúa, mecánico y conductor elegido)",
    },
    "en": {
        "RESPONSABILIDAD_CIVIL": "Third-party liability",
        "PERDIDA_TOTAL_DANOS": "Total loss due to damage",
        "PERDIDA_PARCIAL_DANOS": "Partial loss due to damage",
        "PERDIDA_TOTAL_HURTO": "Total loss due to theft",
        "PERDIDA_PARCIAL_HURTO": "Partial loss due to theft",
        "ASISTENCIA": "Assistance (tow truck, mechanic and designated driver)",
    },
}
NOMBRE_USO = {
    "es": {"PARTICULAR": "Particular", "COMERCIAL": "Comercial"},
    "en": {"PARTICULAR": "Private", "COMERCIAL": "Commercial"},
}
NOMBRE_ESTADO = {
    "es": {"EMITIDA": "Emitida", "VIGENTE": "Vigente", "CANCELADA": "Cancelada"},
    "en": {"EMITIDA": "Issued", "VIGENTE": "In force", "CANCELADA": "Cancelled"},
}


@dataclass(frozen=True)
class Presentacion:
    """El idioma y la moneda en que sale un documento. `cop_por_unidad` es cuántos COP vale una
    unidad de `moneda` (la tasa de referencia del BFF) y `fecha_tasa`, el día de esa tasa. Sin tasa,
    o con una moneda que no se conoce, los montos salen en su moneda original."""

    idioma: str = "es"
    moneda: str = MONEDA_BASE
    cop_por_unidad: str | None = None
    fecha_tasa: str | None = None  # AAAA-MM-DD

    @classmethod
    def desde(cls, datos: dict[str, Any] | None) -> "Presentacion":
        """De lo que llega en la petición o de lo que se guardó al firmar; tolera basura."""
        datos = datos or {}
        idioma = str(datos.get("idioma") or "es").lower()[:2]
        moneda = str(datos.get("moneda") or MONEDA_BASE).upper()
        tasa = datos.get("cop_por_unidad")
        try:
            tasa_ok = tasa is not None and Decimal(str(tasa)) > 0
        except InvalidOperation:
            tasa_ok = False
        if moneda not in DECIMALES or moneda == MONEDA_BASE or not tasa_ok:
            moneda, tasa = MONEDA_BASE, None
        return cls(
            idioma=idioma if idioma in IDIOMAS else "es",
            moneda=moneda,
            cop_por_unidad=str(tasa) if tasa is not None else None,
            fecha_tasa=str(datos.get("fecha_tasa") or "")[:10] or None,
        )

    def a_dict(self) -> dict[str, str | None]:
        return {
            "idioma": self.idioma,
            "moneda": self.moneda,
            "cop_por_unidad": self.cop_por_unidad,
            "fecha_tasa": self.fecha_tasa,
        }

    @property
    def convierte(self) -> bool:
        return self.moneda != MONEDA_BASE and self.cop_por_unidad is not None


def _latin1(texto: str) -> str:
    return texto.encode("latin-1", "replace").decode("latin-1")


def formatear_monto(valor: str, moneda: str, idioma: str = "es") -> str:
    """`185000` + `COP` -> `$ 185.000 COP` (en inglés `$185,000 COP`). Texto decimal, no float."""
    try:
        numero = Decimal(valor)
    except InvalidOperation:
        return f"{valor} {moneda}"
    entero, _, decimales = f"{numero:,.2f}".partition(".")
    if idioma == "en":
        return f"${entero}{'' if decimales == '00' else '.' + decimales} {moneda}"
    cuerpo = entero.replace(",", ".") + ("" if decimales == "00" else f",{decimales}")
    return f"$ {cuerpo} {moneda}"


def _convertir(valor: str, presentacion: Presentacion) -> str | None:
    """Un monto en COP expresado en la moneda de la presentación (texto decimal)."""
    try:
        cantidad = Decimal(valor) / Decimal(presentacion.cop_por_unidad or "")
    except (InvalidOperation, ZeroDivisionError):
        return None
    paso = Decimal(1).scaleb(-DECIMALES[presentacion.moneda])
    return str(cantidad.quantize(paso, rounding=ROUND_HALF_EVEN))


def _monto(monto: dict[str, Any], p: Presentacion) -> str:
    """El monto en la moneda elegida y, debajo, su equivalente en COP. Un monto que no está en COP
    (o sin tasa) sale tal cual."""
    original = formatear_monto(monto["valor"], monto["moneda"], p.idioma)
    if not p.convierte or monto["moneda"] != MONEDA_BASE:
        return original
    convertido = _convertir(monto["valor"], p)
    if convertido is None:
        return original
    visible = formatear_monto(convertido, p.moneda, p.idioma)
    return f"{visible}\n{TEXTOS[p.idioma]['equivale'].format(monto=original)}"


def _fecha(valor: datetime | date, idioma: str = "es") -> str:
    return valor.strftime("%b %d, %Y" if idioma == "en" else "%d/%m/%Y")


def _fecha_tasa(p: Presentacion) -> str:
    try:
        return _fecha(date.fromisoformat(p.fecha_tasa or ""), p.idioma)
    except ValueError:
        return _fecha(datetime.now(UTC), p.idioma)


def nombre_producto(producto: str, idioma: str = "es") -> str:
    nombres = NOMBRE_PRODUCTO.get(idioma, NOMBRE_PRODUCTO["es"])
    return nombres.get(producto, nombres["VIDA_DEUDOR"])


class _Documento(FPDF):
    def __init__(self, titulo_interno: str, idioma: str = "es") -> None:
        super().__init__(format="A4")
        self._idioma = idioma
        self.set_compression(False)  # texto legible en el flujo y salida estable
        # El título interno es el que muestra el visor del navegador (pestaña y barra del visor).
        self.set_title(titulo_interno)
        self.set_lang("en" if idioma == "en" else "es-CO")
        self.set_author("Solventa")
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()

    def membrete(self, producto: str, titulo: str, bajada: str | None = None) -> None:
        """La franja con SOLVENTA y el nombre del producto, y el título del documento."""
        self.set_fill_color(18, 52, 86)
        self.rect(0, 0, 210, 32, style="F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 22)
        self.set_xy(15, 8)
        self.cell(0, 9, "SOLVENTA", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 11)
        self.set_x(15)
        self.cell(0, 6, _latin1(nombre_producto(producto, self._idioma)))
        self.set_text_color(0, 0, 0)
        self.set_y(40)
        self.set_font("Helvetica", "B", 16)
        self.multi_cell(0, 8, _latin1(titulo), new_x="LMARGIN", new_y="NEXT")
        if bajada:
            self.set_font("Helvetica", "", 10)
            self.set_text_color(90, 90, 90)
            self.cell(0, 6, _latin1(bajada), new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self) -> None:
        """Pie de cada página: quién emite y el número de página, como un contrato real."""
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        texto = TEXTOS[self._idioma]["pie"].format(pagina=self.page_no())
        self.cell(0, 5, _latin1(texto), align="C")
        self.set_text_color(0, 0, 0)

    def firma(self, imagen: bytes | None, texto: str | None) -> None:
        """La firma del titular estampada (W03-24): la imagen (trazo o cargada) o su nombre con una
        letra cursiva. Sin ninguna, el documento conserva solo el sello de la evidencia."""
        if imagen:
            self.image(io.BytesIO(imagen), w=60)
            self.ln(2)
        elif texto:
            self.set_font("Times", "BI", 22)
            self.cell(0, 12, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
            self.ln(1)

    def seccion(self, titulo: str, filas: list[tuple[str, str]], ancho: int = 55) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_fill_color(235, 240, 246)
        self.cell(0, 7, _latin1(titulo), new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(1)
        for etiqueta, valor in filas:
            self.set_font("Helvetica", "B", 10)
            self.cell(ancho, 6, _latin1(etiqueta))
            self.set_font("Helvetica", "", 10)
            self.multi_cell(0, 6, _latin1(valor), new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def nota(self, texto: str) -> None:
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(90, 90, 90)
        self.multi_cell(0, 4, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def aviso_pruebas(self) -> None:
        self.ln(3)
        self.set_text_color(160, 0, 0)
        self.set_font("Helvetica", "B", 9)
        texto = TEXTOS[self._idioma]["aviso_pruebas"]
        self.multi_cell(0, 5, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)


def _nota_tasa(pdf: _Documento, p: Presentacion) -> None:
    """La tasa de referencia y su fecha, solo cuando los montos están en otra moneda."""
    if not p.convierte:
        return
    tasa = formatear_monto(p.cop_por_unidad or "", MONEDA_BASE, p.idioma)
    pdf.nota(TEXTOS[p.idioma]["tasa"].format(moneda=p.moneda, fecha=_fecha_tasa(p), tasa=tasa))


def _condiciones_generales(pdf: _Documento, producto: str, idioma: str) -> None:
    """Las cláusulas numeradas del contrato: primero las del producto, luego las comunes."""
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(235, 240, 246)
    titulo = TEXTOS[idioma]["condiciones_generales"]
    pdf.cell(0, 7, _latin1(titulo), new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.ln(1)
    del_producto, comunes = CLAUSULAS[idioma]
    clausulas = del_producto.get(producto, del_producto["VIDA_DEUDOR"])
    for i, (titulo, texto) in enumerate([*clausulas, *comunes], start=1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, _latin1(f"{i}. {titulo}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
    pdf.ln(2)


def _secciones_del_producto(pdf: _Documento, propuesta: dict[str, Any], p: Presentacion) -> None:
    """Vida: el crédito asegurado. Auto (KAN-290): el vehículo (placa enmascarada: la placa completa
    nunca se guarda) y las coberturas con su valor asegurado y deducible."""
    t = TEXTOS[p.idioma]
    cobertura = propuesta["cobertura"]
    if propuesta.get("producto") != "AUTO":
        pdf.seccion(
            t["credito_asegurado"],
            [
                (t["plazo_credito"], t["meses"].format(n=propuesta["plazo_meses"])),
                (t["valor_asegurado"], _monto(cobertura, p)),
            ],
        )
        return
    vehiculo = propuesta.get("vehiculo") or {}
    usos = NOMBRE_USO[p.idioma]
    pdf.seccion(
        t["vehiculo_asegurado"],
        [
            # «•» no existe en Latin-1 (las fuentes base del PDF): se dibuja con asteriscos.
            (t["placa"], vehiculo.get("placaEnmascarada", "").replace("•", "*")),
            (t["vehiculo"], f"{vehiculo.get('marca', '')} {vehiculo.get('linea', '')}".strip()),
            (t["modelo"], str(vehiculo.get("modelo", ""))),
            (t["uso"], usos.get(vehiculo.get("uso", ""), vehiculo.get("uso", ""))),
            (t["ciudad"], vehiculo.get("ciudadCirculacion", "")),
            (t["valor_comercial"], _monto(cobertura, p)),
        ],
    )
    nombres = NOMBRE_COBERTURA[p.idioma]
    filas = []
    for item in propuesta.get("coberturas") or []:
        valor = item.get("valorAsegurado")
        texto = _monto(valor, p) if valor else t["incluida"]
        if item.get("deducible"):
            # El deducible es un texto libre del catálogo de tarifas (en español).
            primera, _, resto = texto.partition("\n")
            texto = f"{primera} · {t['deducible'].format(d=item['deducible'])}"
            texto += f"\n{resto}" if resto else ""
        filas.append((nombres.get(item["tipo"], item["tipo"]), texto))
    pdf.seccion(t["coberturas"], filas, ancho=85)


def _seccion_prima_y_cobro(pdf: _Documento, propuesta: dict[str, Any], p: Presentacion) -> None:
    t = TEXTOS[p.idioma]
    pdf.seccion(
        t["prima_y_cobro"],
        [(t["prima_mensual"], _monto(propuesta["prima"], p)), (t["cobro"], t["cobro_recurrente"])],
    )


def _condiciones(pdf: _Documento, producto: str, idioma: str) -> None:
    textos = CONDICIONES[idioma]
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        5,
        _latin1(textos.get(producto, textos["VIDA_DEUDOR"])),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)


def _seccion_titular(
    pdf: _Documento, titulo: str, nombre: str, referencia: str, p: Presentacion
) -> None:
    t = TEXTOS[p.idioma]
    pdf.seccion(
        titulo, [(t["nombre"], nombre)] if nombre else [(t["referencia_titular"], referencia)]
    )


def generar_pdf(
    poliza: dict[str, Any],
    propuesta: dict[str, Any],
    evidencia: dict[str, Any],
    numero: str,
    asegurado: str | None = None,
    ambiente_pruebas: bool = True,
    firma_imagen: bytes | None = None,
    firma_texto: str | None = None,
    presentacion: Presentacion | None = None,
) -> bytes:
    """El PDF de la póliza. `numero` es el número ya formateado (`SOL-AAAA-NNNNNN`). `firma_imagen`
    o `firma_texto` estampan la firma del titular (bloque 9); nada de eso se guarda."""
    p = presentacion or Presentacion()
    t = TEXTOS[p.idioma]
    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    nombre_prod = nombre_producto(producto, p.idioma)
    pdf = _Documento(t["interno_poliza"].format(numero=numero, producto=nombre_prod), p.idioma)
    pdf.membrete(producto, t["titulo_poliza"].format(producto=nombre_prod, numero=numero))
    nombre = (asegurado or "").strip()
    _seccion_titular(pdf, t["asegurado"], nombre, str(poliza["cliente_id"])[:12], p)
    _secciones_del_producto(pdf, propuesta, p)
    pdf.seccion(
        t["prima"],
        [
            (t["prima_mensual"], _monto(propuesta["prima"], p)),
            (t["forma_cobro"], t["cobro_credito"] if producto != "AUTO" else t["cobro_tarjeta"]),
        ],
    )
    estados = NOMBRE_ESTADO[p.idioma]
    pdf.seccion(
        t["vigencia"],
        [
            (t["desde"], _fecha(poliza["vigencia_desde"], p.idioma)),
            (t["hasta"], _fecha(poliza["vigencia_hasta"], p.idioma)),
            (t["estado"], estados.get(poliza["estado"], poliza["estado"])),
        ],
    )
    _nota_tasa(pdf, p)
    pdf.seccion(
        t["firma_electronica"],
        [
            (t["referencia_firma"], evidencia["referencia_proveedor"]),
            (t["firmada_el"], evidencia["firmada_en"].strftime(t["hora_utc"])),
            (t["huella_documento"], evidencia["documento_hash"]),
        ],
    )
    pdf.firma(firma_imagen, firma_texto)
    pdf.nota(t["explicacion_huella"])
    if ambiente_pruebas:
        pdf.aviso_pruebas()
    return bytes(pdf.output())


def generar_borrador(
    propuesta: dict[str, Any],
    ambiente_pruebas: bool = True,
    presentacion: Presentacion | None = None,
) -> bytes:
    """El contrato que el titular lee antes de firmar (W03-31: W03-02 muestra el contrato en PDF).
    Es un borrador: sin número de póliza, vigencia ni firma. No lleva datos personales. No se
    guarda: sale en el idioma y la moneda de quien lo abre."""
    p = presentacion or Presentacion()
    t = TEXTOS[p.idioma]
    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    nombre_prod = nombre_producto(producto, p.idioma)
    pdf = _Documento(t["interno_borrador"].format(producto=nombre_prod), p.idioma)
    pdf.membrete(producto, t["titulo_borrador"].format(producto=nombre_prod))
    _secciones_del_producto(pdf, propuesta, p)
    _seccion_prima_y_cobro(pdf, propuesta, p)
    _nota_tasa(pdf, p)
    _condiciones(pdf, producto, p.idioma)
    _condiciones_generales(pdf, producto, p.idioma)
    if ambiente_pruebas:
        pdf.aviso_pruebas()
    return bytes(pdf.output())


def generar_contrato_firmado(
    propuesta: dict[str, Any],
    evidencia: dict[str, Any],
    asegurado: str | None = None,
    ambiente_pruebas: bool = True,
    firma_imagen: bytes | None = None,
    firma_texto: str | None = None,
    presentacion: Presentacion | None = None,
) -> bytes:
    """El contrato que el titular firmó (T6): lo mismo que leyó (`generar_borrador`) más la
    evidencia de la firma (referencia del proveedor, cuándo, huella del documento) y la firma
    estampada. Se guarda cifrado al firmar. Sin nombre, identifica al titular por su referencia
    opaca."""
    p = presentacion or Presentacion()
    t = TEXTOS[p.idioma]
    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    nombre_prod = nombre_producto(producto, p.idioma)
    pdf = _Documento(t["interno_firmado"].format(producto=nombre_prod), p.idioma)
    pdf.membrete(
        producto,
        t["titulo_firmado"].format(producto=nombre_prod),
        bajada=t["firmado_el"].format(fecha=_fecha(evidencia["firmada_en"], p.idioma)),
    )
    nombre = (asegurado or "").strip()
    _seccion_titular(pdf, t["titular"], nombre, str(propuesta["cliente_id"])[:12], p)
    _secciones_del_producto(pdf, propuesta, p)
    _seccion_prima_y_cobro(pdf, propuesta, p)
    _nota_tasa(pdf, p)
    _condiciones(pdf, producto, p.idioma)
    _condiciones_generales(pdf, producto, p.idioma)
    pdf.ln(3)
    pdf.seccion(
        t["firma_electronica"],
        [
            (t["referencia_firma"], evidencia["referencia_proveedor"]),
            (t["firmada_el"], evidencia["firmada_en"].strftime(t["hora_utc"])),
            (t["version_documento"], evidencia["version_documento"]),
            (t["huella_documento"], evidencia["documento_hash"]),
        ],
    )
    pdf.firma(firma_imagen, firma_texto)
    if ambiente_pruebas:
        pdf.aviso_pruebas()
    return bytes(pdf.output())
