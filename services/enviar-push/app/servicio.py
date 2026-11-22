"""Orquesta un `notificacion.push-solicitada` ya validado: plantilla -> destino en AUTH -> SNS.

Nunca deja un fallo de negocio (sin dispositivo, push apagado, plantilla desconocida) como algo
para reintentar: esos se confirman (regla #4, nunca bloquear al productor; CA-M04-07). Solo un
fallo transitorio (AUTH caído, SNS caído) se propaga para que Pub/Sub reintente con su backoff."""

import logging
from typing import Any

from app.clientes import (
    ErrorServicio,
    PuertoAuth,
    PuertoTasas,
    PushDeshabilitado,
    SinDispositivoActivo,
)
from app.identificadores import id_opaco
from app.monedas import DECIMALES_VISIBLES, convertir, importe_legible, numero_legible
from app.plantillas import IDIOMA_POR_DEFECTO, Plantillas
from app.sns import PuertoEnviadorPush, TokenInvalido

logger = logging.getLogger("enviar_push.servicio")


class ServicioEnviarPush:
    def __init__(
        self,
        auth: PuertoAuth,
        enviador: PuertoEnviadorPush,
        plantillas: Plantillas,
        tasas: PuertoTasas | None = None,
    ) -> None:
        self._auth = auth
        self._enviador = enviador
        self._plantillas = plantillas
        self._tasas = tasas

    async def _variables_para(self, crudas: dict[str, Any], destino: dict) -> dict[str, str]:
        """Las variables del productor, listas para la plantilla del destinatario: sin `nombre`
        ajeno, con su primer nombre, el monto en la moneda que eligió y los números escritos
        como se leen en su idioma."""
        idioma = destino.get("idioma") or IDIOMA_POR_DEFECTO
        # El nombre lo pone este servicio, nunca el productor del evento (regla #6: no viaja en
        # Pub/Sub ni se loguea). Sin nombre, la plantilla usa su título sin saludo.
        variables = {k: str(v) for k, v in crudas.items() if k != "nombre"}
        if destino.get("primer_nombre"):
            variables["nombre"] = destino["primer_nombre"]

        if "monto" in variables:
            monto: Any = variables["monto"]
            moneda = variables.get("moneda", "")
            preferida = destino.get("moneda") or moneda
            if preferida != moneda and self._tasas is not None:
                try:
                    convertido = convertir(
                        monto, moneda, preferida, await self._tasas.tasas_cambio()
                    )
                except ErrorServicio as error:
                    # Regla #4: sin tasas el push sale igual, en la moneda original.
                    logger.warning("sin tasas de cambio (%s); monto en la moneda original", error)
                    convertido = None
                if convertido is not None:
                    monto, moneda = convertido, preferida
                    variables["moneda"] = preferida
            variables["importe"] = importe_legible(monto, moneda, idioma)
            variables["monto"] = numero_legible(monto, idioma, DECIMALES_VISIBLES.get(moneda))
        for clave in ("minutos", "valor"):
            if clave in variables:
                variables[clave] = numero_legible(variables[clave], idioma)
        return variables

    async def procesar(self, evento: dict[str, Any]) -> str:
        """Devuelve el resultado como texto (para el log del llamador): `ENVIADO`, `SIN_DESTINO`,
        `PUSH_DESHABILITADO`, `PLANTILLA_DESCONOCIDA` o `TOKEN_INVALIDO`. Cualquiera de estas
        termina el procesamiento del evento (no se reintenta)."""
        datos = evento["datos"]
        # Solo se mira que exista: las variables se completan después (nombre, importe), con el
        # destinatario ya resuelto.
        if not self._plantillas.existe(datos["plantilla"]):
            # Lo que llega en el evento no se loguea en crudo (inyección en logs): su huella.
            logger.error(
                "plantilla desconocida=%s; evento descartado", id_opaco(str(datos["plantilla"]))
            )
            return "PLANTILLA_DESCONOCIDA"

        try:
            destino = await self._auth.destino_push(datos["usuarioId"])
        except SinDispositivoActivo:
            logger.info("sin dispositivo activo; push confirmado sin enviar")
            return "SIN_DESTINO"
        except PushDeshabilitado:
            logger.info("push deshabilitado por el cliente; push confirmado sin enviar")
            return "PUSH_DESHABILITADO"

        variables = await self._variables_para(datos.get("variables") or {}, destino)
        mensaje = {
            # En el idioma que el cliente eligió (AUTH); la plantilla cae a `es` si no lo tiene.
            **self._plantillas.resolver(
                datos["plantilla"], variables, destino.get("idioma") or IDIOMA_POR_DEFECTO
            ),
            "idempotencia": datos["idempotencia"],
            "ruta": datos.get("ruta"),
            "evento_id": evento["eventId"],
        }
        if "ttlSegundos" in datos:
            mensaje["ttl_segundos"] = datos["ttlSegundos"]
        if "silenciosa" in datos:
            mensaje["silenciosa"] = datos["silenciosa"]

        try:
            self._enviador.enviar(destino, mensaje)
        except TokenInvalido:
            await self._auth.token_invalido(destino["dispositivo_id"])
            logger.info("token invalido; dispositivo marcado en AUTH, push confirmado")
            return "TOKEN_INVALIDO"

        logger.info("push enviado idempotencia=%s", id_opaco(str(datos["idempotencia"])))
        return "ENVIADO"
