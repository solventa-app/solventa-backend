"""Clientes HTTP del BFF hacia AUTH, ACL Worker, RISK, RATING y los demás servicios internos.

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
from app.ticket_avisos import PuertoTickets, TicketsRedis


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


class FlujoRechazado(Exception):
    """POLICY rechazó una operación de firma o pago con un motivo del contrato v0.18 (`detalle`:
    datos para el mensaje, nunca personales, p. ej. `intentos_restantes`)."""

    def __init__(self, motivo: str, **detalle: Any) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.detalle = detalle


class SolicitudInvalida(Exception):
    """El servicio rechazó la solicitud (4xx): reintentar no la arregla."""


class DatosInsuficientes(Exception):
    """RATING no puede ofertar: falta una fuente obligatoria (CA-W01-05). Reintentar sí
    puede arreglarlo cuando el proveedor se recupere."""


class VerificacionPendiente(Exception):
    """AUTH respondió 403 VERIFICACION_PENDIENTE: clave correcta, identidad sin verificar y el
    canal no es la app (HU-M01)."""


class CredencialesSocioInvalidas(Exception):
    """AUTH rechazó el client_id y la clave del socio (HU-W07). Mismo mensaje en todos los casos."""


class PresupuestoAgotado(Exception):
    """El socio agotó sus cotizaciones del entorno (HU-W07, W07-9)."""


class ErrorOnboardingServicio(Exception):
    """ONBOARDING (o AUTH al crear la cuenta) rechazó la solicitud con un código estable."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


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

    async def iniciar_sesion(
        self, usuario: str, clave: str, canal: str | None = None
    ) -> dict | None: ...

    async def renovar_sesion(self, usuario_id: str, expira_en: str) -> dict | None: ...

    async def crear_cuenta_onboarding(self, correo: str, clave: str) -> dict: ...

    async def correo_registrado(self, correo: str) -> bool: ...

    async def obtener_usuario(self, usuario_id: str) -> dict | None: ...

    async def obtener_identidad(self, usuario_id: str) -> dict | None: ...

    async def listar_usuarios(self, rol: str | None) -> list[dict]: ...

    async def crear_usuario(
        self,
        usuario: str,
        clave: str,
        rol: str,
        nombre: str | None = None,
        correo: str | None = None,
        telefono: str | None = None,
        documento_identidad: str | None = None,
    ) -> dict: ...

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]: ...

    async def cambiar_rol_usuario(self, usuario_id: str, rol: str) -> dict | None: ...

    async def suspender_usuario(self, usuario_id: str) -> dict | None: ...

    async def actualizar_preferencias(
        self, usuario_id: str, idioma: str, moneda: str
    ) -> dict | None: ...

    async def actualizar_contacto(
        self, usuario_id: str, correo: str | None, telefono: str | None
    ) -> dict | None: ...


class PuertoAcl(Protocol):
    async def consultar_fuente(
        self, cliente_id: str, consentimiento_id: str, fuente: str
    ) -> dict: ...

    async def invalidar_cache_cliente(self, cliente_id: str) -> None: ...

    async def tasas_cambio(self) -> dict[str, str]: ...

    # HU-W07: el estado de cada circuito (`closed`, `half-opened` u `opened`), para la salud.
    async def circuitos(self) -> dict[str, str]: ...

    async def enviar_notificacion(
        self,
        id_notificacion: str,
        plantilla: str,
        destinatario_id: str,
        datos: dict[str, str],
        correo: str,
    ) -> dict: ...


