"""Conciliar y reintentar un cobro pendiente (KAN-28, CA-W05-02).

**Una sola función** para los dos caminos: el bucle automático
(`reintento.ejecutar_una_pasada`) y el botón del back-office
(`ServicioCobros.reintentar`). Así los dos quedan protegidos igual.

Antes de cobrar se **concilia**: se pregunta a la pasarela, vía el ACL Worker,
si ya tiene un cobro con esta `clave_idempotencia`. Es el caso "la pasarela
cobró pero respondió tarde" (el ACL cortó a los 700 ms y PAYMENTS lo registró
`pendiente`): volver a cobrar sería un doble cobro.

- `encontrado`: se toma su estado y su referencia, **sin cobrar**. No cuenta
  intento (no se cobró).
- `no_encontrado`: se cobra con la **misma** clave. Cuenta intento, salvo que el
  motivo sea `circuito_abierto` (ni se llegó a la pasarela).
- `desconocido` (no se pudo preguntar): no se cobra y sigue `pendiente`. No
  cuenta intento: casi siempre es una caída, y contarla agotaría los intentos
  durante la caída.

El evento `cobro.estado-cambiado` se emite solo si el estado cambió."""

import logging
from typing import TYPE_CHECKING, Any

from app.application.bus_eventos import PuertoBusEventos, construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import OrdenCobro, PuertoClienteAcl
from app.application.identificadores import id_opaco

if TYPE_CHECKING:
    from app.application.servicio_cobros import RepositorioCobros

logger = logging.getLogger("payments.conciliacion")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"


def procesar_reintento(
    cobro: dict[str, Any],
    repositorio: "RepositorioCobros",
    cliente_acl: PuertoClienteAcl,
    bus: PuertoBusEventos,
) -> dict[str, Any]:
    """Concilia y, si hace falta, vuelve a cobrar. Devuelve el cobro actualizado."""
    consulta = cliente_acl.consultar(cobro["clave_idempotencia"])

    if consulta.resultado == "encontrado":
        actualizado = repositorio.actualizar_estado(
            cobro["id"], consulta.estado, consulta.referencia, consulta.motivo
        )
        via = "conciliado"
    elif consulta.resultado == "no_encontrado":
        resultado = cliente_acl.cobrar(
            OrdenCobro(
                clave_idempotencia=cobro["clave_idempotencia"],
                poliza_id=cobro["poliza_id"],
                monto=cobro["monto"],
                moneda=cobro["moneda"],
                token_medio_pago=cobro["token_medio_pago"],
            )
        )
        if resultado.motivo == MOTIVO_CIRCUITO_ABIERTO:
            # Ni se intentó: no se gasta un intento (una caída larga no agota el cobro).
            actualizado = repositorio.actualizar_estado(
                cobro["id"], resultado.estado, resultado.referencia, resultado.motivo
            )
        else:
            actualizado = repositorio.actualizar_estado_y_contar_intento(
                cobro["id"], resultado.estado, resultado.referencia, resultado.motivo
            )
        via = "cobrado de nuevo"
    else:
        logger.info(
            "conciliación sin respuesta, sigue pendiente poliza=%s", id_opaco(cobro["poliza_id"])
        )
        return cobro

    logger.info(
        "reintento %s poliza=%s intentos=%s estado=%s",
        via,
        id_opaco(actualizado["poliza_id"]),
        actualizado["intentos"],
        actualizado["estado"],
    )
    if actualizado["estado"] != cobro["estado"]:
        bus.publicar(
            construir_evento_cobro_estado_cambiado(
                cobro_id=actualizado["id"],
                poliza_id=actualizado["poliza_id"],
                estado=actualizado["estado"],
                clave_idempotencia=actualizado["clave_idempotencia"],
            )
        )
    return actualizado
