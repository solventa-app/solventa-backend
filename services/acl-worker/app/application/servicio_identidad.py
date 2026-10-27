"""Caso de uso: verificar la identidad del cliente del onboarding móvil (HU-M01), lado ACL.

1. Descarga las imágenes con las URLs firmadas que manda ONBOARDING (solo de los orígenes
   permitidos: el bucket de archivos, para que nadie use al ACL de proxy) y con tope de tamaño.
2. Busca el resultado en la caché por la huella SHA-256 de las imágenes: un reintento con las
   mismas imágenes no vuelve a cobrarse.
3. Llama al proveedor dentro de su propio circuito, **sin reintento** (cada llamada se cobra y
   el corte es largo: corre en segundo plano, fuera del camino del usuario).
4. Si algo falla, responde `pendiente` con su motivo (nunca un 5xx, regla #4) y ONBOARDING
   reintenta más tarde.

Nada de esto se registra con datos personales: solo la referencia opaca y códigos."""

import logging
from collections.abc import Callable, Sequence

import httpx
import purgatory

from app.application.cache_identidad import CacheIdentidad, huella_imagenes
from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.domain.puertos import (
    ImagenesIdentidad,
    PuertoVerificacionIdentidad,
    ResultadoIdentidad,
)

logger = logging.getLogger("acl_worker.identidad")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_PROVEEDOR_NO_DISPONIBLE = "proveedor_no_disponible"
MOTIVO_IMAGEN_NO_DISPONIBLE = "imagen_no_disponible"
TAMANO_MAXIMO_IMAGEN = 10 * 1024 * 1024


class ImagenNoDisponible(Exception):
    pass


class DescargadorImagenes:
    """Baja una imagen por URL firmada. No manda credenciales: la URL ya lleva su firma."""

    def __init__(
        self,
        origenes_permitidos: Sequence[str],
        timeout_s: float,
        cliente: httpx.Client | None = None,
        tamano_maximo: int = TAMANO_MAXIMO_IMAGEN,
        reescrituras: dict[str, str] | None = None,
    ) -> None:
        self._origenes = tuple(origen for origen in origenes_permitidos if origen)
        # Solo en local: las URLs firmadas apuntan a `localhost:4443` (lo que ve el celular o el
        # navegador) y desde el contenedor del ACL el emulador es `fake-gcs:4443`.
        self._reescrituras = reescrituras or {}
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(follow_redirects=False)
        self._tamano_maximo = tamano_maximo

    def descargar(self, url: str) -> bytes:
        if not url.startswith(self._origenes):
            raise ImagenNoDisponible("origen_no_permitido")
        encabezados = {}
        for origen, destino in self._reescrituras.items():
            if url.startswith(origen):
                # El emulador enruta por el `Host` firmado: se conserva el original.
                encabezados["Host"] = httpx.URL(url).netloc.decode()
                url = destino + url[len(origen) :]
                break
        try:
            with self._cliente.stream(
                "GET", url, headers=encabezados, timeout=self._timeout_s
            ) as respuesta:
                if respuesta.status_code != 200:
                    raise ImagenNoDisponible(f"HTTP {respuesta.status_code}")
                contenido = bytearray()
                for parte in respuesta.iter_bytes():
                    contenido += parte
                    if len(contenido) > self._tamano_maximo:
                        raise ImagenNoDisponible("demasiado_grande")
        except httpx.HTTPError as error:
            raise ImagenNoDisponible(type(error).__name__) from error
        return bytes(contenido)

    def cerrar(self) -> None:
        self._cliente.close()


class ServicioIdentidad:
    def __init__(
        self,
        adaptador: PuertoVerificacionIdentidad,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
        descargar: Callable[[str], bytes],
        cache: CacheIdentidad | None = None,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s
        self._descargar = descargar
        self._cache = cache

    def verificar(
        self, referencia: str, anverso_url: str, rostro_url: str, reverso_url: str | None = None
    ) -> ResultadoIdentidad:
        ref = id_opaco(referencia)
        try:
            imagenes = ImagenesIdentidad(
                anverso=self._descargar(anverso_url),
                rostro=self._descargar(rostro_url),
                reverso=self._descargar(reverso_url) if reverso_url else None,
            )
        except ImagenNoDisponible as error:
            logger.warning("imagen no disponible id=%s causa=%s", ref, error)
            return ResultadoIdentidad(estado="pendiente", motivo=MOTIVO_IMAGEN_NO_DISPONIBLE)

        huella = huella_imagenes(imagenes)
        if self._cache is not None:
            guardado = self._cache.obtener(huella)
            if guardado is not None:
                logger.info("identidad desde caché id=%s estado=%s", ref, guardado.estado)
                return guardado

        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                resultado = self._adaptador.verificar_identidad(
                    referencia, imagenes, timeout_s=self._presupuesto_s
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, identidad pendiente id=%s", self._nombre_circuito, ref
            )
            return ResultadoIdentidad(estado="pendiente", motivo=MOTIVO_CIRCUITO_ABIERTO)
        except Exception as error:
            logger.warning(
                "fallo verificando identidad circuito=%s id=%s error=%s",
                self._nombre_circuito,
                ref,
                type(error).__name__,
            )
            return ResultadoIdentidad(estado="pendiente", motivo=MOTIVO_PROVEEDOR_NO_DISPONIBLE)

        logger.info(
            "identidad verificada id=%s estado=%s motivo=%s",
            ref,
            resultado.estado,
            resultado.motivo or "-",
        )
        if self._cache is not None:
            self._cache.guardar(huella, resultado)
        return resultado
