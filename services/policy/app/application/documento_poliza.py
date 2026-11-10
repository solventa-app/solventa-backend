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
# KAN-290: el subtítulo, el texto del contrato y los nombres de las coberturas de cada producto.
SUBTITULO = {
    "VIDA_DEUDOR": "Seguro de vida para tu crédito hipotecario",
    "AUTO": "Seguro de automóviles todo riesgo",
}
CONDICIONES = {
    "VIDA_DEUDOR": (
        "Al firmar aceptas las condiciones del seguro de vida para tu crédito hipotecario y "
        "autorizas el cobro recurrente de la prima. La póliza se emite cuando se confirme el "
        "primer pago."
    ),
    "AUTO": (
        "Al firmar aceptas las condiciones del seguro de automóviles todo riesgo y autorizas el "
        "cobro recurrente de la prima. La póliza se emite cuando se confirme el primer pago."
    ),
}
NOMBRE_COBERTURA = {
    "RESPONSABILIDAD_CIVIL": "Responsabilidad civil extracontractual",
    "PERDIDA_TOTAL_DANOS": "Pérdida total por daños",
    "PERDIDA_PARCIAL_DANOS": "Pérdida parcial por daños",
    "PERDIDA_TOTAL_HURTO": "Pérdida total por hurto",
    "PERDIDA_PARCIAL_HURTO": "Pérdida parcial por hurto",
    "ASISTENCIA": "Asistencia (grúa, mecánico y conductor elegido)",
}
NOMBRE_USO = {"PARTICULAR": "Particular", "COMERCIAL": "Comercial"}


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

    def membrete(self, numero: str, producto: str = "VIDA_DEUDOR") -> None:
        self.set_fill_color(18, 52, 86)
        self.rect(0, 0, 210, 32, style="F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 22)
        self.set_xy(15, 8)
        self.cell(0, 9, "SOLVENTA", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 11)
        self.set_x(15)
        self.cell(0, 6, _latin1(SUBTITULO.get(producto, SUBTITULO["VIDA_DEUDOR"])))
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


def _secciones_del_producto(pdf: _Documento, propuesta: dict[str, Any]) -> None:
    """Vida: el crédito asegurado. Auto (KAN-290): el vehículo (placa enmascarada: la placa completa
    nunca se guarda) y las coberturas con su valor asegurado y deducible."""
    cobertura = propuesta["cobertura"]
    if propuesta.get("producto") != "AUTO":
        pdf.seccion(
            "Crédito asegurado",
            [
                ("Plazo del crédito", f"{propuesta['plazo_meses']} meses"),
                ("Valor asegurado", formatear_monto(cobertura["valor"], cobertura["moneda"])),
            ],
        )
        return
    vehiculo = propuesta.get("vehiculo") or {}
    pdf.seccion(
        "Vehículo asegurado",
        [
            # «•» no existe en Latin-1 (las fuentes base del PDF): se dibuja con asteriscos.
            ("Placa", vehiculo.get("placaEnmascarada", "").replace("•", "*")),
            ("Vehículo", f"{vehiculo.get('marca', '')} {vehiculo.get('linea', '')}".strip()),
            ("Modelo", str(vehiculo.get("modelo", ""))),
            ("Uso", NOMBRE_USO.get(vehiculo.get("uso", ""), vehiculo.get("uso", ""))),
            ("Ciudad de circulación", vehiculo.get("ciudadCirculacion", "")),
            ("Valor comercial", formatear_monto(cobertura["valor"], cobertura["moneda"])),
        ],
    )
    filas = []
    for item in propuesta.get("coberturas") or []:
        valor = item.get("valorAsegurado")
        texto = formatear_monto(valor["valor"], valor["moneda"]) if valor else "Incluida"
        if item.get("deducible"):
            texto += f" · deducible {item['deducible']}"
        filas.append((NOMBRE_COBERTURA.get(item["tipo"], item["tipo"]), texto))
    pdf.seccion("Coberturas", filas, ancho=85)


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
    prima = propuesta["prima"]
    nombre = (asegurado or "").strip()
    referencia = str(poliza["cliente_id"])[:12]

    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    pdf = _Documento(numero)
    pdf.membrete(numero, producto)
    pdf.seccion(
        "Asegurado",
        [("Nombre", nombre)] if nombre else [("Referencia del titular", referencia)],
    )
    _secciones_del_producto(pdf, propuesta)
    pdf.seccion(
        "Prima",
        [
            ("Prima mensual", formatear_monto(prima["valor"], prima["moneda"])),
            (
                "Forma de cobro",
                FORMA_COBRO if producto != "AUTO" else "Recurrente, con tu tarjeta",
            ),
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
    prima = propuesta["prima"]
    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    pdf = _Documento("borrador")
    pdf.membrete("en trámite", producto)
    _secciones_del_producto(pdf, propuesta)
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
        _latin1(CONDICIONES.get(producto, CONDICIONES["VIDA_DEUDOR"])),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    if ambiente_pruebas:
        pdf.ln(3)
        pdf.set_text_color(160, 0, 0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, _latin1(AVISO_AMBIENTE_PRUEBAS), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
