"""Validación de la imagen de la firma (HU-W03, W03-24).

ALMACENAMIENTO ya validó el tipo real por los primeros bytes y el tamaño (firmado en la URL). POLICY
valida lo que solo se ve abriendo la imagen:

- **Dimensiones:** hasta 1200 × 600 px (y al menos 100 × 40, para que se vea en el PDF).
- **Fondo:** el borde debe ser blanco o transparente, para que la firma se estampe limpia en el
  documento. Se mira el marco exterior de la imagen y se exige que casi todo sea claro o
  transparente (una firma que toca el borde no la invalida).

Nada de la imagen se registra en los logs."""

import io

from PIL import Image, UnidentifiedImageError

ANCHO_MAXIMO, ALTO_MAXIMO = 1200, 600
ANCHO_MINIMO, ALTO_MINIMO = 100, 40
# Un píxel cuenta como fondo si es casi blanco o casi transparente.
UMBRAL_CLARO = 225
UMBRAL_TRANSPARENTE = 32
PROPORCION_FONDO = 0.95
GROSOR_MARCO = 3


class ImagenInvalida(Exception):
    """`motivo`: DIMENSIONES, FONDO o ILEGIBLE (para el mensaje en la página)."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def validar(contenido: bytes) -> tuple[int, int]:
    """Devuelve el ancho y el alto, o lanza `ImagenInvalida`."""
    try:
        with Image.open(io.BytesIO(contenido)) as imagen:
            imagen.load()
            ancho, alto = imagen.size
            rgba = imagen.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImagenInvalida("ILEGIBLE") from error
    if not (ANCHO_MINIMO <= ancho <= ANCHO_MAXIMO and ALTO_MINIMO <= alto <= ALTO_MAXIMO):
        raise ImagenInvalida("DIMENSIONES")
    if _proporcion_de_fondo(rgba) < PROPORCION_FONDO:
        raise ImagenInvalida("FONDO")
    return ancho, alto


def _proporcion_de_fondo(imagen: Image.Image) -> float:
    ancho, alto = imagen.size
    pixeles = imagen.load()
    total = fondo = 0
    for y in range(alto):
        for x in range(ancho):
            en_marco = (
                x < GROSOR_MARCO
                or y < GROSOR_MARCO
                or x >= ancho - GROSOR_MARCO
                or y >= alto - GROSOR_MARCO
            )
            if not en_marco:
                continue
            r, g, b, a = pixeles[x, y]
            total += 1
            if a <= UMBRAL_TRANSPARENTE or min(r, g, b) >= UMBRAL_CLARO:
                fondo += 1
    return fondo / total if total else 0.0