class PuertoRisk(Protocol):
    async def registrar_perfil(
        self,
        cliente_id: str,
        fuentes: list[dict],
        consentimiento_id: str | None = None,
        consentimiento_vence_en: str | None = None,
    ) -> dict: ...

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None: ...


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
    async def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]: ...

    async def crear_propuesta(
        self,
        oferta_id: str,
        actor_usuario_id: str | None,
        actor_rol: str | None = None,
        contacto: dict | None = None,
        tasa_comision: str | None = None,
    ) -> dict: ...

    async def obtener_propuesta(self, propuesta_id: str) -> dict | None: ...

    async def documento_firma(self, propuesta_id: str) -> dict | None: ...

    async def obtener_poliza(self, poliza_id: str) -> dict | None: ...

    async def generar_documento(self, poliza_id: str, asegurado: str | None) -> bytes | None: ...

    async def reconciliar_cobro(self, propuesta_id: str) -> str | None: ...

    async def firmar_propuesta(
        self, propuesta_id: str, autoriza_cobro: bool, version_documento: str, firmante_id: str
    ) -> dict: ...

    # v0.18 (HU-W03, F-B6). Las de la página del enlace llevan `acceso`: el token del enlace o el
    # titular de la sesión (ver `Acceso`). Lanzan `FlujoRechazado` con el motivo del contrato.
    async def listar_propuestas(
        self,
        cliente_id: str | None,
        actor_usuario_id: str | None,
        estado_visible: str | None,
        pagina: int,
        tamano: int,
        actor_rol: str | None = None,
    ) -> dict: ...

    async def enviar_codigo(self, propuesta_id: str, correo: str | None) -> dict: ...

    async def verificar_codigo(self, propuesta_id: str, codigo: str) -> dict: ...

    async def emitir(self, propuesta_id: str, rol: str, titular_id: str | None) -> dict: ...

    async def reenviar_enlace(self, propuesta_id: str) -> dict: ...

    # HU-W07: lo que vendió un socio, por mes.
    async def ventas_socio(self, socio_id: str, desde: str | None, hasta: str | None) -> dict: ...

    async def estado_enlace(self, propuesta_id: str, acceso: "Acceso") -> dict: ...

    async def contrato(self, propuesta_id: str, acceso: "Acceso") -> dict: ...

    async def contrato_pdf(self, propuesta_id: str, acceso: "Acceso") -> bytes | None: ...

    async def subida_firma(
        self, propuesta_id: str, acceso: "Acceso", tipo_contenido: str, tamano_bytes: int
    ) -> dict: ...

    async def firmar_enlace(self, propuesta_id: str, acceso: "Acceso", entrada: dict) -> dict: ...

    async def iniciar_pago(
        self, propuesta_id: str, acceso: "Acceso", url_retorno: str, idioma: str
    ) -> dict: ...

    async def confirmar_pago(self, propuesta_id: str, acceso: "Acceso", sesion_id: str) -> dict: ...

    async def resolver_enlace(self, token: str) -> dict | None: ...

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None: ...


class PuertoAlmacenamiento(Protocol):
    """KAN-250. Cada llamada lleva el actor (rol e id del token); ALMACENAMIENTO decide con el
    catálogo de tipos quién sube y quién lee. Un rechazo llega como `ArchivoRechazado`."""

    async def solicitar_subida(self, rol: str, actor_id: str, solicitud: dict) -> dict: ...

    async def confirmar(self, rol: str, actor_id: str, archivo_id: str) -> dict: ...

    async def obtener(self, rol: str, actor_id: str, archivo_id: str) -> dict | None: ...

    async def descarga(self, rol: str, actor_id: str, archivo_id: str) -> dict: ...


class PuertoOnboarding(Protocol):
    """HU-M01. `token` es el del recorrido (`X-Onboarding-Token`); un rechazo llega como
    `ErrorOnboardingServicio` con el código del servicio."""

    async def iniciar(self, jurisdiccion: str, tipo_documento: str) -> dict: ...

    async def consultar(self, onboarding_id: str, token: str) -> dict: ...

    async def registrar_consentimiento(
        self, onboarding_id: str, token: str, consentimiento_id: str
    ) -> dict: ...

    async def solicitar_subida(
        self, onboarding_id: str, token: str, paso: str, tipo_contenido: str, tamano: int
    ) -> dict: ...

    async def confirmar_subida(self, onboarding_id: str, token: str, archivo_id: str) -> dict: ...

    async def vincular_cuenta(self, onboarding_id: str, token: str, usuario_id: str) -> dict: ...

    async def enviar(self, onboarding_id: str, token: str) -> dict: ...

    async def reintentar(self, onboarding_id: str, token: str) -> dict: ...

    async def de_usuario(self, usuario_id: str) -> dict | None: ...


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


class ResolucionRechazada(Exception):
    """CLAIMS no pudo resolver el siniestro. `mensaje` está en lenguaje del operador: es un error
    de negocio (409) o una dependencia caída con reintento (503); se muestra tal cual."""

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje


class PuertoClaims(Protocol):
    async def listar(self, estado: str | None) -> list[dict]: ...

    async def obtener(self, siniestro_id: str) -> dict | None: ...

    async def peritos(self, siniestro_id: str) -> list[dict]: ...

    async def aprobar(self, siniestro_id: str, actor_usuario_id: str) -> dict: ...

    async def rechazar(
        self, siniestro_id: str, motivo: str, comentario: str | None, actor_usuario_id: str
    ) -> dict: ...

    async def derivar(
        self, siniestro_id: str, perito_id: str, instrucciones: str | None, actor_usuario_id: str
    ) -> dict: ...


