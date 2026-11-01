"""Rutas de la API de socios (`/v1`, HU-W07). Contrato: `contracts/openapi/socios-v1.json`.

Va como un `APIRouter` con rutas explícitas, **nunca** como un `Mount("/v1")`, que se tragaría
`/v1/graphql/*` (W07-24). Cada ruta de negocio reutiliza lo de W01 y W03 con el socio como
intermediario (W07-20):

- cotizar: `ServicioOferta.solicitar`, con la reserva de presupuesto antes y la devolución si falla
  por un error nuestro (W07-9);
- aceptar, código, emitir y reenviar: `ServicioSuscripcion` y `ServicioFirmaPago`, que ya saben
  que un SOCIO solo opera sobre lo que vendió él (404 si no).

Con un token de PRUEBAS responde el modo prueba (`app.socios.modo_prueba`), que nunca llama a los
micros ni a los stubs (W07-10). Ni el documento, ni el contacto, ni el código, ni la clave, ni el
token se loguean."""

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.clientes import CredencialesSocioInvalidas, ErrorServicio, PresupuestoAgotado
from app.esquema import RAMO_POLIZA, estado_visible
from app.identificadores import cliente_id_desde_documento, id_opaco
from app.idioma import idioma_de, traducir
from app.jurisdicciones import PLAZO_MAXIMO_MESES, PLAZO_MINIMO_MESES
from app.monedas import MAXIMO_MONTOS, TASAS_PRUEBA, ConversionInvalida, convertir
from app.revocacion import avisar_revocacion
from app.servicio_firma_pago import ErrorFlujoDatos
from app.servicio_oferta import ErrorOfertaDatos, validar_solicitud
from app.servicio_suscripcion import (
    MENSAJE_CONTACTO_INVALIDO,
    ErrorAceptacionDatos,
    ServicioSuscripcion,
    explicar_estado,
)
from app.socios.autenticacion import SocioActual
from app.socios.errores import (
    MENSAJE_CREDENCIALES_INVALIDAS,
    MENSAJE_PRESUPUESTO_AGOTADO,
    MENSAJE_SIN_CONSENTIMIENTO,
    MENSAJE_SOLO_PRODUCCION,
    ErrorApiSocio,
    datos_invalidos,
    no_disponible,
    no_encontrado,
)

logger = logging.getLogger("bff.socios")

CONTRATO = Path(__file__).resolve().parent.parent / "socios-v1.json"
CANAL_SOCIOS = "API"
SEGUNDOS_PRESUPUESTO = 3600


@cache
def contrato() -> dict:
    return json.loads(CONTRATO.read_text(encoding="utf-8"))


# ---------- Entradas (camelCase del contrato) ----------


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SolicitudToken(_Entrada):
    client_id: str = Field(alias="clientId", min_length=1, max_length=64)
    clave: str = Field(min_length=1, max_length=128)


class SolicitudConsentimiento(_Entrada):
    documento_identidad: str = Field(alias="documentoIdentidad", min_length=6, max_length=20)
    finalidad: str = Field(min_length=1, max_length=100)
    version_texto: str = Field(alias="versionTexto", min_length=1, max_length=20)


class SolicitudCotizacion(_Entrada):
    consentimiento_id: str = Field(alias="consentimientoId", min_length=1)
    documento_identidad: str = Field(alias="documentoIdentidad", min_length=6, max_length=20)
    valor_credito: str = Field(alias="valorCredito", pattern=r"^\d+(\.\d+)?$")
    plazo_meses: int = Field(alias="plazoMeses", ge=PLAZO_MINIMO_MESES, le=PLAZO_MAXIMO_MESES)
    direccion_inmueble: str = Field(alias="direccionInmueble", min_length=1, max_length=200)


class Contacto(_Entrada):
    nombre: str = Field(min_length=2, max_length=100)
    correo: str = Field(max_length=254)


class SolicitudPropuesta(_Entrada):
    oferta_id: str = Field(alias="ofertaId", min_length=1)
    contacto: Contacto


class SolicitudCodigo(_Entrada):
    correo: str | None = Field(default=None, max_length=254)


class MontoEntrada(_Entrada):
    valor: str = Field(pattern=r"^\d+(\.\d+)?$")
    moneda: str = Field(min_length=3, max_length=3)


