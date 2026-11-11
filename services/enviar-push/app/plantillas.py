"""Catálogo de plantillas de push (KAN-259): el productor del evento solo elige una clave de
`contracts/plantillas-push.json` (copia sincronizada en `app/contrato/`, ver
`scripts/sincronizar_contrato_push.py`); el título y el cuerpo nunca los escribe él."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("enviar_push.plantillas")

RUTA_CATALOGO = Path(__file__).resolve().parent / "contrato" / "plantillas-push.json"
IDIOMA_POR_DEFECTO = "es"


class PlantillaDesconocida(Exception):
    """El evento pide una plantilla que no está en el catálogo."""


def cargar_catalogo(ruta: Path = RUTA_CATALOGO) -> dict[str, Any]:
    return json.loads(ruta.read_text(encoding="utf-8"))["plantillas"]


def _render(texto: str, variables: dict[str, str]) -> str:
    try:
        return texto.format(**variables)
    except (KeyError, IndexError) as error:
        logger.warning("variable ausente para la plantilla (%s); se deja el texto literal", error)
        return texto


class Plantillas:
    def __init__(self, catalogo: dict[str, Any] | None = None) -> None:
        self._catalogo = catalogo if catalogo is not None else cargar_catalogo()

    def resolver(
        self, clave: str, variables: dict[str, str] | None, idioma: str = IDIOMA_POR_DEFECTO
    ) -> dict[str, Any]:
        """{titulo, cuerpo, ttl_segundos, silenciosa} ya renderizados. Cae a `es` si el idioma
        pedido no está en la plantilla."""
        plantilla = self._catalogo.get(clave)
        if plantilla is None:
            raise PlantillaDesconocida(clave)
        textos = plantilla.get(idioma) or plantilla[IDIOMA_POR_DEFECTO]
        variables = variables or {}
        return {
            "titulo": _render(textos["titulo"], variables),
            "cuerpo": _render(textos["cuerpo"], variables),
            "ttl_segundos": plantilla.get("ttlSegundos", 86400),
            "silenciosa": bool(plantilla.get("silenciosa", False)),
        }
