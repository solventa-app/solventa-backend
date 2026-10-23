"""Clientes HTTP del BFF hacia AUTH, ACL Worker, RISK y RATING.

Cada `Protocol` permite probar el BFF con dobles (ver `tests/fakes.py`) sin
levantar los servicios. Las fallas de red, timeouts y 5xx se convierten en
`ErrorServicio`; quien orquesta decide cómo degradar (regla #4: nunca un 5xx
crudo al cliente). Ningún cuerpo de petición ni de respuesta se loguea."""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.autenticacion_servicios import autenticacion_para
from app.configuracion import Configuracion


class ErrorServicio(Exception):
    """El servicio interno no respondió a tiempo o respondió con error."""


class PropuestaRechazada(Exception):
    """POLICY no aceptó la oferta. `motivo` es un `MotivoErrorAceptacion` del contrato."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class FirmaRechazada(Exception):
    """POLICY no aceptó la firma. `motivo` es un `MotivoErrorFirma` del contrato."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class SolicitudInvalida(Exception):
    """El servicio rechazó la solicitud (4xx): reintentar no la arregla."""


class DatosInsuficientes(Exception):
    """RATING no puede ofertar: falta una fuente obligatoria (CA-W01-05). Reintentar sí
    puede arreglarlo cuando el proveedor se recupere."""


class PuertoAuth(Protocol):
    async def registrar_consentimiento(
        self,
        finalidad: str,
        version_texto: str,
        actor: dict | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
    ) -> dict: ...

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None: ...

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None: ...

    async def iniciar_sesion(self, usuario: str, clave: str) -> dict | None: ...

    async def obtener_usuario(self, usuario_id: str) -> dict | None: ...

    async def listar_usuarios(self, rol: str | None) -> list[dict]: ...

    async def crear_usuario(
        self, usuario: str, clave: str, rol: str, nombre: str | None = None
    ) -> dict: ...

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]: ...

    async def cambiar_rol_usuario(self, usuario_id: str, rol: str) -> dict | None: ...

    async def suspender_usuario(self, usuario_id: str) -> dict | None: ...

    async def actualizar_preferencias(
        self, usuario_id: str, idioma: str, moneda: str
    ) -> dict | None: ...


class PuertoAcl(Protocol):
    async def consultar_fuente(
        self, cliente_id: str, consentimiento_id: str, fuente: str
    ) -> dict: ...

    async def invalidar_cache_cliente(self, cliente_id: str) -> None: ...

    async def tasas_cambio(self) -> dict[str, str]: ...


class PuertoRisk(Protocol):
    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict: ...


class PuertoRating(Protocol):
    async def generar_oferta(
        self,
        cliente_id: str,
        operation_time: str,
        cobertura: str,
        plazo_meses: int,
        direccion_inmueble: str,
        consentimiento_id: str | None = None,
    ) -> dict: ...

    async def obtener_oferta(self, oferta_id: str) -> dict | None: ...


class PuertoPolicy(Protocol):
    async def crear_propuesta(self, oferta_id: str, actor_usuario_id: str | None) -> dict: ...

    async def obtener_propuesta(self, propuesta_id: str) -> dict | None: ...

    async def documento_firma(self, propuesta_id: str) -> dict | None: ...

    async def firmar_propuesta(
        self, propuesta_id: str, autoriza_cobro: bool, version_documento: str, firmante_id: str
    ) -> dict: ...


class PuertoPayments(Protocol):
    async def listar_cobros(
        self,
        poliza_id: str | None,
        estado: str | None,
        pagina: int | None = None,
        tamano: int | None = None,
    ) -> list[dict]: ...

    async def reintentar_cobro(self, cobro_id: str) -> dict | None: ...

    async def crear_cobro(self, orden: dict) -> dict: ...


@dataclass
class Clientes:
    auth: PuertoAuth
    acl: PuertoAcl
    risk: PuertoRisk
    rating: PuertoRating
    payments: PuertoPayments
    policy: PuertoPolicy


async def _pedir(
    cliente: httpx.AsyncClient, metodo: str, ruta: str, cuerpo: dict | None = None
) -> httpx.Response:
    try:
        respuesta = await cliente.request(metodo, ruta, json=cuerpo)
    except httpx.HTTPError as error:
        raise ErrorServicio(type(error).__name__) from error
    if respuesta.status_code >= 500:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta


