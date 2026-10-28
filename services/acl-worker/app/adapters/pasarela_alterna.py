"""Segundo adaptador de la pasarela de pagos (HA-MOD-001, EC031, CA-W05-06).

Habla con un proveedor que expone OTRA API que el primero: otros nombres de campos (`reference`,
`payment_token`), otros estados (`APPROVED`, `DECLINED`, `PENDING`) y otras rutas. Lo único que
hace es traducir hacia y desde el contrato del puerto `PuertoPasarelaPagos`; el Circuit Breaker, los
reintentos, PAYMENTS y el BFF no se enteran. Cambiar de proveedor es elegir este adaptador con
`PROVEEDOR_PASARELA=alterna`: ninguna línea de los servicios del Core cambia ni se vuelve a
desplegar.

Igual que el primero, nunca recibe ni reenvía datos de tarjeta: solo el `token_medio_pago` ya
tokenizado por el proveedor PCI-DSS (regla de arquitectura #6)."""

import httpx

from app.domain.puertos import (
    EstadoCobro,
    EstadoSesionPago,
    MedioPagoTokenizado,
    OrdenCobro,
    ResultadoCobro,
    SesionPago,
    SolicitudSesionPago,
)

ESTADOS: dict[str, EstadoCobro] = {
    "APPROVED": "cobrado",
    "DECLINED": "rechazado",
    "PENDING": "pendiente",
}


ESTADOS_SESION: dict[str, EstadoSesionPago] = {
    "OPEN": "abierta",
    "PROCESSING": "procesando",
    "SUCCEEDED": "cobrada",
    "DECLINED": "rechazada",
    "FAILED": "fallida",
    "EXPIRED": "vencida",
}
TIPOS_MEDIO = {"credit": "CREDITO", "debit": "DEBITO"}


def _traducir_sesion(cuerpo: dict) -> SesionPago:
    estado = ESTADOS_SESION.get(cuerpo.get("status", ""))
    if estado is None:
        return SesionPago(
            estado="procesando",
            sesion_id=cuerpo.get("id"),
            motivo="estado_desconocido_del_proveedor",
        )
    metodo = cuerpo.get("payment_method")
    tipo = TIPOS_MEDIO.get((metodo or {}).get("funding", ""))
    return SesionPago(
        estado=estado,
        sesion_id=cuerpo["id"],
        url_pago=cuerpo.get("checkout_url"),
        vence_en=cuerpo.get("expires_at"),
        motivo=cuerpo.get("decline_code"),
        medio=(
            MedioPagoTokenizado(token=metodo["token"], marca=metodo["brand"], tipo=tipo)
            if estado == "cobrada" and metodo and tipo
            else None
        ),
        referencia=cuerpo.get("transaction_id") if estado == "cobrada" else None,
    )


def _traducir(cuerpo: dict) -> ResultadoCobro:
    estado = ESTADOS.get(cuerpo["status"])
    if estado is None:
        # Un estado que no conocemos no se inventa como cobrado: queda pendiente y se concilia.
        return ResultadoCobro(estado="pendiente", motivo="estado_desconocido_del_proveedor")
    return ResultadoCobro(
        estado=estado,
        referencia=cuerpo.get("transaction_id"),
        motivo=cuerpo.get("decline_code"),
        detalle={"reference": cuerpo.get("reference")},
    )


class AdaptadorPasarelaAlterna:
    """Implementa `PuertoPasarelaPagos` (y la variante con `timeout_s` que usa `ServicioCobro`)."""

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
            "/alterna/v2/charges",
            json={
                "reference": orden.id_intento_cobro,
                "policy": orden.poliza_id,
                "amount": orden.monto,
                "currency": orden.moneda,
                "payment_token": orden.token_medio_pago,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        return _traducir(respuesta.json())

    def consultar(
        self, referencia_comercio: str, timeout_s: float | None = None
    ) -> ResultadoCobro | None:
        """404 → `None` (no es una falla, no abre el circuito); cualquier otro error se propaga
        para el Circuit Breaker."""
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(f"/alterna/v2/charges/{referencia_comercio}", timeout=timeout)
        if respuesta.status_code == 404:
            return None
        respuesta.raise_for_status()
        return _traducir(respuesta.json())

    def crear_sesion_pago(
        self, solicitud: SolicitudSesionPago, timeout_s: float | None = None
    ) -> SesionPago:
        """Un 422 es un rechazo de la solicitud, no una falla del proveedor."""
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/alterna/v2/checkout-sessions",
            json={
                "reference": solicitud.id_intento_cobro,
                "amount": solicitud.monto,
                "currency": solicitud.moneda,
                "description": solicitud.descripcion,
                "return_url": solicitud.url_retorno,
                "locale": solicitud.idioma,
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
        return _traducir_sesion(respuesta.json())

    def consultar_sesion_pago(
        self, sesion_id: str, timeout_s: float | None = None
    ) -> SesionPago | None:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(f"/alterna/v2/checkout-sessions/{sesion_id}", timeout=timeout)
        if respuesta.status_code == 404:
            return None
        respuesta.raise_for_status()
        return _traducir_sesion(respuesta.json())

    def sondear(self) -> None:
        """Sonda de recuperación del circuito: nunca crea un cobro de verdad."""
        respuesta = self._cliente.get("/alterna/v2/status", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
