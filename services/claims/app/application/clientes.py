"""Clientes HTTP de CLAIMS hacia otros servicios privados (síncronos, con `httpx`).

- ALMACENAMIENTO (W04-5): CLAIMS actúa en nombre propio (`X-Actor-Rol: SERVICIO`, `X-Actor-Id:
  claims`) para subir las evidencias de la semilla. Los bytes van directo al bucket por la URL
  firmada; las URLs no se guardan ni se registran. Patrón de
  `services/policy/app/application/cliente_almacenamiento.py` (copiado, sin código compartido).
- POLICY: la póliza de cada siniestro (`GET /propuestas/{id}` para la semilla y `GET
  /polizas/{id}`).
"""

from typing import Any

import httpx

CABECERAS_ACTOR = {"X-Actor-Rol": "SERVICIO", "X-Actor-Id": "claims"}


class ServicioNoDisponible(Exception):
    """El servicio destino no respondió o respondió 5xx."""


class SolicitudRechazada(Exception):
    """El servicio destino rechazó la solicitud (4xx). `codigo` viene de su `detail`."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


def _reescribir(url: str, reglas: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """En local la URL firmada apunta a `localhost:4443`, que desde el contenedor es `fake-gcs`. El
    emulador enruta por el `Host` firmado, así que se conserva el original."""
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


def _pedir(cliente: httpx.Client, metodo: str, ruta: str, **kwargs) -> Any:
    try:
        respuesta = cliente.request(metodo, ruta, **kwargs)
    except httpx.HTTPError as error:
        raise ServicioNoDisponible(type(error).__name__) from error
    if 400 <= respuesta.status_code < 500:
        try:
            codigo = respuesta.json().get("detail") or "RECHAZADA"
        except ValueError:
            codigo = "RECHAZADA"
        raise SolicitudRechazada(str(codigo))
    if respuesta.status_code >= 500:
        raise ServicioNoDisponible(f"HTTP {respuesta.status_code}")
    try:
        return respuesta.json()
    except ValueError as error:
        raise ServicioNoDisponible("RespuestaIlegible") from error


class ClienteAlmacenamiento:
    def __init__(
        self,
        url: str,
        timeout_s: float,
        auth: httpx.Auth | None = None,
        reescritura: list[tuple[str, str]] | None = None,
    ) -> None:
        self._cliente = httpx.Client(
            base_url=url, timeout=timeout_s, auth=auth, headers=CABECERAS_ACTOR
        )
        # Las URLs firmadas no llevan nuestras credenciales: otro cliente, sin auth ni cabeceras.
        self._bucket = httpx.Client(timeout=timeout_s)
        self._reescritura = reescritura or []

    def subir(self, tipo: str, tipo_contenido: str, contenido: bytes, propietario_id: str) -> str:
        """Pide la subida, hace el `PUT` directo al bucket y confirma. Devuelve el id del
        archivo."""
        subida = _pedir(
            self._cliente,
            "POST",
            "/archivos/subidas",
            json={
                "tipo": tipo,
                "tipo_contenido": tipo_contenido,
                "tamano_bytes": len(contenido),
                "propietario_id": propietario_id,
            },
        )
        url, encabezados = _reescribir(subida["url"], self._reescritura)
        try:
            respuesta = self._bucket.request(
                subida["metodo"],
                url,
                content=contenido,
                headers={**subida["encabezados"], **encabezados},
            )
            respuesta.raise_for_status()
        except httpx.HTTPError as error:
            raise ServicioNoDisponible(type(error).__name__) from error
        archivo_id = subida["archivo"]["id"]
        confirmado = _pedir(self._cliente, "POST", f"/archivos/{archivo_id}/confirmar")
        if confirmado.get("estado") != "DISPONIBLE":
            raise SolicitudRechazada(confirmado.get("motivo") or "ARCHIVO_RECHAZADO")
        return archivo_id

    def cerrar(self) -> None:
        self._cliente.close()
        self._bucket.close()


class ClientePolicy:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def poliza_de_propuesta(self, propuesta_id: str) -> str:
        """Id de la póliza emitida de una propuesta (la semilla la necesita para enlazar el
        siniestro)."""
        propuesta = _pedir(self._cliente, "GET", f"/propuestas/{propuesta_id}")
        if not propuesta.get("poliza_id"):
            raise SolicitudRechazada("PROPUESTA_SIN_POLIZA")
        return propuesta["poliza_id"]

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any]:
        return _pedir(self._cliente, "GET", f"/polizas/{poliza_id}")

    def cerrar(self) -> None:
        self._cliente.close()


class ClientePayments:
    """PAYMENTS, escritor único de los pagos: la orden de la indemnización al aprobar (W04-6)."""

    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def ordenar_indemnizacion(
        self, siniestro_id: str, poliza_id: str, monto: str, moneda: str
    ) -> dict[str, Any]:
        """Idempotente por siniestro: pedirla otra vez devuelve la misma orden."""
        return _pedir(
            self._cliente,
            "POST",
            "/indemnizaciones",
            json={
                "siniestro_id": siniestro_id,
                "poliza_id": poliza_id,
                "monto": monto,
                "moneda": moneda,
            },
        )

    def cerrar(self) -> None:
        self._cliente.close()
