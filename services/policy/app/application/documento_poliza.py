"""El PDF de la póliza (HU-W03, KAN-215, CA-W03-08).

Un documento formal con el membrete, el asegurado, el crédito, la cobertura, la prima, la vigencia
y el sello de la firma electrónica (la referencia de la evidencia, cuándo se firmó y la huella del
documento que se firmó). Se genera al pedirlo, a partir de los datos guardados: no se almacena, así
que el PDF nunca queda desactualizado ni es un archivo más que proteger.

Regla #6: el nombre del asegurado llega solo en el cuerpo de la petición y no se guarda ni se
loguea. Sin él, el documento identifica al asegurado por la referencia opaca de su cliente.

Las fuentes base del PDF solo cubren Latin-1 (el español entra completo); cualquier otro carácter
se reemplaza en vez de romper el documento."""

import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fpdf import FPDF

AVISO_AMBIENTE_PRUEBAS = (
    "Documento generado en un ambiente de pruebas: la firma electrónica y el cobro son simulados "
    "y esta póliza no tiene validez legal."
)
FORMA_COBRO = "con la cuota de tu crédito"


def _latin1(texto: str) -> str:
    return texto.encode("latin-1", "replace").decode("latin-1")


def formatear_monto(valor: str, moneda: str) -> str:
    """`185000` + `COP` -> `$ 185.000 COP`. Texto decimal de entrada, nunca float."""
    try:
        numero = Decimal(valor)
    except InvalidOperation:
        return f"{valor} {moneda}"
    entero, _, decimales = f"{numero:,.2f}".partition(".")
    cuerpo = entero.replace(",", ".") + ("" if decimales == "00" else f",{decimales}")
    return f"$ {cuerpo} {moneda}"


def _fecha(valor: datetime) -> str:
    return valor.strftime("%d/%m/%Y")


class _Documento(FPDF):
    def __init__(self, numero: str) -> None:
        super().__init__(format="A4")
        self.set_compression(False)  # texto legible en el flujo y salida estable
        self.set_title(_latin1(f"Poliza {numero}"))
        self.set_author("Solventa")
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()

    def membrete(self, numero: str) -> None:
        self.set_fill_color(18, 52, 86)
        self.rect(0, 0, 210, 32, style="F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 22)
        self.set_xy(15, 8)
        self.cell(0, 9, "SOLVENTA", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 11)
        self.set_x(15)
        self.cell(0, 6, _latin1("Seguro de vida para tu crédito hipotecario"))
        self.set_text_color(0, 0, 0)
        self.set_y(40)
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 9, _latin1(f"Póliza No. {numero}"), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

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

    def seccion(self, titulo: str, filas: list[tuple[str, str]]) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_fill_color(235, 240, 246)
        self.cell(0, 7, _latin1(titulo), new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(1)
        for etiqueta, valor in filas:
            self.set_font("Helvetica", "B", 10)
            self.cell(55, 6, _latin1(etiqueta))
            self.set_font("Helvetica", "", 10)
            self.multi_cell(0, 6, _latin1(valor), new_x="LMARGIN", new_y="NEXT")
        self.ln(3)


def generar_pdf(
    poliza: dict[str, Any],
    propuesta: dict[str, Any],
    evidencia: dict[str, Any],
    numero: str,
    asegurado: str | None = None,
    ambiente_pruebas: bool = True,
    firma_imagen: bytes | None = None,
    firma_texto: str | None = None,
) -> bytes:
    """El PDF de la póliza. `numero` es el número ya formateado (`SOL-AAAA-NNNNNN`). `firma_imagen`
    o `firma_texto` estampan la firma del titular (bloque 9); nada de eso se guarda."""
    prima, cobertura = propuesta["prima"], propuesta["cobertura"]
    nombre = (asegurado or "").strip()
    referencia = str(poliza["cliente_id"])[:12]

    pdf = _Documento(numero)
    pdf.membrete(numero)
    pdf.seccion(
        "Asegurado",
        [("Nombre", nombre)] if nombre else [("Referencia del titular", referencia)],
    )
    pdf.seccion(
        "Crédito asegurado",
        [
            ("Plazo del crédito", f"{propuesta['plazo_meses']} meses"),
            ("Valor asegurado", formatear_monto(cobertura["valor"], cobertura["moneda"])),
        ],
    )
    pdf.seccion(
        "Prima",
        [
            ("Prima mensual", formatear_monto(prima["valor"], prima["moneda"])),
            ("Forma de cobro", FORMA_COBRO),
        ],
    )
    pdf.seccion(
        "Vigencia",
        [
            ("Desde", _fecha(poliza["vigencia_desde"])),
            ("Hasta", _fecha(poliza["vigencia_hasta"])),
            ("Estado", poliza["estado"]),
        ],
    )
    pdf.seccion(
        "Firma electrónica",
        [
            ("Referencia de la firma", evidencia["referencia_proveedor"]),
            ("Firmada el", evidencia["firmada_en"].strftime("%d/%m/%Y %H:%M UTC")),
            ("Huella del documento", evidencia["documento_hash"]),
        ],
    )
    pdf.firma(firma_imagen, firma_texto)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(
        0,
        4,
        _latin1(
            "La huella (SHA-256) identifica el documento exacto que el titular firmó: si cambia "
            "una sola letra, la huella cambia."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    if ambiente_pruebas:
        pdf.ln(3)
        pdf.set_text_color(160, 0, 0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, _latin1(AVISO_AMBIENTE_PRUEBAS), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def generar_borrador(propuesta: dict[str, Any], ambiente_pruebas: bool = True) -> bytes:
    """El contrato que el titular lee antes de firmar (W03-31: W03-02 muestra el contrato en PDF).
    Es un borrador: sin número de póliza, vigencia ni firma. No lleva datos personales."""
    prima, cobertura = propuesta["prima"], propuesta["cobertura"]
    pdf = _Documento("borrador")
    pdf.membrete("en trámite")
    pdf.seccion(
        "Crédito asegurado",
        [
            ("Plazo del crédito", f"{propuesta['plazo_meses']} meses"),
            ("Valor asegurado", formatear_monto(cobertura["valor"], cobertura["moneda"])),
        ],
    )
    pdf.seccion(
        "Prima y autorización de cobro",
        [
            ("Prima mensual", formatear_monto(prima["valor"], prima["moneda"])),
            ("Cobro", "Recurrente, con la tarjeta que registres al pagar"),
        ],
    )
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        5,
        _latin1(
            "Al firmar aceptas las condiciones del seguro de vida para tu crédito hipotecario y "
            "autorizas el cobro recurrente de la prima. La póliza se emite cuando se confirme el "
            "primer pago."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    if ambiente_pruebas:
        pdf.ln(3)
        pdf.set_text_color(160, 0, 0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, _latin1(AVISO_AMBIENTE_PRUEBAS), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
