"""Tarea de reconciliación diferida, ejecutada por `rq.SimpleWorker` (ver
`app/worker.py`). Vuelve a llamar al ACL Worker por HTTP — nunca al proveedor
directo (regla de arquitectura #2): el ACL Worker sigue siendo el único punto
de contacto con proveedores externos.

Contrato (traducido del Consolidador KYC del Experimento 1, Node.js/BullMQ, a
Python/`rq`; ver `services/acl-worker/app/application/reconciliacion.py` para
el lado productor y la nota de alcance en `docs/sprint-1.md`):

- Si la respuesta vuelve fresca (`degradado=False` y `de_cache=False`), el job
  terminó bien: `ServicioConsultaFuentes.consultar()` ya re-guarda la caché en
  su propio camino de éxito al atender esta misma llamada, así que la próxima
  consulta normal del usuario ya la hereda — este módulo no persiste nada
  aparte.
- Si sigue degradada (o el ACL Worker no responde), se lanza una excepción
  para que `rq` reintente con el backoff configurado por el productor;
  agotados los reintentos, `rq` mueve el job al `FailedJobRegistry`
  (equivalente a la DLQ del diseño original) — no se reintenta indefinidamente
  ni queda oculto.

Antes de volver a consultar, verifica en AUTH que el consentimiento siga
vigente (regla de arquitectura #7): si se revocó (o no existe), el job termina
sin consultar y sin reintentar. Si AUTH no responde, se lanza para que `rq`
reintente más tarde — nunca se consulta un proveedor sin haber confirmado el
consentimiento.

Fuera de alcance de este módulo (decisión de negocio, no de este Consolidador,
igual que en el diseño original de KYC): si RISK/RATING ya mostró/persistió
una oferta preliminar con el dato degradado, decidir si debe recalcularse esa
oferta ya mostrada una vez que aquí se consigue el dato fresco.
"""

import logging
import os

import httpx

from app.autenticacion_servicios import autenticacion_para, modo_desde_entorno
from app.identificadores import id_opaco

logger = logging.getLogger("consolidador_fuentes.tareas")

ACL_URL = os.getenv("ACL_URL", "http://localhost:8005")
AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8001")
# Más holgado que el presupuesto de 700 ms del camino síncrono (EC009/EC010):
# esta llamada corre fuera del camino de usuario, en un job de fondo.
TIMEOUT_S = float(os.getenv("ACL_TIMEOUT_S", "3"))
# Token de identidad hacia AUTH y el ACL Worker en Cloud Run (servicios privados); nada en local.
_MODO_AUTENTICACION = modo_desde_entorno()
AUTH_HACIA_AUTH = autenticacion_para(_MODO_AUTENTICACION, "auth")
AUTH_HACIA_ACL = autenticacion_para(_MODO_AUTENTICACION, "acl-worker")


class FuenteSigueDegradadaError(Exception):
    """Se lanza cuando el ACL Worker respondió, pero la fuente sigue sin dato
    fresco (`de_cache` o `degradado`) — señal para que `rq` reintente."""


def consentimiento_vigente(consentimiento_id: str) -> bool:
    """`False` si AUTH dice que no existe (404) o que ya no está vigente. Cualquier
    otra falla de AUTH se propaga: sin confirmación no se consulta."""
    respuesta = httpx.get(
        f"{AUTH_URL}/consentimientos/{consentimiento_id}", timeout=TIMEOUT_S, auth=AUTH_HACIA_AUTH
    )
    if respuesta.status_code == 404:
        return False
    respuesta.raise_for_status()
    return bool(respuesta.json().get("vigente"))


def reconciliar_fuente(
    fuente: str, cliente_id: str, consentimiento_id: str, encolado_en: float
) -> None:
    cliente_opaco = id_opaco(cliente_id)
    if not consentimiento_vigente(consentimiento_id):
        logger.info(
            "reconciliacion fuente=%s cliente=%s: consentimiento no vigente, se descarta",
            fuente,
            cliente_opaco,
        )
        return

    respuesta = httpx.post(
        f"{ACL_URL}/fuentes/consultar",
        json={
            "cliente_id": cliente_id,
            "consentimiento_id": consentimiento_id,
            "fuente": fuente,
        },
        timeout=TIMEOUT_S,
        auth=AUTH_HACIA_ACL,
    )
    respuesta.raise_for_status()
    cuerpo = respuesta.json()

    if cuerpo["de_cache"] or cuerpo["degradado"]:
        logger.info(
            "reconciliacion fuente=%s cliente=%s: aun degradada, rq reintentara",
            fuente,
            cliente_opaco,
        )
        raise FuenteSigueDegradadaError(fuente)

    logger.info(
        "reconciliacion fuente=%s cliente=%s: dato fresco obtenido, job resuelto",
        fuente,
        cliente_opaco,
    )
