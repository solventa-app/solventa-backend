"""Cliente HTTP hacia ALMACENAMIENTO para la imagen de la firma (HU-W03, W03-24 revisada; plan
principal 1.5).

POLICY actúa en nombre propio (`X-Actor-Rol: SERVICIO`, `X-Actor-Id: policy`): la página del enlace
no tiene sesión, así que no puede pedir la subida ella misma. El tipo `firma-imagen` lo sube y lo
lee solo `servicio:policy`, con propietario `poliza/<propuesta>` y el consentimiento de la
propuesta.

- Los bytes de la subida nunca pasan por POLICY: el navegador hace el `PUT` directo al bucket.
- Al firmar, POLICY confirma el archivo (síncrono), pide una URL de lectura de minutos y baja la
  imagen para validarla y estamparla en el PDF. **Las URLs no se guardan ni se registran.**
- `ArchivoRechazado` es una respuesta del servicio (tipo, tamaño, contenido): la firma se rechaza
con
  un mensaje claro. `AlmacenamientoNoDisponible` es una falla: se ofrece reintentar."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

TIPO_FIRMA = "firma-imagen"
CABECERAS_ACTOR = {"X-Actor-Rol": "SERVICIO", "X-Actor-Id": "policy"}


class AlmacenamientoNoDisponible(Exception):
    """ALMACENAMIENTO o el bucket no respondieron."""


class ArchivoRechazado(Exception):
    """El servicio rechazó la solicitud o el archivo. `codigo` es estable
    (TAMANO_NO_ADMITIDO...)."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


@dataclass
class Subida:
    archivo: dict[str, Any]
    url: str
    metodo: str
    encabezados: dict[str, str]
    vence_en: str


class PuertoAlmacenamiento(Protocol):
    def solicitar_subida(
        self, propuesta_id: str, consentimiento_id: str | None, tipo_contenido: str, tamano: int
    ) -> Subida: ...

    def confirmar(self, archivo_id: str) -> dict[str, Any]: ...

    def bajar(self, archivo_id: str) -> bytes: ...

    def retener(self, archivo_id: str) -> None: ...


def _reescribir(url: str, reglas: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """En local la URL firmada apunta a `localhost:4443`, que desde el contenedor es `fake-gcs`. El
    emulador enruta por el `Host` firmado, así que se conserva el original (como hace el ACL con las
    imágenes de M01)."""
    for desde, hacia in reglas:
        if url.startswith(desde):
            return hacia + url[len(desde) :], {"Host": httpx.URL(url).netloc.decode()}
    return url, {}


def reglas_de_reescritura(texto: str) -> list[tuple[str, str]]:
    """`http://localhost:4443/=http://fake-gcs:4443/,...` -> pares (desde, hacia)."""
    reglas = []
    for par in filter(None, (p.strip() for p in texto.split(","))):
        desde, _, hacia = par.partition("=")
        if desde and hacia:
            reglas.append((desde, hacia))
    return reglas


class ClienteAlmacenamiento:
    def __init__(
        self,
        url: str,
        timeout_s: float,
        auth: httpx.Auth | None = None,
        reescritura: list[tuple[str, str]] | None = None,
        tamano_max_bytes: int = 512_000,
    ) -> None:
        self._cliente = httpx.Client(
            base_url=url, timeout=timeout_s, auth=auth, headers=CABECERAS_ACTOR
        )
        # Las URLs firmadas no llevan nuestras credenciales: otro cliente, sin auth ni cabeceras.
        self._bucket = httpx.Client(timeout=timeout_s)
        self._reescritura = reescritura or []
        self._tamano_max = tamano_max_bytes

    def _pedir(self, metodo: str, ruta: str, **kwargs) -> dict[str, Any]:
        try:
            respuesta = self._cliente.request(metodo, ruta, **kwargs)
        except httpx.HTTPError as error:
            raise AlmacenamientoNoDisponible(type(error).__name__) from error
        if respuesta.status_code in (400, 403, 404, 409, 422):
            try:
                codigo = respuesta.json().get("detail") or "ARCHIVO_RECHAZADO"
            except ValueError:
                codigo = "ARCHIVO_RECHAZADO"
            raise ArchivoRechazado(str(codigo))
        if respuesta.status_code >= 400:
            raise AlmacenamientoNoDisponible(f"HTTP {respuesta.status_code}")
        try:
            return respuesta.json()
        except ValueError as error:
            raise AlmacenamientoNoDisponible("RespuestaIlegible") from error

    def solicitar_subida(
        self, propuesta_id: str, consentimiento_id: str | None, tipo_contenido: str, tamano: int
    ) -> Subida:
        cuerpo = self._pedir(
            "POST",
            "/archivos/subidas",
            json={
                "tipo": TIPO_FIRMA,
                "tipo_contenido": tipo_contenido,
                "tamano_bytes": tamano,
                "propietario_id": propuesta_id,
                "consentimiento_id": consentimiento_id,
            },
        )
        return Subida(
            archivo=cuerpo["archivo"],
            url=cuerpo["url"],
            metodo=cuerpo["metodo"],
            encabezados=cuerpo["encabezados"],
            vence_en=cuerpo["vence_en"],
        )

    def confirmar(self, archivo_id: str) -> dict[str, Any]:
        return self._pedir("POST", f"/archivos/{archivo_id}/confirmar")

    def bajar(self, archivo_id: str) -> bytes:
        """Los bytes de un archivo DISPONIBLE, por una URL firmada de minutos que no se guarda."""
        url, encabezados = _reescribir(
            self._pedir("GET", f"/archivos/{archivo_id}/descarga")["url"], self._reescritura
        )
        try:
            respuesta = self._bucket.get(url, headers=encabezados)
            respuesta.raise_for_status()
        except httpx.HTTPError as error:
            raise AlmacenamientoNoDisponible(type(error).__name__) from error
        if len(respuesta.content) > self._tamano_max:
            raise ArchivoRechazado("TAMANO_NO_ADMITIDO")
        return respuesta.content

    def retener(self, archivo_id: str) -> None:
        """Empieza la retención (1 día para `firma-imagen`): el bucket borra el objeto después."""
        self._pedir("POST", f"/archivos/{archivo_id}/retencion")

    def cerrar(self) -> None:
        self._cliente.close()
        self._bucket.close()