class PuertoSocios(Protocol):
    """Socios de distribución en AUTH (HU-W07). Todos lanzan `ErrorServicio` si AUTH no responde."""

    async def emitir_token(self, client_id: str, clave: str) -> dict: ...

    async def jwks(self) -> dict: ...

    async def obtener(self, socio_id: str) -> dict | None: ...

    async def nombres(self, ids: list[str]) -> dict[str, str]: ...

    async def credenciales(self, socio_id: str) -> list[dict]: ...

    async def rotar(self, socio_id: str, entorno: str) -> dict: ...

    async def reservar(self, socio_id: str, entorno: str) -> int: ...

    async def devolver(self, socio_id: str, entorno: str) -> int: ...

    async def registrar_consumo(
        self, socio_id: str, entorno: str, api: str, resultado: str, latencia_ms: int
    ) -> None: ...

    async def consumo(
        self, socio_id: str, entorno: str, desde: str | None, hasta: str | None
    ) -> dict: ...


@dataclass(frozen=True)
class Acceso:
    """Cómo se autoriza una operación de la página del enlace (W03-33): el token del enlace o el
    titular de la sesión de un CLIENTE (que el BFF toma de AUTH, nunca del cliente)."""

    enlace_token: str | None = None
    titular_id: str | None = None

    def cabeceras(self) -> dict[str, str]:
        if self.enlace_token:
            return {"X-Enlace-Firma": self.enlace_token}
        return {"X-Titular-Id": self.titular_id} if self.titular_id else {}


@dataclass
class Clientes:
    auth: PuertoAuth
    acl: PuertoAcl
    risk: PuertoRisk
    rating: PuertoRating
    payments: PuertoPayments
    policy: PuertoPolicy
    almacenamiento: PuertoAlmacenamiento
    onboarding: PuertoOnboarding
    # Tickets del stream de avisos (Redis). `None` si no hay Redis: `ticketAvisos` da error.
    tickets: PuertoTickets | None = None
    claims: PuertoClaims | None = None
    socios: PuertoSocios | None = None


