"""Oferta de la póliza de auto (KAN-290, tarea 5 de `docs/plan-poliza-auto.md`).

RATING lee el perfil del vehículo que escribió RISK (`perfiles_vehiculo`) de la secundaria, con la
misma sesión causal y el mismo respaldo en la primaria que el perfil financiero (D-02, regla 1:
RATING nunca escribe en Riesgo), y lo tarifa con `tarifa_auto`. La oferta tiene la misma forma que
la de vida más los campos de auto del contrato v0.42.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pymongo import MongoClient
from pymongo.read_preferences import Secondary

from app.application.cifrado import CifradorCampos
from app.application.identificadores import id_opaco
from app.application.lector_perfiles import NOMBRE_BASE, leer_con_fallback_a_primaria
from app.application.tarifa_auto import TARIFA, cotizar, monto, rango_preliminar
from app.application.tiempo_causal import deserializar_operation_time

logger = logging.getLogger("rating.oferta_auto")

COLECCION = "perfiles_vehiculo"
PRODUCTO = "AUTO"


class LectorVehiculos(Protocol):
    def leer(self, vehiculo_id: str, operation_time: str) -> dict[str, Any] | None: ...


class LectorVehiculosMongo:
    """El último perfil de un vehículo con su resumen descifrado en memoria."""

    def __init__(
        self,
        cliente_primaria: MongoClient,
        cliente_secundaria: MongoClient,
        max_time_ms: int,
        cifrador: CifradorCampos | None,
    ) -> None:
        self._primaria = cliente_primaria[NOMBRE_BASE][COLECCION]
        self._secundaria = cliente_secundaria[NOMBRE_BASE][COLECCION].with_options(
            read_preference=Secondary()
        )
        self._cliente_secundaria = cliente_secundaria
        self._max_time_ms = max_time_ms
        self._cifrador = cifrador

    def leer(self, vehiculo_id: str, operation_time: str) -> dict[str, Any] | None:
        punto_corte = deserializar_operation_time(operation_time)
        filtro = {"vehiculo_id": vehiculo_id}
        orden = [("perfil_version", -1)]

        def _de_secundaria() -> dict[str, Any] | None:
            with self._cliente_secundaria.start_session(causal_consistency=True) as session:
                session.advance_operation_time(punto_corte)
                return self._secundaria.find_one(
                    filtro, sort=orden, session=session, max_time_ms=self._max_time_ms
                )

        documento, de_secundaria = leer_con_fallback_a_primaria(
            _de_secundaria, lambda: self._primaria.find_one(filtro, sort=orden)
        )
        if not de_secundaria:
            logger.warning(
                "perfil de vehículo leído de la primaria vehiculo=%s", id_opaco(vehiculo_id)
            )
        if documento is None:
            return None
        resumen = documento["resumen_cifrado"]
        if isinstance(resumen, str):
            if self._cifrador is None:
                raise ValueError("no hay cifrador para el resumen del vehículo")
            resumen = json.loads(self._cifrador.descifrar(resumen))
        return {
            "vehiculo_id": documento["vehiculo_id"],
            "titular_id": documento["titular_id"],
            "perfil_version": documento["perfil_version"],
            "consentimiento_id": documento.get("consentimiento_id"),
            "resumen": resumen,
        }


def _fuentes(estados: dict[str, str]) -> list[dict[str, Any]]:
    """`Oferta.fuentes` con el mismo vocabulario que las fuentes de vida."""
    return [
        {
            "id": nombre,
            "nombre": nombre,
            "origen": "OPEN_DATA",
            "estado": estado,
            "capturadoEn": None,
        }
        for nombre, estado in estados.items()
    ]


class ServicioOfertaAuto:
    def __init__(self, lector: LectorVehiculos, anio_actual: int | None = None) -> None:
        self._lector = lector
        self._anio_actual = anio_actual

    def generar(
        self,
        vehiculo_id: str,
        operation_time: str,
        placa_enmascarada: str,
        kilometraje_anual: int,
        ciudad: str,
        uso: str,
        deducible: str,
    ) -> dict[str, Any] | None:
        """La oferta, o None si no hay perfil. Lanza las excepciones de `tarifa_auto` cuando el
        vehículo no existe, no es asegurable o RUNT no respondió."""
        perfil = self._lector.leer(vehiculo_id, operation_time)
        if perfil is None:
            return None
        resumen = perfil["resumen"]
        cotizacion = cotizar(
            resumen,
            kilometraje_anual,
            ciudad,
            uso,
            deducible,
            self._anio_actual or datetime.now(UTC).year,
        )
        oferta: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "producto": PRODUCTO,
            "tipo": cotizacion.tipo,
            "versionReglas": TARIFA["version"],
            "cobertura": monto(cotizacion.valor_comercial),
            "desgloseScore": [],
            "fuentes": _fuentes(resumen.get("fuentes", {})),
            "perfilRiesgo": None,
            "vehiculo": {
                "placaEnmascarada": placa_enmascarada,
                "marca": resumen["marca"],
                "linea": resumen["linea"],
                "modelo": int(resumen["modelo"]),
                "clase": resumen["clase"],
                "uso": "COMERCIAL" if resumen.get("servicio") == "PUBLICO" else uso,
                "valorComercial": monto(cotizacion.valor_comercial),
                "ciudadCirculacion": ciudad.strip(),
                "kilometrajeAnual": kilometraje_anual,
                "deducible": deducible,
            },
            "coberturas": cotizacion.coberturas,
            "factoresTarifa": cotizacion.factores,
            # Para UNDER al emitir (tarea 6): demasiados siniestros, revisión manual.
            "requiereRevision": cotizacion.requiere_revision,
            "vigenciaMeses": TARIFA["vigencia_meses"],
        }
        if cotizacion.tipo == "PRELIMINAR":
            minima, maxima = rango_preliminar(cotizacion.prima_mensual)
            oferta["prima"] = None
            oferta["rangoPrima"] = {"minima": monto(minima), "maxima": monto(maxima)}
        else:
            oferta["prima"] = monto(cotizacion.prima_mensual)
            oferta["rangoPrima"] = None
        logger.info(
            "oferta de auto vehiculo=%s version=%s tipo=%s revision=%s",
            id_opaco(vehiculo_id),
            perfil["perfil_version"],
            cotizacion.tipo,
            cotizacion.requiere_revision,
        )
        return oferta
