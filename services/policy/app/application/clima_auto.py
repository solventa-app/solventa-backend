"""Cobertura paramétrica de clima incluida en toda póliza de auto (HU-M04 + HU-W08).

KAN-259 y KAN-290.

Al emitir una póliza `AUTO`, POLICY le agrega una cobertura `CLIMA` en la celda de la ciudad de
circulación, con los parámetros de `parametrica_clima` en `tarifas-auto.json` (copia del contrato en
`app/tarifas-auto.json`). No cambia la prima: va incluida. Las coordenadas salen de
`app/ciudades.json` (copia de `contracts/ciudades.json`, centro de la ciudad con 2 decimales, que es
la celda con la que CLAIMS cruza el evento).

Una ciudad que no está en el catálogo (el formulario de la web acepta texto libre) se queda sin
cobertura de clima: se registra en el log, sin la ciudad (regla #6), y la emisión sigue.
"""

import json
import logging
import unicodedata
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("policy.clima_auto")

_APP = Path(__file__).resolve().parents[1]


def _cargar(nombre: str) -> dict[str, Any]:
    return json.loads((_APP / nombre).read_text(encoding="utf-8"))


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_tildes.lower().replace("-", " ").split())


PARAMETROS: dict[str, Any] = _cargar("tarifas-auto.json")["parametrica_clima"]
MONEDA: str = _cargar("tarifas-auto.json")["moneda"]
_CIUDADES: dict[str, tuple[float, float]] = {}
for _c in _cargar("ciudades.json")["ciudades"]:
    for _clave in (_c["id"], _c["nombre"]):
        _CIUDADES[_normalizar(_clave)] = (_c["latitud"], _c["longitud"])


def coordenadas(ciudad: str | None) -> tuple[float, float] | None:
    """(latitud, longitud) de la ciudad del catálogo, sin importar tildes ni mayúsculas."""
    if not ciudad:
        return None
    return _CIUDADES.get(_normalizar(ciudad))


class PuertoCoberturas(Protocol):
    def de_poliza(self, poliza_id: str) -> list[dict[str, Any]]: ...

    def crear_clima(
        self,
        poliza_id: str,
        monto_fijo: str,
        moneda: str,
        vigencia_desde: Any,
        vigencia_hasta: Any,
        latitud: float,
        longitud: float,
        variable: str,
        umbral: str,
        ventana_horas: int,
    ) -> dict[str, Any]: ...


def incluir_clima(
    coberturas: PuertoCoberturas, propuesta: dict[str, Any], poliza: dict[str, Any]
) -> dict[str, Any] | None:
    """Crea la cobertura de clima de una póliza AUTO recién emitida. Idempotente: si la póliza ya
    tiene coberturas paramétricas no crea otra. Devuelve la creada, o `None` si no aplica."""
    if (propuesta.get("producto") or "VIDA_DEUDOR") != "AUTO":
        return None
    if coberturas.de_poliza(poliza["id"]):
        return None
    vehiculo = propuesta.get("vehiculo") or {}
    punto = coordenadas(vehiculo.get("ciudadCirculacion"))
    if punto is None:
        logger.warning("póliza de auto sin cobertura de clima: ciudad fuera del catálogo")
        return None
    return coberturas.crear_clima(
        poliza["id"],
        PARAMETROS["monto"],
        MONEDA,
        poliza["vigencia_desde"],
        poliza["vigencia_hasta"],
        latitud=punto[0],
        longitud=punto[1],
        variable=PARAMETROS["variable"],
        umbral=PARAMETROS["umbral"],
        ventana_horas=int(PARAMETROS["ventana_horas"]),
    )


class PuertoCeldas(Protocol):
    def celdas_clima_vigentes(self) -> list[dict[str, Any]]: ...


class PuertoConsultaClima(Protocol):
    def consultar_clima(self, latitud: float, longitud: float, variable: str) -> bool: ...


def consultar_celdas(coberturas: PuertoCeldas, acl: PuertoConsultaClima) -> dict[str, int]:
    """Una consulta al ACL por cada celda de clima con coberturas vigentes (no por póliza). Lo
    dispara Cloud Scheduler; el ACL publica `evento.parametrico.recibido` y CLAIMS liquida."""
    celdas = coberturas.celdas_clima_vigentes()
    publicadas = sum(
        1 for c in celdas if acl.consultar_clima(c["latitud"], c["longitud"], c["variable"])
    )
    return {"celdas": len(celdas), "publicadas": publicadas}
