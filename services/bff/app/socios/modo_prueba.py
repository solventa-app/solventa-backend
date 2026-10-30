"""Modo prueba de la API de socios (HU-W07, W07-10): lo que responde `/v1` con un token de PRUEBAS.

Simula el flujo completo, del consentimiento a la póliza emitida, **sin llamar a las fuentes, a
RISK, a RATING, a POLICY ni a ningún stub**. Solo el presupuesto de cotizaciones de PRUEBAS y el
consumo van a AUTH, para que el panel del socio muestre su uso real del sandbox.

- **Respuestas por documento de prueba** (tabla `DOCUMENTOS`, como los mapeos de WireMock); otro
  documento cualquiera se comporta como `1000000001`.
- **Código de verificación fijo:** `000000`.
- **Avance solo:** tras «emitir», la propuesta queda `EN_FIRMA` y avanza sola: `FIRMADA` a los
  `SEGUNDOS_FIRMA` y `EMITIDA` (con póliza) a los `SEGUNDOS_EMISION`. No hay página de firma: el
  titular simulado firma y paga solo.
- **Estado en Redis con TTL** (`prueba:<socio>:...`, 24 h). Si se pierde (Redis apagado de noche),
  el sandbox empieza de cero; sin Redis (pruebas, local sin Redis) se guarda en memoria.

Los ids llevan el prefijo `prueba-` para que nadie los confunda con datos reales."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.clientes import ErrorServicio, PresupuestoAgotado
from app.idioma import traducir
from app.socios.errores import (
    MENSAJE_PRESUPUESTO_AGOTADO,
    MENSAJE_SIN_CONSENTIMIENTO,
    ErrorApiSocio,
    no_disponible,
    no_encontrado,
)

TTL_SEGUNDOS = 24 * 3600
SEGUNDOS_FIRMA = 3
SEGUNDOS_EMISION = 6
CODIGO_DE_PRUEBA = "000000"
INTENTOS_CODIGO = 5
MONEDA = "COP"

MENSAJE_OFERTA_PRELIMINAR = "Esta oferta es preliminar y no se puede aceptar. Vuelve a calcularla."
MENSAJE_CODIGO_INVALIDO = "El código no es correcto. Revísalo e intenta de nuevo."
MENSAJE_PROPUESTA_NO_VALIDA = "La propuesta no está en un estado que permita esta operación."
MENSAJE_REVISION = "Estamos revisando tu solicitud. Te avisamos cuando esté lista."
MENSAJE_NO_APROBADA = (
    "No pudimos aprobar tu solicitud. Habla con un asesor para conocer tus opciones."
)


@dataclass(frozen=True)
class Escenario:
    descripcion: str
    tipo: str = "COMPLETA"  # COMPLETA o PRELIMINAR
    decision: str = "APROBADA"  # APROBADA, RECHAZADA o REVISION
    presupuesto_agotado: bool = False
    consentimiento_vencido: bool = False


DOCUMENTOS: dict[str, Escenario] = {
    "1000000001": Escenario("Oferta COMPLETA; la póliza se emite."),
    "1000000002": Escenario("Oferta PRELIMINAR (fuentes degradadas): no se acepta.", "PRELIMINAR"),
    "1000000003": Escenario("La suscripción no aprueba: NO_EMITIDA.", decision="RECHAZADA"),
    "1000000004": Escenario("La suscripción pide revisión: EN_REVISION.", decision="REVISION"),
    "1000000005": Escenario("Presupuesto agotado: 429.", presupuesto_agotado=True),
    "1000000006": Escenario("Consentimiento vencido: 409.", consentimiento_vencido=True),
}
POR_DEFECTO = DOCUMENTOS["1000000001"]

# PDF mínimo de una página con el texto «MODO PRUEBA» (sin librerías: solo para el sandbox).
_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 79>>stream\nBT /F1 28 Tf 120 700 Td (MODO PRUEBA) Tj 0 -40 Td "
    b"/F1 14 Tf (Poliza simulada) Tj ET\nendstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


class Almacen(Protocol):
    async def leer(self, clave: str) -> dict | None: ...

    async def guardar(self, clave: str, valor: dict) -> None: ...


class AlmacenMemoria:
    def __init__(self) -> None:
        self._datos: dict[str, str] = {}

    async def leer(self, clave: str) -> dict | None:
        valor = self._datos.get(clave)
        return json.loads(valor) if valor else None

    async def guardar(self, clave: str, valor: dict) -> None:
        self._datos[clave] = json.dumps(valor)


class AlmacenRedis:
    """Si Redis no responde, el sandbox sigue en memoria: es un entorno de pruebas."""

    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5
        )
        self._respaldo = AlmacenMemoria()

    async def leer(self, clave: str) -> dict | None:
        try:
            valor = await self._redis.get(clave)
        except Exception:  # noqa: BLE001 - Redis caído: se usa la memoria
            return await self._respaldo.leer(clave)
        return json.loads(valor) if valor else await self._respaldo.leer(clave)

    async def guardar(self, clave: str, valor: dict) -> None:
        await self._respaldo.guardar(clave, valor)
        try:
            await self._redis.set(clave, json.dumps(valor), ex=TTL_SEGUNDOS)
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self) -> None:
        await self._redis.aclose()


def _iso(momento: datetime) -> str:
    return momento.isoformat()


def _monto(valor: str) -> dict:
    return {"valor": valor, "moneda": MONEDA}


class ModoPrueba:
    def __init__(self, almacen: Almacen, socios: Any, reloj=lambda: datetime.now(UTC)) -> None:
        self._almacen = almacen
        self._socios = socios  # el puerto de AUTH: solo presupuesto de PRUEBAS
        self._reloj = reloj

    def _clave(self, socio_id: str, tipo: str, ident: str) -> str:
        return f"prueba:{socio_id}:{tipo}:{ident}"

    async def _leer(self, socio_id: str, tipo: str, ident: str) -> dict:
        datos = await self._almacen.leer(self._clave(socio_id, tipo, ident))
        if datos is None:
            raise no_encontrado()
        return datos

    async def _guardar(self, socio_id: str, tipo: str, datos: dict) -> None:
        await self._almacen.guardar(self._clave(socio_id, tipo, datos["id"]), datos)

    # ---------- Consentimiento y cotización ----------

    async def consentimiento(self, socio, finalidad: str, version_texto: str, documento: str):
        ahora = self._reloj()
        escenario = DOCUMENTOS.get(documento.strip(), POR_DEFECTO)
        vence = (
            ahora - timedelta(days=1)
            if escenario.consentimiento_vencido
            else ahora + timedelta(days=30)
        )
        datos = {
            "id": f"prueba-c-{uuid.uuid4()}",
            "finalidad": finalidad,
            "versionTexto": version_texto,
            "otorgadoEn": _iso(ahora),
            "venceEn": _iso(vence),
            "revocadoEn": None,
            "vigente": not escenario.consentimiento_vencido,
            "estado": "VENCIDO" if escenario.consentimiento_vencido else "VIGENTE",
            "documento": documento.strip(),
        }
        await self._guardar(socio.id, "consentimiento", datos)
        return _sin(datos, "documento")

    async def revocar(self, socio, consentimiento_id: str) -> dict:
        datos = await self._leer(socio.id, "consentimiento", consentimiento_id)
        datos.update(revocadoEn=_iso(self._reloj()), vigente=False, estado="REVOCADO")
        await self._guardar(socio.id, "consentimiento", datos)
        return _sin(datos, "documento")

    async def cotizar(self, socio, consentimiento_id: str, documento: str) -> dict:
        consentimiento = await self._leer(socio.id, "consentimiento", consentimiento_id)
        if not consentimiento["vigente"] or consentimiento["documento"] != documento.strip():
            raise ErrorApiSocio(409, "CONSENTIMIENTO_NO_VIGENTE", MENSAJE_SIN_CONSENTIMIENTO)
        escenario = DOCUMENTOS.get(documento.strip(), POR_DEFECTO)
        if escenario.presupuesto_agotado:
            raise ErrorApiSocio(
                429, "PRESUPUESTO_AGOTADO", MENSAJE_PRESUPUESTO_AGOTADO, False, None, 3600
            )
        try:
            restantes = await self._socios.reservar(socio.id, socio.entorno)
        except PresupuestoAgotado as error:
            raise ErrorApiSocio(
                429, "PRESUPUESTO_AGOTADO", MENSAJE_PRESUPUESTO_AGOTADO, False, None, 3600
            ) from error
        except ErrorServicio as error:
            raise no_disponible() from error
        completa = escenario.tipo == "COMPLETA"
        oferta = {
            "id": f"prueba-o-{uuid.uuid4()}",
            "tipo": escenario.tipo,
            "prima": _monto("61200.00") if completa else None,
            "rangoPrima": None
            if completa
            else {"minima": _monto("48960.00"), "maxima": _monto("73440.00")},
            "cobertura": _monto("200000000"),
            "nivelRiesgo": "BAJO" if completa else None,
            "fuentes": [
                {"nombre": nombre, "estado": "OK" if completa else "CACHE"}
                for nombre in ("cuentas-bancarias", "historial-crediticio", "afiliacion-pila")
            ],
        }
        await self._guardar(socio.id, "oferta", {**oferta, "documento": documento.strip()})
        return {"oferta": oferta, "presupuestoRestante": restantes}

    # ---------- Propuesta ----------

    async def aceptar(self, socio, oferta_id: str, idioma: str) -> dict:
        oferta = await self._leer(socio.id, "oferta", oferta_id)
        if oferta["tipo"] != "COMPLETA":
            raise ErrorApiSocio(409, "OFERTA_PRELIMINAR", MENSAJE_OFERTA_PRELIMINAR, True)
        existente = await self._almacen.leer(self._clave(socio.id, "aceptada", oferta_id))
        if existente:  # aceptar dos veces la misma oferta devuelve la misma propuesta
            return await self.propuesta(socio, existente["id"], idioma)
        ahora = _iso(self._reloj())
        propuesta = {
            "id": f"prueba-p-{uuid.uuid4()}",
            "ofertaId": oferta_id,
            "documento": oferta["documento"],
            "estado": "ACEPTADA",
            "prima": oferta["prima"],
            "cobertura": oferta["cobertura"],
            "plazoMeses": 60,
            "polizaId": None,
            "intentos": 0,
            "emitidaEn": None,
            "venceEnlace": None,
            "creadaEn": ahora,
            "actualizadoEn": ahora,
        }
        await self._guardar(socio.id, "propuesta", propuesta)
        await self._almacen.guardar(
            self._clave(socio.id, "aceptada", oferta_id), {"id": propuesta["id"]}
        )
        return self._salida(propuesta, idioma)

    async def _avanzar(self, socio, propuesta: dict) -> dict:
        """El titular simulado firma y paga solo después de «emitir»."""
        if propuesta["estado"] not in ("EN_FIRMA", "FIRMADA") or not propuesta["emitidaEn"]:
            return propuesta
        emitida = datetime.fromisoformat(propuesta["emitidaEn"])
        transcurrido = (self._reloj() - emitida).total_seconds()
        if transcurrido >= SEGUNDOS_EMISION:
            ahora = self._reloj()
            poliza = {
                "id": f"prueba-pol-{propuesta['id'].removeprefix('prueba-p-')}",
                "numero": f"PRUEBA-{ahora:%Y}-{propuesta['id'][-6:]}",
                "estado": "EMITIDA",
                "ramo": "MODO PRUEBA",
                "vigenciaDesde": _iso(ahora),
                "vigenciaHasta": _iso(ahora + timedelta(days=365 * 5)),
                "prima": propuesta["prima"],
                "cobertura": propuesta["cobertura"],
            }
            await self._guardar(socio.id, "poliza", poliza)
            propuesta.update(estado="EMITIDA", polizaId=poliza["id"], actualizadoEn=_iso(ahora))
        elif transcurrido >= SEGUNDOS_FIRMA and propuesta["estado"] == "EN_FIRMA":
            propuesta.update(estado="FIRMADA", actualizadoEn=_iso(self._reloj()))
        await self._guardar(socio.id, "propuesta", propuesta)
        return propuesta

    async def propuesta(self, socio, propuesta_id: str, idioma: str) -> dict:
        propuesta = await self._avanzar(
            socio, await self._leer(socio.id, "propuesta", propuesta_id)
        )
        return self._salida(propuesta, idioma)

    async def codigo(self, socio, propuesta_id: str) -> dict:
        propuesta = await self._leer(socio.id, "propuesta", propuesta_id)
        if propuesta["estado"] != "ACEPTADA":
            raise ErrorApiSocio(409, "PROPUESTA_NO_VALIDA", MENSAJE_PROPUESTA_NO_VALIDA)
        return {
            "correoEnmascarado": "t•••@prueba.solventa",
            "venceEn": _iso(self._reloj() + timedelta(minutes=10)),
        }

    async def verificar(self, socio, propuesta_id: str, codigo: str, idioma: str) -> dict:
        propuesta = await self._leer(socio.id, "propuesta", propuesta_id)
        if propuesta["estado"] == "IDENTIDAD_VERIFICADA":
            return self._salida(propuesta, idioma)
        if propuesta["estado"] != "ACEPTADA":
            raise ErrorApiSocio(409, "PROPUESTA_NO_VALIDA", MENSAJE_PROPUESTA_NO_VALIDA)
        if codigo != CODIGO_DE_PRUEBA:
            propuesta["intentos"] += 1
            await self._guardar(socio.id, "propuesta", propuesta)
            restantes = max(0, INTENTOS_CODIGO - propuesta["intentos"])
            raise ErrorApiSocio(409, "CODIGO_INVALIDO", MENSAJE_CODIGO_INVALIDO, True, restantes)
        propuesta.update(estado="IDENTIDAD_VERIFICADA", actualizadoEn=_iso(self._reloj()))
        await self._guardar(socio.id, "propuesta", propuesta)
        return self._salida(propuesta, idioma)

    async def emitir(self, socio, propuesta_id: str, idioma: str) -> dict:
        propuesta = await self._leer(socio.id, "propuesta", propuesta_id)
        if propuesta["estado"] in ("EN_FIRMA", "FIRMADA", "EMITIDA", "NO_EMITIDA", "EN_REVISION"):
            return {"propuesta": self._salida(propuesta, idioma), "enlace": None}
        if propuesta["estado"] != "IDENTIDAD_VERIFICADA":
            raise ErrorApiSocio(409, "PROPUESTA_NO_VALIDA", MENSAJE_PROPUESTA_NO_VALIDA)
        escenario = DOCUMENTOS.get(propuesta["documento"], POR_DEFECTO)
        ahora = self._reloj()
        enlace = None
        if escenario.decision == "RECHAZADA":
            propuesta["estado"] = "NO_EMITIDA"
        elif escenario.decision == "REVISION":
            propuesta["estado"] = "EN_REVISION"
        else:
            vence = ahora + timedelta(minutes=30)
            propuesta.update(estado="EN_FIRMA", emitidaEn=_iso(ahora), venceEnlace=_iso(vence))
            enlace = {"correoEnmascarado": "t•••@prueba.solventa", "venceEn": _iso(vence)}
        propuesta["actualizadoEn"] = _iso(ahora)
        await self._guardar(socio.id, "propuesta", propuesta)
        return {"propuesta": self._salida(propuesta, idioma), "enlace": enlace}

    async def reenviar(self, socio, propuesta_id: str) -> dict:
        propuesta = await self._leer(socio.id, "propuesta", propuesta_id)
        if propuesta["estado"] not in ("EN_FIRMA", "FIRMADA"):
            raise ErrorApiSocio(409, "PROPUESTA_NO_VALIDA", MENSAJE_PROPUESTA_NO_VALIDA)
        vence = self._reloj() + timedelta(minutes=30)
        propuesta["venceEnlace"] = _iso(vence)
        await self._guardar(socio.id, "propuesta", propuesta)
        return {"correoEnmascarado": "t•••@prueba.solventa", "venceEn": _iso(vence)}

    # ---------- Póliza y ventas ----------

    async def poliza(self, socio, poliza_id: str) -> dict:
        return await self._leer(socio.id, "poliza", poliza_id)

    async def documento(self, socio, poliza_id: str) -> tuple[str, bytes]:
        poliza = await self._leer(socio.id, "poliza", poliza_id)
        return poliza["numero"], _PDF

    async def ventas(self, socio) -> dict:
        """Cifras fijas, para que el panel del sandbox tenga datos (W07-10)."""
        hoy = self._reloj().date()
        meses = []
        for atras, (propuestas, emitidas) in enumerate([(4, 2), (7, 5), (12, 9)]):
            anio, mes = hoy.year, hoy.month - (2 - atras)
            while mes <= 0:
                anio, mes = anio - 1, mes + 12
            primas = 61200 * emitidas
            meses.append(
                {
                    "mes": f"{anio:04d}-{mes:02d}",
                    "propuestas": propuestas,
                    "emitidas": emitidas,
                    "primas": _monto(f"{primas}.00"),
                    "comision": _monto(f"{primas // 10}.00"),
                }
            )
        totales_primas = sum(int(m["primas"]["valor"].split(".")[0]) for m in meses)
        return {
            "desde": f"{meses[0]['mes']}-01",
            "hasta": hoy.isoformat(),
            "tasaComision": "0.1000",
            "meses": meses,
            "totales": {
                "propuestas": sum(m["propuestas"] for m in meses),
                "emitidas": sum(m["emitidas"] for m in meses),
                "primas": _monto(f"{totales_primas}.00"),
                "comision": _monto(f"{totales_primas // 10}.00"),
            },
        }

    # ---------- Salida ----------

    def _salida(self, propuesta: dict, idioma: str) -> dict:
        mensaje = {"EN_REVISION": MENSAJE_REVISION, "NO_EMITIDA": MENSAJE_NO_APROBADA}.get(
            propuesta["estado"]
        )
        visible = (
            "EMITIDA"
            if propuesta["estado"] == "EMITIDA"
            else "NO_EMITIDA"
            if propuesta["estado"] in ("NO_EMITIDA", "CANCELADA")
            else "EMITIENDO"
        )
        enlace = None
        if propuesta["venceEnlace"]:
            enlace = {
                "vigente": propuesta["estado"] in ("EN_FIRMA", "FIRMADA")
                and datetime.fromisoformat(propuesta["venceEnlace"]) > self._reloj(),
                "venceEn": propuesta["venceEnlace"],
            }
        return {
            "id": propuesta["id"],
            "ofertaId": propuesta["ofertaId"],
            "estado": propuesta["estado"],
            "estadoVisible": visible,
            "mensajeEstado": traducir(mensaje, idioma) if mensaje else None,  # type: ignore[arg-type]
            "prima": propuesta["prima"],
            "cobertura": propuesta["cobertura"],
            "plazoMeses": propuesta["plazoMeses"],
            "polizaId": propuesta["polizaId"],
            "enlace": enlace,
            "creadaEn": propuesta["creadaEn"],
            "actualizadoEn": propuesta["actualizadoEn"],
        }


def _sin(datos: dict, clave: str) -> dict:
    return {k: v for k, v in datos.items() if k != clave}
