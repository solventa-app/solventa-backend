"""Casos adicionales de la semilla de siniestros, para llenar la bandeja y probar su paginación.

Cada caso parte de uno PENDIENTE de `siniestros.json`, del que hereda el relato, la ubicación y las
evidencias: las fotos siguen siendo las de ese relato, y un caso sin resolver no menciona ninguna
decisión. Lo demás sale del generador: asegurado, radicado, fechas, suma, estado y resolución. El
generador usa una semilla
fija para que los ids y los estados sean los mismos en cada corrida: `sembrar` es idempotente y el
reinicio de la demo debe volver a lo mismo.

Reglas que se cumplen siempre, como en una gestión real:
- un caso EN_REVISION tiene perito, porque solo se llega ahí derivando (W04);
- el perito es el más cercano a la ciudad del caso;
- la indemnización nunca supera la suma reclamada, y un rechazo trae su motivo;
- un rechazo por relato o evidencia deja esa verificación sin cumplir.
"""

import random
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timedelta

SEMILLA = 4501
CANTIDAD = 10
PRIMER_NUMERO = 11  # Sigue a sin-10, pol-10 y demo-siniestros-prop-10.
PRIMER_RADICADO = 201

# El reparto queda fijo para que la bandeja traiga casos en todos los filtros.
ESTADOS = (["PENDIENTE"] * 3) + (["EN_REVISION"] * 3) + (["APROBADO"] * 2) + (["RECHAZADO"] * 2)

NOMBRES = ("Laura", "Andrés", "Camila", "Julián", "Natalia", "Mateo", "Paula", "Santiago")
APELLIDOS = ("Rojas", "Pineda", "Cárdenas", "Ospina", "Mejía", "Quintero", "Vargas", "Salazar")

PERITO_POR_CIUDAD = {"Cali": "per-03", "Medellín": "per-04"}
PERITO_POR_DEFECTO = "per-01"
PERITO_CARIBE = "per-02"
CIUDADES_CARIBE = ("Barranquilla", "Cartagena")

MOTIVOS_RECHAZO = {
    "EVIDENCIA_INSUFICIENTE": (
        "Georreferenciación",
        "Las evidencias aportadas no permiten establecer el lugar de ocurrencia del hecho.",
    ),
    "INCONSISTENCIA_EN_RELATO": (
        "Identidad",
        "El relato del asegurado no coincide con la información validada para el caso.",
    ),
}
VIA_RECURSO = (
    "El asegurado puede aportar nuevas pruebas dentro de 15 días hábiles o acudir al defensor del "
    "consumidor financiero."
)


def _perito_de(ciudad: str) -> str:
    if ciudad in CIUDADES_CARIBE:
        return PERITO_CARIBE
    return PERITO_POR_CIUDAD.get(ciudad, PERITO_POR_DEFECTO)


def _documento(azar: random.Random) -> str:
    numero = azar.randrange(10_000_000, 99_999_999)
    return f"C.C. {numero:,}".replace(",", ".")


def _evidencias(base: dict, numero: int, ocurrido: datetime, azar: random.Random) -> list[dict]:
    evidencias = []
    registrada = ocurrido
    for orden, evidencia in enumerate(base["evidencias"], start=1):
        registrada += timedelta(minutes=azar.randrange(5, 40))
        evidencias.append(
            {
                **evidencia,
                "id": f"ev-{numero:02d}-{orden}",
                "registrada_en": registrada.isoformat(),
            }
        )
    return evidencias


def _derivacion(ciudad: str, resuelta: datetime, azar: random.Random) -> dict:
    return {
        "tipo": "DERIVACION",
        "resuelta_en": resuelta.isoformat(),
        "notificacion": (
            "Se informó al asegurado por correo y app móvil que su reclamación fue derivada a "
            "peritaje externo."
        ),
        "perito_id": _perito_de(ciudad),
        "instrucciones": "Verificar el daño reportado y estimar el costo real de reparación.",
        "vence_informe_horas": azar.randrange(48, 168),
    }


def _aprobacion(suma: int, resuelta: datetime, azar: random.Random) -> dict:
    deducible = azar.choice((0.9, 0.95, 1))
    return {
        "tipo": "APROBACION",
        "resuelta_en": resuelta.isoformat(),
        "notificacion": (
            "Se notificó la aprobación al asegurado por correo y app móvil, con copia al banco "
            "acreedor."
        ),
        "indemnizacion": {"valor": str(int(suma * deducible)), "moneda": "COP"},
        "metodo_transferencia": "ACH Colombia Interbancario",
        "plazo_dispersion": "1 a 3 días hábiles",
    }


def _rechazo(caso: dict, resuelta: datetime, azar: random.Random) -> dict:
    motivo = azar.choice(sorted(MOTIVOS_RECHAZO))
    verificacion, comentario = MOTIVOS_RECHAZO[motivo]
    for fila in caso["verificaciones"]:
        if fila["descripcion"].startswith(verificacion):
            fila["cumple"] = False
    return {
        "tipo": "RECHAZO",
        "resuelta_en": resuelta.isoformat(),
        "notificacion": (
            "Se notificó el rechazo al asegurado por correo y app móvil, con copia al banco "
            "acreedor."
        ),
        "motivo": motivo,
        "comentario": comentario,
        "via_recurso": VIA_RECURSO,
    }


def generar(bases: Sequence[dict], ahora: datetime) -> list[dict]:
    """Los casos adicionales, en el formato de `siniestros.json`. Las fechas son relativas a
    `ahora`, para que la cuenta regresiva y la bandeja tengan sentido en cualquier día."""
    azar = random.Random(SEMILLA)
    pendientes = [base for base in bases if base["estado"] == "PENDIENTE"]
    estados = list(ESTADOS)
    azar.shuffle(estados)
    casos = []
    radicado = PRIMER_RADICADO
    for indice, estado in enumerate(estados):
        numero = PRIMER_NUMERO + indice
        base = azar.choice(pendientes)
        radicado += azar.randrange(1, 6)
        ocurrido = ahora - timedelta(days=azar.randrange(2, 12), minutes=azar.randrange(0, 600))
        notificado = ocurrido + timedelta(hours=azar.randrange(1, 8))
        resuelta = notificado + timedelta(hours=azar.randrange(4, 30))
        suma = azar.randrange(1_500, 18_000) * 1000
        caso = deepcopy(base)
        caso.update(
            id=f"sin-{numero:02d}",
            radicado=f"SIN-2026-{radicado:04d}",
            estado=estado,
            asegurado={
                "nombre": " ".join(
                    (azar.choice(NOMBRES), azar.choice(APELLIDOS), azar.choice(APELLIDOS))
                ),
                "documento": _documento(azar),
            },
            poliza_id=f"pol-{numero:02d}",
            ocurrido_en=ocurrido.isoformat(),
            notificado_en=notificado.isoformat(),
            suma_reclamada={"valor": str(suma), "moneda": "COP"},
            vence_dictamen_horas=azar.randrange(6, 72),
            evidencias=_evidencias(base, numero, ocurrido, azar),
            resolucion=None,
        )
        if estado == "EN_REVISION":
            caso["resolucion"] = _derivacion(caso["ubicacion"]["ciudad"], resuelta, azar)
        elif estado == "APROBADO":
            caso["resolucion"] = _aprobacion(suma, resuelta, azar)
        elif estado == "RECHAZADO":
            caso["resolucion"] = _rechazo(caso, resuelta, azar)
        casos.append(caso)
    return casos
