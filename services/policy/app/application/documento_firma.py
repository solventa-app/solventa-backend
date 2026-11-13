"""El documento que el cliente firma y su hash (HU-W03, CA-W03-02 y CA-W03-03).

El documento se construye siempre desde la propuesta guardada (el precio sale de RATING, nunca del
cliente) y se serializa de forma canónica: el mismo documento da siempre el mismo hash. Ese hash es
lo único que ve el proveedor de firma; el texto exacto se guarda cifrado junto a la evidencia para
poder demostrar después qué se firmó."""

import hashlib
import json
from typing import Any

VERSION_DOCUMENTO = "v1"
FORMA_COBRO = "con la cuota de tu crédito"
# KAN-290: la póliza de auto no se cobra con la cuota de un crédito: es recurrente con tarjeta.
FORMA_COBRO_AUTO = "con tu tarjeta, de forma recurrente"
NOMBRE_SEGURO = {
    "VIDA_DEUDOR": "Seguro de vida para tu crédito hipotecario",
    "AUTO": "Seguro de automóviles todo riesgo",
}


class DocumentoNoGenerable(Exception):
    """La propuesta no tiene lo necesario para armar el documento (p. ej. el plazo)."""


def construir_documento(propuesta: dict[str, Any]) -> dict[str, Any]:
    meses = propuesta.get("plazo_meses")
    if not meses:
        raise DocumentoNoGenerable("la propuesta no trae el plazo")
    prima = propuesta["prima"]
    producto = propuesta.get("producto") or "VIDA_DEUDOR"
    forma_cobro = FORMA_COBRO_AUTO if producto == "AUTO" else FORMA_COBRO
    return {
        "version": VERSION_DOCUMENTO,
        "propuesta_id": propuesta["id"],
        "resumen": (
            f"{NOMBRE_SEGURO.get(producto, NOMBRE_SEGURO['VIDA_DEUDOR'])}: prima de "
            f"{prima['valor']} {prima['moneda']} al mes durante {meses} meses, cobrada "
            f"{forma_cobro}. Al firmar autorizas ese cobro recurrente."
        ),
        "monto_mensual": {"valor": prima["valor"], "moneda": prima["moneda"]},
        "meses": meses,
        "forma_cobro": forma_cobro,
        "cobertura": propuesta["cobertura"],
    }


def serializar(documento: dict[str, Any]) -> str:
    """Forma canónica: claves ordenadas y sin espacios, para que el hash sea estable."""
    return json.dumps(documento, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_documento(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()
