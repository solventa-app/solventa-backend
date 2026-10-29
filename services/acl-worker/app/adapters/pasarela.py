"""Adaptador de la pasarela de pagos: traducción pura a HTTP contra
`stubs/pasarela` (T-W05-2, lado ACL). Nunca recibe ni reenvía datos de
tarjeta: solo el `token_medio_pago` ya tokenizado por el proveedor PCI-DSS
(regla de arquitectura #6).
"""

import httpx

from app.domain.puertos import (
    MedioPagoTokenizado,
    OrdenCobro,
    ResultadoCobro,
    SesionPago,
    SolicitudSesionPago,
)

ESTADOS_SESION = {
    "ABIERTA": "abierta",
    "PROCESANDO": "procesando",
    "COBRADA": "cobrada",
    "RECHAZADA": "rechazada",
    "FALLIDA": "fallida",
    "VENCIDA": "vencida",
}


def _sesion(cuerpo: dict) -> SesionPago:
    estado = ESTADOS_SESION.get(cuerpo.get("estado", ""))
    medio = cuerpo.get("medio")
    if estado is None:
        # Un estado desconocido no se da por cobrado: queda en proceso y se vuelve a consultar.
        return SesionPago(
            estado="procesando",
            sesion_id=cuerpo.get("sesion_id"),
            motivo="estado_desconocido_del_proveedor",
        )
    return SesionPago(
        estado=estado,
        sesion_id=cuerpo["sesion_id"],
        url_pago=cuerpo.get("url_pago"),
        vence_en=cuerpo.get("vence_en"),
        motivo=cuerpo.get("motivo"),
        medio=(
            MedioPagoTokenizado(token=medio["token"], marca=medio["marca"], tipo=medio["tipo"])
            if estado == "cobrada" and medio
            else None
        ),
        referencia=cuerpo.get("referencia") if estado == "cobrada" else None,
    )


class AdaptadorPasarela:
    """Implementa `PuertoPasarelaPagos`."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/cobros",
            json={
                # Traducción de frontera: la pasarela llama "referencia del comercio" a lo
                # que Solventa llama `id_intento_cobro` en su dominio.
                "referencia_comercio": orden.id_intento_cobro,
                "poliza_id": orden.poliza_id,
                "monto": orden.monto,
                "moneda": orden.moneda,
                "token_medio_pago": orden.token_medio_pago,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoCobro(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            motivo=cuerpo.get("motivo"),
            detalle=cuerpo.get("detalle", {}),
        )

    def consultar(
        self, referencia_comercio: str, timeout_s: float | None = None
    ) -> ResultadoCobro | None:
        """`GET /v1/cobros/{referencia_comercio}`: 404 → `None` (no es una falla, no
        abre el circuito); cualquier otro error se propaga para el Circuit Breaker."""
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(f"/v1/cobros/{referencia_comercio}", timeout=timeout)
        if respuesta.status_code == 404:
            return None
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoCobro(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            motivo=cuerpo.get("motivo"),
            detalle=cuerpo.get("detalle", {}),
        )

    def crear_sesion_pago(
        self, solicitud: SolicitudSesionPago, timeout_s: float | None = None
    ) -> SesionPago:
        """`POST /v1/sesiones-pago`. Un 422 (p. ej. `url_retorno_no_permitida`) es un rechazo de la
        solicitud, no una falla del proveedor: no abre el circuito."""
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/sesiones-pago",
            json={
                "referencia_comercio": solicitud.id_intento_cobro,
                "monto": solicitud.monto,
                "moneda": solicitud.moneda,
                "descripcion": solicitud.descripcion,
                "url_retorno": solicitud.url_retorno,
                "idioma": solicitud.idioma,
            },
            timeout=timeout,
        )
        if respuesta.status_code == 422:
            detalle = respuesta.json().get("detail")
            return SesionPago(
                estado="rechazada",
                motivo=detalle if isinstance(detalle, str) else "solicitud_invalida",
            )
        respuesta.raise_for_status()
        return _sesion(respuesta.json())

    def consultar_sesion_pago(
        self, sesion_id: str, timeout_s: float | None = None
    ) -> SesionPago | None:
        """`GET /v1/sesiones-pago/{id}`: 404 → `None` (no abre el circuito)."""
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(f"/v1/sesiones-pago/{sesion_id}", timeout=timeout)
        if respuesta.status_code == 404:
            return None
        respuesta.raise_for_status()
        return _sesion(respuesta.json())

    def sondear(self) -> None:
        """Llama `GET /v1/estado` (obedece el modo simulado) en vez de ejecutar un
        cobro real: sondear la recuperación del circuito nunca debe crear una
        orden de pago de verdad. Ver `app/application/sonda.py`."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
