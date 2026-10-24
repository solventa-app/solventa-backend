"""Reglas de la decisión de suscripción (HU-W03, KAN-213, CA-W03-07 y CA-W03-09).

Son reglas simples y explícitas, no un modelo: lo que se necesita es una decisión automática,
rápida y que se pueda explicar. Una suscripción se rechaza solo si el KYC rechaza la identidad; todo
lo demás que no permita aprobar sola (KYC sin respuesta, riesgo alto o desconocido, cobertura por
encima del tope, moneda no soportada) va a **revisión manual**: nunca se aprueba a ciegas ni se
rechaza por una falla de un proveedor.

Los motivos son códigos, sin datos personales ni montos, para poder loguearlos."""

from dataclasses import dataclass, field
from decimal import Decimal

APROBADA = "APROBADA"
RECHAZADA = "RECHAZADA"
REVISION_MANUAL = "REVISION_MANUAL"

KYC_RECHAZADO = "KYC_RECHAZADO"
KYC_NO_DISPONIBLE = "KYC_NO_DISPONIBLE"
RIESGO_ALTO = "RIESGO_ALTO"
RIESGO_DESCONOCIDO = "RIESGO_DESCONOCIDO"
COBERTURA_ALTA = "COBERTURA_ALTA"
MONEDA_NO_SOPORTADA = "MONEDA_NO_SOPORTADA"

MONEDA = "COP"
NIVELES_QUE_APRUEBAN = ("BAJO", "MEDIO")


@dataclass
class Decision:
    decision: str
    motivos: list[str] = field(default_factory=list)


def decidir(
    estado_kyc: str,
    nivel_riesgo: str | None,
    cobertura: Decimal,
    moneda: str,
    cobertura_maxima: Decimal,
) -> Decision:
    if estado_kyc == "rechazada":
        return Decision(RECHAZADA, [KYC_RECHAZADO])

    motivos: list[str] = []
    if estado_kyc != "aprobada":
        motivos.append(KYC_NO_DISPONIBLE)

    nivel = (nivel_riesgo or "").strip().upper()
    if nivel == "ALTO":
        motivos.append(RIESGO_ALTO)
    elif nivel not in NIVELES_QUE_APRUEBAN:
        motivos.append(RIESGO_DESCONOCIDO)

    if moneda != MONEDA:
        motivos.append(MONEDA_NO_SOPORTADA)
    elif cobertura > cobertura_maxima:
        motivos.append(COBERTURA_ALTA)

    return Decision(REVISION_MANUAL if motivos else APROBADA, motivos)