async def _pedir(
    cliente: httpx.AsyncClient,
    metodo: str,
    ruta: str,
    cuerpo: dict | None = None,
    cabeceras: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    try:
        respuesta = await cliente.request(
            metodo, ruta, json=cuerpo, headers=cabeceras, params=params
        )
    except httpx.HTTPError as error:
        raise ErrorServicio(type(error).__name__) from error
    if respuesta.status_code >= 500:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta


def _cuerpo_o_vacio(respuesta: httpx.Response) -> dict[str, Any]:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return {}
    return dict(cuerpo) if isinstance(cuerpo, dict) else {}


def _json_o_none(respuesta: httpx.Response) -> dict[str, Any] | None:
    """404 = no existe; cualquier otro 4xx es un error de integración, no del usuario."""
    if respuesta.status_code == 404:
        return None
    if respuesta.is_error:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta.json()


class ClienteSociosHttp:
    """Los endpoints de socios de AUTH (HU-W07); comparte el `httpx.AsyncClient` de AUTH. Nunca se
    loguea la clave ni el token (httpx registra solo la URL)."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def emitir_token(self, client_id: str, clave: str) -> dict:
        respuesta = await _pedir(
            self._cliente, "POST", "/socios/tokens", {"client_id": client_id, "clave": clave}
        )
        if respuesta.status_code == 401:
            raise CredencialesSocioInvalidas()
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def jwks(self) -> dict:
        respuesta = await _pedir(self._cliente, "GET", "/.well-known/jwks.json")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def obtener(self, socio_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/socios/{socio_id}"))

    async def nombres(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        respuesta = await _pedir(self._cliente, "POST", "/socios/nombres", {"ids": ids})
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def credenciales(self, socio_id: str) -> list[dict]:
        respuesta = await _pedir(self._cliente, "GET", f"/socios/{socio_id}/credenciales")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def rotar(self, socio_id: str, entorno: str) -> dict:
        ruta = f"/socios/{socio_id}/credenciales/{entorno}/rotar"
        respuesta = await _pedir(self._cliente, "POST", ruta)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def reservar(self, socio_id: str, entorno: str) -> int:
        ruta = f"/socios/{socio_id}/presupuesto/{entorno}/cotizaciones/reservar"
        respuesta = await _pedir(self._cliente, "POST", ruta)
        if respuesta.status_code == 429:
            raise PresupuestoAgotado()
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()["restantes"]

    async def devolver(self, socio_id: str, entorno: str) -> int:
        ruta = f"/socios/{socio_id}/presupuesto/{entorno}/cotizaciones/devolver"
        respuesta = await _pedir(self._cliente, "POST", ruta)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()["restantes"]

    async def registrar_consumo(
        self, socio_id: str, entorno: str, api: str, resultado: str, latencia_ms: int
    ) -> None:
        cuerpo = {
            "entorno": entorno,
            "api": api,
            "resultado": resultado,
            "latencia_ms": latencia_ms,
        }
        respuesta = await _pedir(self._cliente, "POST", f"/socios/{socio_id}/consumos", cuerpo)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")

    async def consumo(
        self, socio_id: str, entorno: str, desde: str | None, hasta: str | None
    ) -> dict:
        params = {"entorno": entorno, "desde": desde, "hasta": hasta}
        respuesta = await _pedir(
            self._cliente,
            "GET",
            f"/socios/{socio_id}/consumo",
            params={k: v for k, v in params.items() if v is not None},
        )
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

    async def iniciar_sesion(
        self, usuario: str, clave: str, canal: str | None = None
    ) -> dict | None:
        """`None` si AUTH rechaza las credenciales (401); `VerificacionPendiente` con el 403 de
        una cuenta del onboarding fuera de la app (HU-M01)."""
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/sesiones",
            {"usuario": usuario, "clave": clave, "canal": canal},
        )
        if respuesta.status_code == 401:
            return None
        if respuesta.status_code == 403:
            raise VerificacionPendiente()
        return _json_o_error(respuesta)

    async def renovar_sesion(self, usuario_id: str, expira_en: str) -> dict | None:
        """`actualizarSesion` (HU-M01). `None` si la cuenta ya no puede tener sesión (401)."""
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/sesiones/renovar",
            {"usuario_id": usuario_id, "expira_en": expira_en},
        )
        if respuesta.status_code == 401:
            return None
        return _json_o_error(respuesta)

    async def correo_registrado(self, correo: str) -> bool:
        """¿Ya hay una cuenta con ese correo? El correo va solo en el cuerpo."""
        respuesta = await _pedir(
            self._cliente, "POST", "/usuarios/onboarding/correo-registrado", {"correo": correo}
        )
        if respuesta.status_code in (400, 422):
            raise ErrorOnboardingServicio("DATO_INVALIDO")
        return bool(_json_o_error(respuesta)["registrado"])

    async def crear_cuenta_onboarding(self, correo: str, clave: str) -> dict:
        """CLIENTE en PENDIENTE_VERIFICACION (HU-M01). Correo y clave van solo en el cuerpo."""
        respuesta = await _pedir(
            self._cliente, "POST", "/usuarios/onboarding", {"correo": correo, "clave": clave}
        )
        if respuesta.status_code == 409:
            raise ErrorOnboardingServicio("CORREO_YA_REGISTRADO")
        if respuesta.status_code in (400, 422):
            raise ErrorOnboardingServicio("DATO_INVALIDO")
        return _json_o_error(respuesta)

    async def obtener_identidad(self, usuario_id: str) -> dict | None:
        """`titular_id` y documento en claro del CLIENTE verificado, o `None` si no hay. Dato
        personal: se usa en memoria y no se loguea."""
        ruta = f"/usuarios/{usuario_id}/identidad"
        return _json_o_none(await _pedir(self._cliente, "GET", ruta))

    async def obtener_usuario(self, usuario_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/usuarios/{usuario_id}"))

    async def listar_usuarios(self, rol: str | None) -> list[dict]:
        ruta = "/usuarios" if rol is None else f"/usuarios?rol={rol}"
        return _json_o_error(await _pedir(self._cliente, "GET", ruta))

    async def crear_usuario(
        self,
        usuario: str,
        clave: str,
        rol: str,
        nombre: str | None = None,
        correo: str | None = None,
        telefono: str | None = None,
        documento_identidad: str | None = None,
    ) -> dict:
        # El nombre, el contacto, el documento (datos personales) y la clave viajan solo en el
        # cuerpo, nunca en la URL, y no se registran. AUTH calcula el titular (W03-27).
        cuerpo = {
            "usuario": usuario,
            "clave": clave,
            "rol": rol,
            "nombre": nombre,
            "correo": correo,
            "telefono": telefono,
            "documento_identidad": documento_identidad,
        }
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

    async def actualizar_contacto(
        self, usuario_id: str, correo: str | None, telefono: str | None
    ) -> dict | None:
        """Correo y teléfono del propio cliente (dato personal: solo en el cuerpo, nunca se loguea).
        AUTH responde el usuario con `tiene_contacto`, no el contacto."""
        respuesta = await _pedir(
            self._cliente,
            "POST",
            f"/usuarios/{usuario_id}/contacto",
            {"correo": correo, "telefono": telefono},
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

    async def circuitos(self) -> dict[str, str]:
        respuesta = await _pedir(self._cliente, "GET", "/circuitos")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

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

    async def enviar_notificacion(
        self,
        id_notificacion: str,
        plantilla: str,
        destinatario_id: str,
        datos: dict[str, str],
        correo: str,
    ) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/notificaciones/enviar",
            {
                "id_notificacion": id_notificacion,
                "plantilla": plantilla,
                "destinatario_id": destinatario_id,
                "datos": datos,
                "correo": correo,
            },
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


class ClienteRiskHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def registrar_perfil(
        self,
        cliente_id: str,
        fuentes: list[dict],
        consentimiento_id: str | None = None,
        consentimiento_vence_en: str | None = None,
    ) -> dict:
        cuerpo = {
            "cliente_id": cliente_id,
            "fuentes": fuentes,
            "consentimiento_id": consentimiento_id,
            "consentimiento_vence_en": consentimiento_vence_en,
        }
        respuesta = await _pedir(self._cliente, "POST", "/perfiles", cuerpo)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None:
        cuerpo = {"titular_id": titular_id, "consentimiento_id": consentimiento_id}
        respuesta = await _pedir(
            self._cliente, "POST", "/perfiles/consentimiento-revocado", cuerpo
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")


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

    async def crear_propuesta(
        self,
        oferta_id: str,
        actor_usuario_id: str | None,
        actor_rol: str | None = None,
        contacto: dict | None = None,
        tasa_comision: str | None = None,
    ) -> dict:
        """`contacto` (nombre y correo) lo manda el ASESOR o el SOCIO y POLICY lo guarda cifrado;
        `tasa_comision`, solo el SOCIO (HU-W07), y queda congelada en la propuesta.
        404 y 422 son rechazos de negocio (`PropuestaRechazada` con el motivo del contrato);
        un 5xx o un 503 (RATING caído) llega como `ErrorServicio`, que se ofrece reintentar."""
        cuerpo = {
            "oferta_id": oferta_id,
            "actor_usuario_id": actor_usuario_id,
            "actor_rol": actor_rol,
            "contacto": contacto,
        }
        if tasa_comision is not None:
            cuerpo["tasa_comision"] = tasa_comision
        respuesta = await _pedir(self._cliente, "POST", "/propuestas", cuerpo)
        if respuesta.status_code in (404, 422):
            raise PropuestaRechazada(_detalle(respuesta))
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def obtener_propuesta(self, propuesta_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/propuestas/{propuesta_id}"))

    async def obtener_poliza(self, poliza_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/polizas/{poliza_id}"))

    async def generar_documento(self, poliza_id: str, asegurado: str | None) -> bytes | None:
        """El PDF de la póliza, o `None` si no existe. El nombre del asegurado va en el CUERPO:
        las URL quedan en los logs de acceso y un nombre es un dato personal (regla #6)."""
        respuesta = await _pedir(
            self._cliente, "POST", f"/polizas/{poliza_id}/documento", {"asegurado": asegurado}
        )
        if respuesta.status_code == 404:
            return None
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.content

    async def reconciliar_cobro(self, propuesta_id: str) -> str | None:
        """Le pide a POLICY que revise el cobro de esa propuesta y, si ya se cobró, emita la póliza
        (decisión W03-14). Devuelve el estado de la propuesta, o `None` si el id no es de una
        propuesta (un cobro que no es de W03). Un 5xx o un 503 llega como `ErrorServicio`."""
        respuesta = await _pedir(
            self._cliente, "POST", f"/propuestas/{propuesta_id}/_reconciliar-cobro"
        )
        if respuesta.status_code == 404:
            return None
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()["estado"]

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

    # ---------- v0.18 (HU-W03, F-B6) ----------

    async def _flujo(
        self,
        metodo: str,
        ruta: str,
        cuerpo: dict | None = None,
        acceso: Acceso | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """Una operación del flujo de firma y pago. Un 4xx es un rechazo de negocio
        (`FlujoRechazado` con el motivo de `detail` y sus datos); un 5xx o la red caída llegan como
        `ErrorServicio`, que el llamador convierte en un error con reintento."""
        respuesta = await _pedir(
            self._cliente,
            metodo,
            ruta,
            cuerpo,
            acceso.cabeceras() if acceso else None,
            params,
        )
        if respuesta.is_error:
            cuerpo_error = _cuerpo_o_vacio(respuesta)
            detalle = cuerpo_error.pop("detail", None)
            motivo = detalle if isinstance(detalle, str) else "SOLICITUD_INVALIDA"
            raise FlujoRechazado(motivo, **cuerpo_error)
        return respuesta.json()

    async def listar_propuestas(
        self,
        cliente_id: str | None,
        actor_usuario_id: str | None,
        estado_visible: str | None,
        pagina: int,
        tamano: int,
        actor_rol: str | None = None,
    ) -> dict:
        params = {
            "cliente_id": cliente_id,
            "actor_usuario_id": actor_usuario_id,
            "actor_rol": actor_rol,
            "estado_visible": estado_visible,
            "pagina": pagina,
            "tamano": tamano,
        }
        return await self._flujo(
            "GET", "/propuestas", params={k: v for k, v in params.items() if v is not None}
        )

    async def enviar_codigo(self, propuesta_id: str, correo: str | None) -> dict:
        return await self._flujo("POST", f"/propuestas/{propuesta_id}/codigo", {"correo": correo})

    async def ventas_socio(self, socio_id: str, desde: str | None, hasta: str | None) -> dict:
        params = {k: v for k, v in {"desde": desde, "hasta": hasta}.items() if v is not None}
        respuesta = await _pedir(self._cliente, "GET", f"/socios/{socio_id}/ventas", params=params)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def verificar_codigo(self, propuesta_id: str, codigo: str) -> dict:
        ruta = f"/propuestas/{propuesta_id}/codigo/verificar"
        return await self._flujo("POST", ruta, {"codigo": codigo})

    async def emitir(self, propuesta_id: str, rol: str, titular_id: str | None) -> dict:
        cuerpo = {"rol": rol, "titular_id": titular_id}
        return await self._flujo("POST", f"/propuestas/{propuesta_id}/emitir", cuerpo)

    async def reenviar_enlace(self, propuesta_id: str) -> dict:
        return await self._flujo("POST", f"/propuestas/{propuesta_id}/enlace")

    async def resolver_enlace(self, token: str) -> dict | None:
        """El token va en el cuerpo, nunca en la URL (regla 6). `None` si no existe."""
        try:
            return await self._flujo("POST", "/enlaces/resolver", {"token": token})
        except FlujoRechazado as rechazo:
            if rechazo.motivo == "PROPUESTA_NO_ENCONTRADA":
                return None
            raise

    async def estado_enlace(self, propuesta_id: str, acceso: Acceso) -> dict:
        return await self._flujo("GET", f"/propuestas/{propuesta_id}/enlace-estado", None, acceso)

    async def contrato(self, propuesta_id: str, acceso: Acceso) -> dict:
        return await self._flujo("GET", f"/propuestas/{propuesta_id}/contrato", None, acceso)

    async def contrato_pdf(self, propuesta_id: str, acceso: Acceso) -> bytes | None:
        """El contrato a firmar en PDF; `None` si la propuesta no es de quien lo pide (404)."""
        respuesta = await _pedir(
            self._cliente,
            "GET",
            f"/propuestas/{propuesta_id}/contrato.pdf",
            cabeceras=acceso.cabeceras(),
        )
        if respuesta.status_code == 404:
            return None
        if respuesta.is_error:
            raise FlujoRechazado(
                str(_cuerpo_o_vacio(respuesta).get("detail", "SOLICITUD_INVALIDA"))
            )
        return respuesta.content

    async def subida_firma(
        self, propuesta_id: str, acceso: Acceso, tipo_contenido: str, tamano_bytes: int
    ) -> dict:
        cuerpo = {"tipo_contenido": tipo_contenido, "tamano_bytes": tamano_bytes}
        return await self._flujo("POST", f"/propuestas/{propuesta_id}/firma-imagen", cuerpo, acceso)

    async def firmar_enlace(self, propuesta_id: str, acceso: Acceso, entrada: dict) -> dict:
        return await self._flujo(
            "POST", f"/propuestas/{propuesta_id}/firma-enlace", entrada, acceso
        )

    async def iniciar_pago(
        self, propuesta_id: str, acceso: Acceso, url_retorno: str, idioma: str
    ) -> dict:
        cuerpo = {"url_retorno": url_retorno, "idioma": idioma}
        return await self._flujo("POST", f"/propuestas/{propuesta_id}/pago", cuerpo, acceso)

    async def confirmar_pago(self, propuesta_id: str, acceso: Acceso, sesion_id: str) -> dict:
        ruta = f"/propuestas/{propuesta_id}/pago/{sesion_id}/confirmar"
        return await self._flujo("POST", ruta, None, acceso)

    async def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]:
        """`{propuesta_id: nombre}` (dato personal: nunca se loguea), hasta 200 por llamada."""
        respuesta = await _pedir(
            self._cliente, "POST", "/propuestas/nombres-titulares", {"ids": propuesta_ids}
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None:
        cuerpo = {"titular_id": titular_id, "consentimiento_id": consentimiento_id}
        respuesta = await _pedir(
            self._cliente, "POST", "/propuestas/consentimiento-revocado", cuerpo
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")


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


class ArchivoRechazado(Exception):
    """ALMACENAMIENTO rechazó la operación; `codigo` es el de su respuesta."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


class ClienteAlmacenamientoHttp:
    """Archivos (KAN-250). Los bytes no pasan por aquí: solo URLs firmadas y metadatos."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def _pedir(
        self, metodo: str, ruta: str, rol: str, actor_id: str, cuerpo: dict | None = None
    ) -> httpx.Response:
        try:
            respuesta = await self._cliente.request(
                metodo,
                ruta,
                json=cuerpo,
                headers={"X-Actor-Rol": rol, "X-Actor-Id": actor_id},
            )
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.status_code >= 500:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        if respuesta.is_error:
            raise ArchivoRechazado(str(respuesta.json().get("detail", "SOLICITUD_INVALIDA")))
        return respuesta

    async def solicitar_subida(self, rol: str, actor_id: str, solicitud: dict) -> dict:
        return (await self._pedir("POST", "/archivos/subidas", rol, actor_id, solicitud)).json()

    async def confirmar(self, rol: str, actor_id: str, archivo_id: str) -> dict:
        ruta = f"/archivos/{archivo_id}/confirmar"
        return (await self._pedir("POST", ruta, rol, actor_id)).json()

    async def obtener(self, rol: str, actor_id: str, archivo_id: str) -> dict | None:
        try:
            return (await self._pedir("GET", f"/archivos/{archivo_id}", rol, actor_id)).json()
        except ArchivoRechazado as error:
            if error.codigo in ("ARCHIVO_NO_ENCONTRADO", "NO_AUTORIZADO"):
                return None
            raise

    async def descarga(self, rol: str, actor_id: str, archivo_id: str) -> dict:
        ruta = f"/archivos/{archivo_id}/descarga"
        return (await self._pedir("GET", ruta, rol, actor_id)).json()


class ClienteOnboardingHttp:
    """Onboarding del móvil (HU-M01). El token del recorrido viaja en `X-Onboarding-Token`; nunca
    se loguea (httpx registra solo la URL)."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def _pedir(
        self,
        metodo: str,
        ruta: str,
        token: str | None = None,
        cuerpo: dict | None = None,
    ) -> dict:
        try:
            respuesta = await self._cliente.request(
                metodo,
                ruta,
                json=cuerpo,
                headers={"X-Onboarding-Token": token} if token else None,
            )
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.status_code >= 500:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        if respuesta.is_error:
            try:
                codigo = respuesta.json().get("detail")
            except (ValueError, AttributeError):
                codigo = None
            # El 422 de validación de FastAPI trae una lista, no un código.
            raise ErrorOnboardingServicio(codigo if isinstance(codigo, str) else "DATO_INVALIDO")
        return respuesta.json()

    async def iniciar(self, jurisdiccion: str, tipo_documento: str) -> dict:
        cuerpo = {"jurisdiccion": jurisdiccion, "tipo_documento": tipo_documento}
        return await self._pedir("POST", "/onboarding", cuerpo=cuerpo)

    async def consultar(self, onboarding_id: str, token: str) -> dict:
        return await self._pedir("GET", f"/onboarding/{onboarding_id}", token)

    async def registrar_consentimiento(
        self, onboarding_id: str, token: str, consentimiento_id: str
    ) -> dict:
        ruta = f"/onboarding/{onboarding_id}/consentimiento"
        return await self._pedir("POST", ruta, token, {"consentimiento_id": consentimiento_id})

    async def solicitar_subida(
        self, onboarding_id: str, token: str, paso: str, tipo_contenido: str, tamano: int
    ) -> dict:
        cuerpo = {"paso": paso, "tipo_contenido": tipo_contenido, "tamano_bytes": tamano}
        return await self._pedir("POST", f"/onboarding/{onboarding_id}/subidas", token, cuerpo)

    async def confirmar_subida(self, onboarding_id: str, token: str, archivo_id: str) -> dict:
        ruta = f"/onboarding/{onboarding_id}/subidas/{archivo_id}/confirmar"
        return await self._pedir("POST", ruta, token)

    async def vincular_cuenta(self, onboarding_id: str, token: str, usuario_id: str) -> dict:
        ruta = f"/onboarding/{onboarding_id}/cuenta"
        return await self._pedir("POST", ruta, token, {"usuario_id": usuario_id})

    async def enviar(self, onboarding_id: str, token: str) -> dict:
        return await self._pedir("POST", f"/onboarding/{onboarding_id}/enviar", token)

    async def reintentar(self, onboarding_id: str, token: str) -> dict:
        return await self._pedir("POST", f"/onboarding/{onboarding_id}/reintentar", token)

    async def de_usuario(self, usuario_id: str) -> dict | None:
        try:
            return await self._pedir("GET", f"/usuarios/{usuario_id}/onboarding")
        except ErrorOnboardingServicio as error:
            if error.codigo == "SESION_NO_ENCONTRADA":
                return None
            raise


MENSAJE_DATOS_INVALIDOS = "Revisa los datos enviados e intenta de nuevo."
MENSAJE_RESOLUCION = "No pudimos completar la acción sobre el siniestro. Intenta de nuevo."


class ClienteClaimsHttp:
    """Mesa de siniestros (HU-W04). La autorización (solo BACKOFFICE) la hace el BFF antes de
    llamar; CLAIMS es privado."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def listar(self, estado: str | None) -> list[dict]:
        ruta = f"/siniestros?estado={estado}" if estado else "/siniestros"
        respuesta = await _pedir(self._cliente, "GET", ruta)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def obtener(self, siniestro_id: str) -> dict | None:
        return _json_o_none(await _pedir(self._cliente, "GET", f"/siniestros/{siniestro_id}"))

    async def peritos(self, siniestro_id: str) -> list[dict]:
        respuesta = await _pedir(self._cliente, "GET", f"/siniestros/{siniestro_id}/peritos")
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def _resolver(self, siniestro_id: str, accion: str, cuerpo: dict) -> dict:
        try:
            respuesta = await self._cliente.post(
                f"/siniestros/{siniestro_id}/{accion}", json=cuerpo
            )
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.status_code in (409, 503):
            # El mensaje lo escribe CLAIMS para el operador (caso ya resuelto, orden de pago no
            # registrada...). Un 503 deja el caso abierto: reintentar es seguro.
            raise ResolucionRechazada(str(_detalle(respuesta) or MENSAJE_RESOLUCION))
        if respuesta.status_code == 422:
            raise ResolucionRechazada(MENSAJE_DATOS_INVALIDOS)
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def aprobar(self, siniestro_id: str, actor_usuario_id: str) -> dict:
        return await self._resolver(siniestro_id, "aprobar", {"actor_usuario_id": actor_usuario_id})

    async def rechazar(
        self, siniestro_id: str, motivo: str, comentario: str | None, actor_usuario_id: str
    ) -> dict:
        return await self._resolver(
            siniestro_id,
            "rechazar",
            {"motivo": motivo, "comentario": comentario, "actor_usuario_id": actor_usuario_id},
        )

    async def derivar(
        self, siniestro_id: str, perito_id: str, instrucciones: str | None, actor_usuario_id: str
    ) -> dict:
        return await self._resolver(
            siniestro_id,
            "derivar",
            {
                "perito_id": perito_id,
                "instrucciones": instrucciones,
                "actor_usuario_id": actor_usuario_id,
            },
        )


def construir_clientes(config: Configuracion) -> tuple[Clientes, list[Any]]:
    """Devuelve los clientes y lo que quedó abierto (`httpx.AsyncClient` y Redis, ambos con
    `aclose`), para cerrarlo al apagar."""

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
    almacenamiento = http(
        config.almacenamiento_url, "almacenamiento", config.servicios_timeout_segundos
    )
    tickets = TicketsRedis(config.redis_url)
    onboarding = http(config.onboarding_url, "onboarding", config.servicios_timeout_segundos)
    claims = http(config.claims_url, "claims", config.servicios_timeout_segundos)
    clientes = Clientes(
        auth=ClienteAuthHttp(auth),
        acl=ClienteAclHttp(acl),
        risk=ClienteRiskHttp(risk),
        rating=ClienteRatingHttp(rating),
        payments=ClientePaymentsHttp(payments),
        policy=ClientePolicyHttp(policy),
        almacenamiento=ClienteAlmacenamientoHttp(almacenamiento),
        tickets=tickets,
        onboarding=ClienteOnboardingHttp(onboarding),
        claims=ClienteClaimsHttp(claims),
        socios=ClienteSociosHttp(auth),
    )
    return clientes, [
        auth,
        acl,
        risk,
        rating,
        payments,
        policy,
        almacenamiento,
        tickets,
        onboarding,
        claims,
    ]


def construir_sondas(config: Configuracion) -> tuple[dict[str, Any], list[Any]]:
    """Un cliente por micro solo para su `/health` (salud del sistema, HU-W07), con la misma
    autenticación de servicio que el resto. Devuelve `{micro: cliente}` y lo que hay que cerrar."""
    urls = {
        "auth": config.auth_url,
        "acl-worker": config.acl_url,
        "risk": config.risk_url,
        "rating": config.rating_url,
        "payments": config.payments_url,
        "policy": config.policy_url,
        "almacenamiento": config.almacenamiento_url,
        "under": config.under_url,
        "consolidador-fuentes": config.consolidador_url,
    }
    clientes = {
        nombre: httpx.AsyncClient(
            base_url=url,
            timeout=1.0,
            auth=autenticacion_para(config.autenticacion_servicios, nombre),
        )
        for nombre, url in urls.items()
    }
    return clientes, list(clientes.values())