def _json_o_none(respuesta: httpx.Response) -> dict[str, Any] | None:
    """404 = no existe; cualquier otro 4xx es un error de integración, no del usuario."""
    if respuesta.status_code == 404:
        return None
    if respuesta.is_error:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta.json()


class ClienteAuthHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def registrar_consentimiento(
        self,
        finalidad: str,
        version_texto: str,
        actor: dict | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
    ) -> dict:
        cuerpo_peticion: dict[str, Any] = {
            "finalidad": finalidad,
            "version_texto": version_texto,
            "actor": actor,
        }
        if titular_id is not None:
            cuerpo_peticion["titular_id"] = titular_id
        if tipo_dato is not None:
            cuerpo_peticion["tipo_dato"] = tipo_dato
        if fuente_datos is not None:
            cuerpo_peticion["fuente_datos"] = fuente_datos
        if canal is not None:
            cuerpo_peticion["canal"] = canal
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/consentimientos",
            cuerpo_peticion,
        )
        cuerpo = _json_o_none(respuesta)
        if cuerpo is None:
            raise ErrorServicio("HTTP 404")
        return cuerpo

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None:
        return _json_o_none(
            await _pedir(self._cliente, "GET", f"/consentimientos/{consentimiento_id}")
        )

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None:
        return _json_o_none(
            await _pedir(self._cliente, "POST", f"/consentimientos/{consentimiento_id}/revocar")
        )

    # ---------- Sesiones y usuarios (HU-M02) ----------
    # La clave viaja solo en el cuerpo de `POST /sesiones` y `POST /usuarios`: httpx
    # loguea la URL, nunca el cuerpo.

    async def iniciar_sesion(self, usuario: str, clave: str) -> dict | None:
        """`None` si AUTH rechaza las credenciales (401)."""
        respuesta = await _pedir(
            self._cliente, "POST", "/sesiones", {"usuario": usuario, "clave": clave}
        )
        if respuesta.status_code == 401:
            return None
        return _json_o_error(respuesta)

    async def obtener_usuario(self, usuario_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/usuarios/{usuario_id}"))

    async def listar_usuarios(self, rol: str | None) -> list[dict]:
        ruta = "/usuarios" if rol is None else f"/usuarios?rol={rol}"
        return _json_o_error(await _pedir(self._cliente, "GET", ruta))

    async def crear_usuario(
        self, usuario: str, clave: str, rol: str, nombre: str | None = None
    ) -> dict:
        # El nombre (dato personal) y la clave viajan solo en el cuerpo, nunca en la URL.
        cuerpo = {"usuario": usuario, "clave": clave, "rol": rol, "nombre": nombre}
        return _json_o_error(await _pedir(self._cliente, "POST", "/usuarios", cuerpo))

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]:
        """`{id: nombre}` de los CLIENTE pedidos (dato personal: nunca se loguea). Los ids
        van en el cuerpo, no en la URL; AUTH admite hasta 200 por llamada."""
        respuesta = await _pedir(self._cliente, "POST", "/usuarios/nombres", {"ids": ids})
        return _json_o_error(respuesta)

    async def cambiar_rol_usuario(self, usuario_id: str, rol: str) -> dict | None:
        respuesta = await _pedir(self._cliente, "POST", f"/usuarios/{usuario_id}/rol", {"rol": rol})
        if respuesta.status_code == 404:
            return None
        return _json_o_error(respuesta)

    async def suspender_usuario(self, usuario_id: str) -> dict | None:
        return _json_o_none(
            await _pedir(self._cliente, "POST", f"/usuarios/{usuario_id}/suspender")
        )

    async def actualizar_preferencias(
        self, usuario_id: str, idioma: str, moneda: str
    ) -> dict | None:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            f"/usuarios/{usuario_id}/preferencias",
            {"idioma": idioma, "moneda": moneda},
        )
        if respuesta.status_code == 404:
            return None
        return _json_o_error(respuesta)


