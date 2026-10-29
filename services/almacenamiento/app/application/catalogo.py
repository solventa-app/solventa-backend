"""Catálogo de tipos de archivo (KAN-250): qué se puede guardar, quién lo sube y quién lo lee.

La fuente de verdad es `contracts/tipos-archivo.json`; la imagen lleva su copia en
`app/tipos-archivo.json` (`scripts/sincronizar_tipos_archivo.py`, verificado en el CI), igual que
`fuentes.json`. Conectar otra HU = una fila nueva en el contrato, sin tocar el código del servicio.

Un actor se escribe como su rol (`CLIENTE`, `ASESOR`, `BACKOFFICE`) o como `servicio:<nombre>`
cuando llama un servicio interno en nombre propio. El catálogo se valida completo al cargar: un
error en el contrato tumba el arranque (y las pruebas), no la primera subida."""

import json
from dataclasses import dataclass
from pathlib import Path

RUTA_POR_DEFECTO = Path(__file__).resolve().parent.parent / "tipos-archivo.json"
ROLES = ("CLIENTE", "ASESOR", "BACKOFFICE")
PROPIETARIOS = ("usuario", "onboarding", "poliza", "siniestro")
# Firmas conocidas: el tipo de contenido declarado se comprueba con los primeros bytes del objeto.
TIPOS_CONTENIDO = ("image/jpeg", "image/png", "video/mp4", "application/pdf")


class CatalogoInvalido(ValueError):
    """El contrato `tipos-archivo.json` tiene un error."""


@dataclass(frozen=True)
class TipoArchivo:
    tipo: str
    descripcion: str
    activo: bool
    tipos_contenido: tuple[str, ...]
    tamano_max_bytes: int
    quien_sube: tuple[str, ...]
    quien_lee: tuple[str, ...]
    propietario: str
    requiere_consentimiento: bool
    retencion_dias: int | None


def _actor_valido(actor: str) -> bool:
    if actor in ROLES:
        return True
    prefijo, _, nombre = actor.partition(":")
    return prefijo == "servicio" and bool(nombre)


def _validar(fila: dict) -> TipoArchivo:
    tipo = fila.get("tipo", "")
    if not tipo or tipo != tipo.lower() or "/" in tipo:
        raise CatalogoInvalido(f"tipo inválido: {tipo!r} (minúsculas, sin '/')")
    try:
        definido = TipoArchivo(
            tipo=tipo,
            descripcion=str(fila["descripcion"]),
            activo=bool(fila["activo"]),
            tipos_contenido=tuple(fila["tipos_contenido"]),
            tamano_max_bytes=int(fila["tamano_max_bytes"]),
            quien_sube=tuple(fila["quien_sube"]),
            quien_lee=tuple(fila["quien_lee"]),
            propietario=str(fila["propietario"]),
            requiere_consentimiento=bool(fila["requiere_consentimiento"]),
            retencion_dias=fila["retencion_dias"],
        )
    except KeyError as error:
        raise CatalogoInvalido(f"{tipo}: falta el campo {error}") from None
    if not definido.tipos_contenido or not set(definido.tipos_contenido) <= set(TIPOS_CONTENIDO):
        raise CatalogoInvalido(f"{tipo}: tipos_contenido fuera de {TIPOS_CONTENIDO}")
    if definido.tamano_max_bytes <= 0:
        raise CatalogoInvalido(f"{tipo}: tamano_max_bytes debe ser mayor que 0")
    for actor in definido.quien_sube + definido.quien_lee:
        if not _actor_valido(actor):
            raise CatalogoInvalido(f"{tipo}: actor desconocido {actor!r}")
    if not definido.quien_sube or not definido.quien_lee:
        raise CatalogoInvalido(f"{tipo}: quien_sube y quien_lee no pueden estar vacíos")
    if definido.propietario not in PROPIETARIOS:
        raise CatalogoInvalido(f"{tipo}: propietario debe ser uno de {PROPIETARIOS}")
    dias = definido.retencion_dias
    if dias is not None and (not isinstance(dias, int) or isinstance(dias, bool) or dias <= 0):
        raise CatalogoInvalido(f"{tipo}: retencion_dias debe ser un entero > 0 o null")
    return definido


class Catalogo:
    def __init__(self, tipos: list[TipoArchivo]) -> None:
        self._tipos = {t.tipo: t for t in tipos}
        if len(self._tipos) != len(tipos):
            raise CatalogoInvalido("hay tipos repetidos")

    @classmethod
    def desde_json(cls, contenido: dict) -> "Catalogo":
        if contenido.get("version") != 1:
            raise CatalogoInvalido("versión de catálogo no soportada")
        return cls([_validar(fila) for fila in contenido.get("tipos", [])])

    @classmethod
    def desde_archivo(cls, ruta: Path = RUTA_POR_DEFECTO) -> "Catalogo":
        return cls.desde_json(json.loads(ruta.read_text(encoding="utf-8")))

    def obtener(self, tipo: str) -> TipoArchivo | None:
        """El tipo si existe y está activo. Uno inactivo se trata como desconocido al subir."""
        definido = self._tipos.get(tipo)
        return definido if definido is not None and definido.activo else None

    def definicion(self, tipo: str) -> TipoArchivo | None:
        """El tipo aunque esté inactivo: los archivos que ya existen se siguen pudiendo leer."""
        return self._tipos.get(tipo)

    def con_retencion(self) -> list[TipoArchivo]:
        return [t for t in self._tipos.values() if t.retencion_dias is not None]
