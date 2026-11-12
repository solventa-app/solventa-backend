"""Rutas de los prestadores hacia el cliente para el mapa de asistencia (HU-M06, v0.51, B8).

La app manda su ubicación y los prestadores que muestra (los 3 destacados primero). CLAIMS busca
sus coordenadas en el catálogo, pide al ACL los tiempos de todos y la geometría de los 3
destacados (Mapbox o simulado) y deja un registro en `auditoria_rutas`: la ubicación exacta y la
dirección aproximada cifradas, nunca en los logs. Si el ACL no responde, la respuesta va vacía (la
app usa tiempos aproximados) y la auditoría registra `FALLA`. Auditar nunca rompe la respuesta.
"""

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.application.cifrado import CifradorCampos

logger = logging.getLogger("claims.rutas")

MAX_PRESTADORES = 10
CON_GEOMETRIA = 3


class MapasAcl(Protocol):
    def rutas(self, pedido: dict) -> dict: ...

    def direccion_aproximada(self, latitud: float, longitud: float) -> str | None: ...


class RepositorioAuditoriaRutas:
    """Colección `auditoria_rutas` (escritor único: CLAIMS)."""

    def __init__(self, cliente, cifrador: CifradorCampos, nombre_db: str = "claims") -> None:
        self._coleccion = cliente[nombre_db]["auditoria_rutas"]
        self._cifrador = cifrador

    def registrar(self, registro: dict) -> None:
        rid = registro["id"]
        direccion = registro.get("direccion_aproximada")
        self._coleccion.insert_one(
            {
                "_id": rid,
                "creada_en": registro["creada_en"],
                "cliente_id": registro["cliente_id"],
                "prestador_ids": registro["prestador_ids"],
                "proveedor": registro["proveedor"],
                "resultado": registro["resultado"],
                "latencia_ms": registro["latencia_ms"],
                "ubicacion_cifrada": self._cifrador.cifrar(
                    json.dumps(registro["ubicacion"]), f"ubicacion:{rid}"
                ),
                "direccion_aproximada_cifrada": (
                    self._cifrador.cifrar(direccion, f"direccion:{rid}") if direccion else None
                ),
            }
        )


class ServicioRutas:
    def __init__(
        self,
        prestador: Callable[[str], dict | None],
        acl: MapasAcl,
        auditoria,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        medir: Callable[[], float] = time.monotonic,
    ) -> None:
        self._prestador = prestador
        self._acl = acl
        self._auditoria = auditoria
        self._reloj = reloj
        self._medir = medir

    def consultar(
        self, cliente_id: str, latitud: float, longitud: float, prestador_ids: list[str]
    ) -> list[dict]:
        ids = list(dict.fromkeys(prestador_ids))[:MAX_PRESTADORES]
        destinos = []
        for pid in ids:
            p = self._prestador(pid)
            if p is not None and p["activo"]:
                destinos.append({"id": pid, "latitud": p["latitud"], "longitud": p["longitud"]})
        if not destinos:
            return []
        inicio = self._medir()
        try:
            respuesta = self._acl.rutas(
                {
                    "latitud": latitud,
                    "longitud": longitud,
                    "destinos": destinos,
                    "con_geometria": [d["id"] for d in destinos[:CON_GEOMETRIA]],
                }
            )
        except Exception as error:
            logger.warning("el ACL no respondió las rutas error=%s", type(error).__name__)
            respuesta = {"proveedor": "simulado", "resultado": "FALLA", "rutas": []}
        latencia_ms = max(0, round((self._medir() - inicio) * 1000))
        self._auditar(cliente_id, latitud, longitud, ids, respuesta, latencia_ms)
        return list(respuesta.get("rutas") or [])

    def _auditar(self, cliente_id, latitud, longitud, ids, respuesta, latencia_ms) -> None:
        try:
            direccion = self._acl.direccion_aproximada(latitud, longitud)
        except Exception:
            direccion = None  # B8: si Google falla, se guarda sin dirección
        try:
            self._auditoria.registrar(
                {
                    "id": str(uuid.uuid4()),
                    "creada_en": self._reloj(),
                    "cliente_id": cliente_id,
                    "prestador_ids": ids,
                    "proveedor": respuesta.get("proveedor", "simulado"),
                    "resultado": respuesta.get("resultado", "FALLA"),
                    "latencia_ms": latencia_ms,
                    "ubicacion": {"latitud": latitud, "longitud": longitud},
                    "direccion_aproximada": direccion,
                }
            )
        except Exception as error:
            logger.warning("no se registró la auditoría de rutas error=%s", type(error).__name__)