class SolicitudConversion(_Entrada):
    montos: list[MontoEntrada] = Field(min_length=1, max_length=MAXIMO_MONTOS)
    moneda: str = Field(min_length=3, max_length=3)


class VerificacionCodigo(_Entrada):
    codigo: str = Field(pattern=r"^\d{6}$")


# ---------- Salidas ----------


def _iso(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        valor = datetime.fromisoformat(valor)
    if isinstance(valor, datetime) and valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.isoformat()


def _monto(datos: dict | None) -> dict | None:
    return {"valor": datos["valor"], "moneda": datos["moneda"]} if datos else None


def oferta_rest(datos: dict) -> dict:
    """La oferta de RATING (camelCase) sin lo que no es del socio: ni el desglose del score ni los
    ids de las fuentes."""
    rango = datos.get("rangoPrima")
    return {
        "id": datos["id"],
        "tipo": datos["tipo"],
        "prima": _monto(datos.get("prima")),
        "rangoPrima": (
            {"minima": _monto(rango["minima"]), "maxima": _monto(rango["maxima"])}
            if rango
            else None
        ),
        "cobertura": _monto(datos["cobertura"]),
        "nivelRiesgo": (datos.get("perfilRiesgo") or {}).get("nivelRiesgo"),
        "fuentes": [{"nombre": f["nombre"], "estado": f["estado"]} for f in datos["fuentes"]],
    }


def propuesta_rest(datos: dict, idioma: str, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    explicacion = explicar_estado(datos)
    vence = datos.get("enlace_vence_en")
    enlace = None
    if vence is not None:
        vence_dt = datetime.fromisoformat(vence) if isinstance(vence, str) else vence
        enlace = {
            "vigente": datos["estado"] in ("EN_FIRMA", "FIRMADA") and vence_dt > ahora,
            "venceEn": _iso(vence_dt),
        }
    return {
        "id": datos["id"],
        "ofertaId": datos["oferta_id"],
        "estado": datos["estado"],
        "estadoVisible": estado_visible(datos["estado"]).value,
        "mensajeEstado": traducir(explicacion.mensaje, idioma) if explicacion else None,
        "prima": _monto(datos["prima"]),
        "cobertura": _monto(datos["cobertura"]),
        "plazoMeses": datos.get("plazo_meses") or 0,
        "polizaId": datos.get("poliza_id"),
        "enlace": enlace,
        "creadaEn": _iso(datos.get("creado_en") or datos.get("actualizado_en")),
        "actualizadoEn": _iso(datos["actualizado_en"]),
    }


def poliza_rest(datos: dict) -> dict:
    return {
        "id": datos["id"],
        "numero": str(datos["numero"]),
        "estado": datos["estado"],
        "ramo": RAMO_POLIZA,
        "vigenciaDesde": _iso(datos["vigencia_desde"]),
        "vigenciaHasta": _iso(datos["vigencia_hasta"]),
        "prima": _monto(datos["prima"]),
        "cobertura": _monto(datos["cobertura"]),
    }


def consentimiento_rest(datos: dict) -> dict:
    return {
        "id": datos["id"],
        "finalidad": datos["finalidad"],
        "versionTexto": datos["version_texto"],
        "otorgadoEn": _iso(datos["otorgado_en"]),
        "venceEn": _iso(datos["vence_en"]),
        "revocadoEn": _iso(datos.get("revocado_en")),
        "vigente": datos["vigente"],
        "estado": datos["estado"],
    }


# Rechazos de W03 (`ErrorFlujoDatos.motivo`) -> (estado HTTP, código del contrato).
_FLUJO_A_HTTP = {
    "PROPUESTA_NO_ENCONTRADA": (404, "NO_ENCONTRADO"),
    "PROPUESTA_NO_VALIDA": (409, "PROPUESTA_NO_VALIDA"),
    "CODIGO_INVALIDO": (409, "CODIGO_INVALIDO"),
    "CODIGO_VENCIDO": (409, "CODIGO_VENCIDO"),
    "INTENTOS_AGOTADOS": (409, "INTENTOS_AGOTADOS"),
    "DATOS_INVALIDOS": (422, "DATOS_INVALIDOS"),
}

# Rechazos al aceptar (`ErrorAceptacionDatos.motivo`) -> (estado HTTP, código del contrato).
_ACEPTACION_A_HTTP = {
    "OFERTA_NO_ENCONTRADA": (404, "OFERTA_NO_ENCONTRADA"),
    "OFERTA_PRELIMINAR": (409, "OFERTA_PRELIMINAR"),
    "OFERTA_VENCIDA": (409, "OFERTA_VENCIDA"),
    "CONSENTIMIENTO_NO_VIGENTE": (409, "CONSENTIMIENTO_NO_VIGENTE"),
}


def error_de_flujo(error: ErrorFlujoDatos) -> ErrorApiSocio:
    conocido = _FLUJO_A_HTTP.get(error.motivo or "")
    if conocido is None:
        return no_disponible()
    estado, codigo = conocido
    restantes = error.extra.get("intentos_restantes")
    return ErrorApiSocio(estado, codigo, error.mensaje, error.puede_reintentar, restantes)


def _solo_produccion(socio: SocioActual) -> None:
    if socio.es_prueba:
        raise ErrorApiSocio(403, "PROHIBIDO", MENSAJE_SOLO_PRODUCCION)


def _fecha_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError as error:
        raise datos_invalidos() from error


# ---------- Router ----------


def construir_router() -> APIRouter:
    """Las dependencias (clientes, servicios, verificador y modo prueba) se leen de `app.state`,
    que arma el lifespan del BFF."""
    router = APIRouter(tags=["socios"])

    async def socio_de(request: Request) -> SocioActual:
        socio = await request.app.state.verificador_socios.verificar(request.headers)
        request.state.socio = socio  # para el registro de consumo (`main.py`)
        return socio

    def estado(request: Request) -> Any:
        return request.app.state

    def idioma(request: Request) -> str:
        return idioma_de(request.headers.get("accept-language"))

    def modo_prueba(request: Request) -> Any:
        prueba = getattr(request.app.state, "modo_prueba", None)
        if prueba is None:
            raise no_disponible()
        return prueba

    # ---------- Acceso (públicas) ----------

    @router.post("/v1/token")
    async def crear_token(body: SolicitudToken, request: Request) -> dict:
        try:
            datos = await estado(request).clientes.socios.emitir_token(body.client_id, body.clave)
        except CredencialesSocioInvalidas as error:
            raise ErrorApiSocio(
                401, "CREDENCIALES_INVALIDAS", MENSAJE_CREDENCIALES_INVALIDAS
            ) from error
        except ErrorServicio as error:
            raise no_disponible() from error
        return {
            "accessToken": datos["token"],
            "tokenType": "Bearer",
            "expiraEn": _iso(datos["expira_en"]),
            "entorno": datos["entorno"],
        }

    @router.get("/v1/.well-known/jwks.json")
    async def jwks(request: Request) -> dict:
        return await estado(request).verificador_socios.jwks()

    @router.get("/v1/openapi.json")
    def openapi() -> dict:
        return contrato()

    @router.get("/v1/docs", response_class=HTMLResponse)
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/v1/openapi.json", title="Solventa · API de socios")

    # ---------- Consentimiento ----------

    @router.post("/v1/consentimientos", status_code=201)
    async def registrar_consentimiento(body: SolicitudConsentimiento, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).consentimiento(
                socio, body.finalidad, body.version_texto, body.documento_identidad
            )
        documento = body.documento_identidad.strip()
        if sum(c.isdigit() for c in documento) < 6:
            raise datos_invalidos()
        titular_id = cliente_id_desde_documento(documento, request.app.state.cliente_id_clave)
        try:
            datos = await estado(request).clientes.auth.registrar_consentimiento(
                body.finalidad,
                body.version_texto,
                {"rol": "SOCIO", "usuario_id": socio.id},
                titular_id=titular_id,
                canal=CANAL_SOCIOS,
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        logger.info("consentimiento de socio socio=%s", id_opaco(socio.id))
        return consentimiento_rest(datos)

    async def _consentimiento_del_socio(request: Request, socio: SocioActual, cid: str) -> dict:
        try:
            datos = await estado(request).clientes.auth.obtener_consentimiento(cid)
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos is None or datos.get("actor") != {"rol": "SOCIO", "usuario_id": socio.id}:
            raise no_encontrado()
        return datos

    @router.post("/v1/consentimientos/{consentimiento_id}/revocar")
    async def revocar_consentimiento(consentimiento_id: str, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).revocar(socio, consentimiento_id)
        await _consentimiento_del_socio(request, socio, consentimiento_id)
        clientes = estado(request).clientes
        try:
            datos = await clientes.auth.revocar_consentimiento(consentimiento_id)
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos is None:
            raise no_encontrado()
        titular_id = datos.get("titular_id")
        if titular_id:
            await avisar_revocacion(
                clientes, estado(request).servicio_firma_pago, titular_id, consentimiento_id
            )
        return consentimiento_rest(datos)

    # ---------- Cotización ----------

    @router.post("/v1/cotizaciones")
    async def cotizar(body: SolicitudCotizacion, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).cotizar(
                socio, body.consentimiento_id, body.documento_identidad
            )
        # Primero lo que no gasta presupuesto: los datos y que el consentimiento sea de este socio.
        mensaje = validar_solicitud(
            body.documento_identidad, body.valor_credito, body.plazo_meses, body.direccion_inmueble
        )
        if mensaje:
            raise datos_invalidos(mensaje)
        consentimiento = await _consentimiento_del_socio(request, socio, body.consentimiento_id)
        if not consentimiento.get("vigente"):
            raise ErrorApiSocio(409, "CONSENTIMIENTO_NO_VIGENTE", MENSAJE_SIN_CONSENTIMIENTO)
        socios = estado(request).clientes.socios
        try:
            restantes = await socios.reservar(socio.id, socio.entorno)
        except PresupuestoAgotado as error:
            raise ErrorApiSocio(
                429, "PRESUPUESTO_AGOTADO", MENSAJE_PRESUPUESTO_AGOTADO, False, None,
                SEGUNDOS_PRESUPUESTO,
            ) from error  # fmt: skip
        except ErrorServicio as error:
            raise no_disponible() from error
        resultado = await estado(request).servicio_oferta.solicitar(
            body.consentimiento_id,
            body.documento_identidad,
            body.valor_credito,
            body.plazo_meses,
            body.direccion_inmueble,
        )
        if isinstance(resultado, ErrorOfertaDatos):
            # La cotización no se hizo: la unidad vuelve al presupuesto (W07-9).
            try:
                await socios.devolver(socio.id, socio.entorno)
            except ErrorServicio:
                logger.warning("no se pudo devolver una cotización socio=%s", id_opaco(socio.id))
            if resultado.motivo == "CONSENTIMIENTO_NO_VIGENTE":
                raise ErrorApiSocio(409, "CONSENTIMIENTO_NO_VIGENTE", resultado.mensaje)
            if not resultado.puede_reintentar:
                raise datos_invalidos(resultado.mensaje)
            raise no_disponible()
        return {"oferta": oferta_rest(resultado), "presupuestoRestante": restantes}

    # ---------- Propuesta, identidad y emisión ----------

    @router.post("/v1/propuestas", status_code=201)
    async def crear_propuesta(body: SolicitudPropuesta, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).aceptar(socio, body.oferta_id, idioma(request))
        clientes = estado(request).clientes
        try:
            datos_socio = await clientes.socios.obtener(socio.id)
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos_socio is None:
            raise ErrorApiSocio(401, "TOKEN_INVALIDO", MENSAJE_CREDENCIALES_INVALIDAS)
        resultado = await ServicioSuscripcion(clientes).aceptar(
            body.oferta_id,
            socio.como_usuario(),
            body.contacto.model_dump(),
            datos_socio["tasa_comision"],
        )
        if isinstance(resultado, ErrorAceptacionDatos):
            if resultado.mensaje == MENSAJE_CONTACTO_INVALIDO:
                raise ErrorApiSocio(422, "CONTACTO_INVALIDO", resultado.mensaje)
            conocido = _ACEPTACION_A_HTTP.get(resultado.motivo or "")
            if conocido is None:
                raise no_disponible()
            raise ErrorApiSocio(
                conocido[0], conocido[1], resultado.mensaje, resultado.puede_reintentar
            )
        return propuesta_rest(resultado, idioma(request))

    async def _propuesta_del_socio(request: Request, socio: SocioActual, pid: str) -> dict:
        try:
            datos = await ServicioSuscripcion(estado(request).clientes).obtener_propuesta(
                pid, socio.como_usuario()
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos is None:
            raise no_encontrado()
        return datos

    @router.get("/v1/propuestas/{propuesta_id}")
    async def obtener_propuesta(propuesta_id: str, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).propuesta(socio, propuesta_id, idioma(request))
        return propuesta_rest(
            await _propuesta_del_socio(request, socio, propuesta_id), idioma(request)
        )

    @router.post("/v1/propuestas/{propuesta_id}/codigo")
    async def enviar_codigo(propuesta_id: str, body: SolicitudCodigo, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).codigo(socio, propuesta_id)
        datos = await estado(request).servicio_firma_pago.enviar_codigo(
            socio.como_usuario(), propuesta_id, body.correo
        )
        if isinstance(datos, ErrorFlujoDatos):
            raise error_de_flujo(datos)
        return {
            "correoEnmascarado": datos["correo_enmascarado"],
            "venceEn": _iso(datos["vence_en"]),
        }

    @router.post("/v1/propuestas/{propuesta_id}/codigo/verificar")
    async def verificar_codigo(
        propuesta_id: str, body: VerificacionCodigo, request: Request
    ) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).verificar(
                socio, propuesta_id, body.codigo, idioma(request)
            )
        datos = await estado(request).servicio_firma_pago.verificar_codigo(
            socio.como_usuario(), propuesta_id, body.codigo
        )
        if isinstance(datos, ErrorFlujoDatos):
            raise error_de_flujo(datos)
        return propuesta_rest(datos, idioma(request))

    @router.post("/v1/propuestas/{propuesta_id}/emitir")
    async def emitir(propuesta_id: str, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).emitir(socio, propuesta_id, idioma(request))
        datos = await estado(request).servicio_firma_pago.emitir(socio.como_usuario(), propuesta_id)
        if isinstance(datos, ErrorFlujoDatos):
            raise error_de_flujo(datos)
        enlace = datos.get("enlace")
        propuesta = dict(datos["propuesta"])
        if enlace:
            propuesta["enlace_vence_en"] = enlace["vence_en"]
        return {
            "propuesta": propuesta_rest(propuesta, idioma(request)),
            "enlace": (
                {
                    "correoEnmascarado": enlace["correo_enmascarado"],
                    "venceEn": _iso(enlace["vence_en"]),
                }
                if enlace
                else None
            ),
        }

    @router.post("/v1/propuestas/{propuesta_id}/reenviar-enlace")
    async def reenviar_enlace(propuesta_id: str, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).reenviar(socio, propuesta_id)
        datos = await estado(request).servicio_firma_pago.reenviar_enlace(
            socio.como_usuario(), propuesta_id
        )
        if isinstance(datos, ErrorFlujoDatos):
            raise error_de_flujo(datos)
        enlace = datos["enlace"]
        return {
            "correoEnmascarado": enlace["correo_enmascarado"],
            "venceEn": _iso(enlace["vence_en"]),
        }

    # ---------- Póliza ----------

    @router.get("/v1/polizas/{poliza_id}")
    async def obtener_poliza(poliza_id: str, request: Request) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).poliza(socio, poliza_id)
        try:
            datos = await ServicioSuscripcion(estado(request).clientes).obtener_poliza(
                poliza_id, socio.como_usuario()
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos is None:
            raise no_encontrado()
        return poliza_rest(datos)

    @router.get("/v1/polizas/{poliza_id}/documento", response_class=Response)
    async def documento_poliza(poliza_id: str, request: Request) -> Response:
        socio = await socio_de(request)
        if socio.es_prueba:
            numero, pdf = await modo_prueba(request).documento(socio, poliza_id)
        else:
            try:
                documento = await ServicioSuscripcion(estado(request).clientes).documento_poliza(
                    poliza_id, socio.como_usuario()
                )
            except ErrorServicio as error:
                raise no_disponible() from error
            if documento is None:
                raise no_encontrado()
            numero, pdf = documento
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="poliza-{numero}.pdf"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ---------- Cuenta ----------

    @router.get("/v1/estado")
    async def estado_sistema(request: Request, ventana: str = "1h") -> dict:
        await socio_de(request)
        salud = getattr(estado(request), "salud", None)
        if salud is None:
            raise no_disponible()
        if ventana not in salud.ventanas:
            raise datos_invalidos()
        return await salud.para_socio(ventana)

    @router.get("/v1/consumo")
    async def consumo(request: Request, desde: str | None = None, hasta: str | None = None) -> dict:
        socio = await socio_de(request)
        try:
            datos = await estado(request).clientes.socios.consumo(
                socio.id, socio.entorno, _fecha_opcional(desde), _fecha_opcional(hasta)
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        return {
            "entorno": datos["entorno"],
            "desde": datos["desde"],
            "hasta": datos["hasta"],
            "presupuesto": datos["presupuesto"],
            "porApi": [
                {
                    "api": a["api"],
                    "llamadas": a["llamadas"],
                    "errores": a["errores"],
                    "latenciaMediaMs": a.get("latencia_media_ms"),
                }
                for a in datos["por_api"]
            ],
            "diario": datos["diario"],
        }

    @router.get("/v1/ventas")
    async def ventas(request: Request, desde: str | None = None, hasta: str | None = None) -> dict:
        socio = await socio_de(request)
        if socio.es_prueba:
            return await modo_prueba(request).ventas(socio)
        clientes = estado(request).clientes
        try:
            datos_socio, datos = await asyncio.gather(
                clientes.socios.obtener(socio.id),
                clientes.policy.ventas_socio(
                    socio.id, _fecha_opcional(desde), _fecha_opcional(hasta)
                ),
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        return {
            "desde": datos["desde"],
            "hasta": datos["hasta"],
            "tasaComision": (datos_socio or {}).get("tasa_comision", "0"),
            "meses": datos["meses"],
            "totales": datos["totales"],
        }

    @router.post("/v1/conversiones")
    async def conversiones(cuerpo: SolicitudConversion, request: Request) -> dict:
        """Solo para mostrar: no cambia lo cobrado ni descuenta presupuesto (KAN-209)."""
        socio = await socio_de(request)
        if socio.es_prueba:
            tasas = TASAS_PRUEBA
        else:
            try:
                tasas = await estado(request).clientes.acl.tasas_cambio()
            except ErrorServicio as error:
                raise no_disponible() from error
        try:
            montos = [
                {
                    "valor": convertir(m.valor, m.moneda, cuerpo.moneda, tasas),
                    "moneda": cuerpo.moneda,
                }
                for m in cuerpo.montos
            ]
        except ConversionInvalida as error:
            raise datos_invalidos() from error
        return {"montos": montos}

    @router.get("/v1/credenciales")
    async def credenciales(request: Request) -> dict:
        socio = await socio_de(request)
        _solo_produccion(socio)
        socios = estado(request).clientes.socios
        try:
            datos_socio, lista = await asyncio.gather(
                socios.obtener(socio.id), socios.credenciales(socio.id)
            )
        except ErrorServicio as error:
            raise no_disponible() from error
        if datos_socio is None:
            raise no_encontrado()
        return {
            "socio": {"id": datos_socio["id"], "nombre": datos_socio["nombre"]},
            "credenciales": [_credencial(c) for c in lista],
        }

    @router.post("/v1/credenciales/{entorno}/rotar")
    async def rotar(entorno: str, request: Request) -> dict:
        socio = await socio_de(request)
        _solo_produccion(socio)
        if entorno not in ("PRODUCCION", "PRUEBAS"):
            raise datos_invalidos()
        try:
            credencial = await estado(request).clientes.socios.rotar(socio.id, entorno)
        except ErrorServicio as error:
            raise no_disponible() from error
        logger.info("credencial rotada socio=%s entorno=%s", id_opaco(socio.id), entorno)
        return _credencial(credencial)

    return router


def _credencial(datos: dict) -> dict:
    return {
        "entorno": datos["entorno"],
        "clientId": datos["client_id"],
        "clave": datos["clave"],
        "creadaEn": _iso(datos["creada_en"]),
    }


def es_ruta_de_socios(camino: str) -> bool:
    return camino.startswith("/v1/") and not camino.startswith("/v1/graphql")


def api_de(camino: str) -> str:
    """El nombre de la API para el consumo: el primer tramo después de `/v1/`."""
    return camino.removeprefix("/v1/").split("/", 1)[0] or "raiz"