def _json_o_error(respuesta: httpx.Response) -> Any:
    """400/409 de AUTH traen un mensaje pensado para el usuario: viaja en `SolicitudInvalida`."""
    if respuesta.status_code in (400, 409):
        raise SolicitudInvalida(respuesta.json().get("detail", "Solicitud inválida."))
    if respuesta.is_error:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta.json()


# Las tasas de referencia cambian poco: se guardan 10 minutos por instancia del BFF.
TTL_TASAS_SEGUNDOS = 600


class ClienteAclHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente
        self._tasas: dict[str, str] | None = None
        self._tasas_vencen = 0.0

    async def tasas_cambio(self) -> dict[str, str]:
        """Cuántos COP vale una unidad de cada moneda (KAN-209), como texto decimal."""
        ahora = time.monotonic()
        if self._tasas is not None and ahora < self._tasas_vencen:
            return self._tasas
        respuesta = await _pedir(self._cliente, "GET", "/tasas-cambio")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        self._tasas = dict(respuesta.json()["cop_por_unidad"])
        self._tasas_vencen = ahora + TTL_TASAS_SEGUNDOS
        return self._tasas

    async def consultar_fuente(self, cliente_id: str, consentimiento_id: str, fuente: str) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/fuentes/consultar",
            {"cliente_id": cliente_id, "consentimiento_id": consentimiento_id, "fuente": fuente},
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def invalidar_cache_cliente(self, cliente_id: str) -> None:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/fuentes/invalidar-cache",
            {"cliente_id": cliente_id},
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")


class ClienteRiskHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict:
        respuesta = await _pedir(
            self._cliente, "POST", "/perfiles", {"cliente_id": cliente_id, "fuentes": fuentes}
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


# Mismo valor que `DETALLE_DATOS_INSUFICIENTES` de services/rating/app/main.py.
DETALLE_DATOS_INSUFICIENTES = "datos_insuficientes"


def _detalle(respuesta: httpx.Response) -> Any:
    try:
        return respuesta.json().get("detail")
    except ValueError:
        return None


class ClienteRatingHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def generar_oferta(
        self,
        cliente_id: str,
        operation_time: str,
        cobertura: str,
        plazo_meses: int,
        direccion_inmueble: str,
        consentimiento_id: str | None = None,
    ) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/ofertas",
            {
                "cliente_id": cliente_id,
                "operation_time": operation_time,
                "cobertura": cobertura,
                "plazo_meses": plazo_meses,
                "direccion_inmueble": direccion_inmueble,
                # Para poder aceptarla después (HU-W03): a qué consentimiento pertenece.
                "consentimiento_id": consentimiento_id,
            },
        )
        if respuesta.status_code == 422 and _detalle(respuesta) == DETALLE_DATOS_INSUFICIENTES:
            raise DatosInsuficientes()
        if respuesta.status_code == 400:
            raise SolicitudInvalida(respuesta.json().get("detail", ""))
        if respuesta.is_error:
            # 404 incluido: RISK acaba de escribir el perfil, así que no encontrarlo
            # es un problema de lectura (réplica), no del usuario.
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def obtener_oferta(self, oferta_id: str) -> dict | None:
        """La oferta guardada para aceptarla; `None` si venció o no existe."""
        return _json_o_none(await _pedir(self._cliente, "GET", f"/ofertas/{oferta_id}"))


class ClientePolicyHttp:
    """Propuestas y pólizas (HU-W03)."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def crear_propuesta(self, oferta_id: str, actor_usuario_id: str | None) -> dict:
        """404 y 422 son rechazos de negocio (`PropuestaRechazada` con el motivo del contrato);
        un 5xx o un 503 (RATING caído) llega como `ErrorServicio`, que se ofrece reintentar."""
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/propuestas",
            {"oferta_id": oferta_id, "actor_usuario_id": actor_usuario_id},
        )
        if respuesta.status_code in (404, 422):
            raise PropuestaRechazada(_detalle(respuesta))
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def obtener_propuesta(self, propuesta_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/propuestas/{propuesta_id}"))

    async def documento_firma(self, propuesta_id: str) -> dict | None:
        """`None` si no existe o no se puede armar el documento (409)."""
        respuesta = await _pedir(
            self._cliente, "GET", f"/propuestas/{propuesta_id}/documento-firma"
        )
        if respuesta.status_code == 409:
            return None
        return _json_o_none(respuesta)

    async def firmar_propuesta(
        self, propuesta_id: str, autoriza_cobro: bool, version_documento: str, firmante_id: str
    ) -> dict:
        """404, 409 y 422 son rechazos de negocio (`FirmaRechazada` con el motivo del contrato); un
        5xx o un 503 (el proveedor no responde) llega como `ErrorServicio`, que se ofrece
        reintentar."""
        respuesta = await _pedir(
            self._cliente,
            "POST",
            f"/propuestas/{propuesta_id}/firma",
            {
                "autoriza_cobro": autoriza_cobro,
                "version_documento": version_documento,
                "firmante_id": firmante_id,
            },
        )
        if respuesta.status_code == 404:
            raise FirmaRechazada("PROPUESTA_NO_FIRMABLE")
        if respuesta.status_code in (409, 422):
            raise FirmaRechazada(str(_detalle(respuesta)))
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


class ClientePaymentsHttp:
    """Cobros para el back-office (KAN-28, CA-W05-01/02)."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def listar_cobros(
        self,
        poliza_id: str | None,
        estado: str | None,
        pagina: int | None = None,
        tamano: int | None = None,
    ) -> list[dict]:
        # Filtros en el CUERPO, nunca en la URL: httpx loguea la URL (regla #6).
        cuerpo: dict[str, object] = {"poliza_id": poliza_id, "estado": estado}
        if pagina is not None:
            cuerpo["pagina"] = pagina
        if tamano is not None:
            cuerpo["tamano"] = tamano
        try:
            respuesta = await self._cliente.post("/cobros/buscar", json=cuerpo)
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def reintentar_cobro(self, cobro_id: str) -> dict | None:
        """`None` si no existe; 409 (p. ej. rechazado) llega como `SolicitudInvalida`
        con el mensaje de PAYMENTS, pensado para el usuario."""
        respuesta = await _pedir(self._cliente, "POST", f"/cobros/{cobro_id}/reintentar")
        if respuesta.status_code == 404:
            return None
        if respuesta.status_code == 409:
            raise SolicitudInvalida(respuesta.json().get("detail", "No se puede reintentar."))
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def crear_cobro(self, orden: dict) -> dict:
        """El token del medio de pago y la póliza van solo en el cuerpo (regla #6).
        409 (mismo intento con otros datos) y 422 (datos inválidos) llegan como
        `SolicitudInvalida`; PAYMENTS no responde 5xx por la pasarela."""
        respuesta = await _pedir(self._cliente, "POST", "/cobros", orden)
        if respuesta.status_code == 409:
            raise SolicitudInvalida(respuesta.json().get("detail", "Cobro en conflicto."))
        if respuesta.status_code == 422:
            raise SolicitudInvalida("Los datos del cobro no son válidos.")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


def construir_clientes(config: Configuracion) -> tuple[Clientes, list[httpx.AsyncClient]]:
    """Devuelve los clientes y los `httpx.AsyncClient` abiertos, para cerrarlos al apagar."""

    def http(url: str, audiencia: str, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=url,
            timeout=timeout,
            auth=autenticacion_para(config.autenticacion_servicios, audiencia),
        )

    auth = http(config.auth_url, "auth", config.servicios_timeout_segundos)
    acl = http(config.acl_url, "acl-worker", config.acl_timeout_segundos)
    risk = http(config.risk_url, "risk", config.servicios_timeout_segundos)
    rating = http(config.rating_url, "rating", config.servicios_timeout_segundos)
    payments = http(config.payments_url, "payments", config.servicios_timeout_segundos)
    policy = http(config.policy_url, "policy", config.servicios_timeout_segundos)
    clientes = Clientes(
        auth=ClienteAuthHttp(auth),
        acl=ClienteAclHttp(acl),
        risk=ClienteRiskHttp(risk),
        rating=ClienteRatingHttp(rating),
        payments=ClientePaymentsHttp(payments),
        policy=ClientePolicyHttp(policy),
    )
    return clientes, [auth, acl, risk, rating, payments, policy]
