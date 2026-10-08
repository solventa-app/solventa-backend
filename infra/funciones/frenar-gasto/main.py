"""Frena el gasto cuando el presupuesto de facturación llega al 100%.

Diseño NUEVO: no existe nada en ``../solventa-arquitectura/proteccion-costos/`` para copiar — se
verificó que esa carpeta no existe en ningún lugar del repo de arquitectura. Este es el patrón
estándar de GCP (presupuesto -> Pub/Sub -> función que reacciona), aplicado aquí por primera vez.

Se activa por el topic de Pub/Sub ``alertas-presupuesto`` que escribe ``google_billing_budget``
(ver ../presupuesto.tf). Acción al llegar al 100%: bloquea TODO el tráfico de entrada de los
servicios de Cloud Run del proyecto (``ingress = INGRESS_TRAFFIC_NONE``).

Deliberadamente NO destructivo: no borra servicios ni datos, solo corta el tráfico entrante (se
revierte a mano con ``gcloud run services update <servicio> --ingress=all``). Y deliberadamente NO
es una garantía absoluta: el costo ya incurrido no se revierte, las notificaciones de presupuesto de
GCP tienen algo de retraso frente al gasto real, y esta función solo actúa sobre Cloud Run (no sobre
Cloud SQL, Memorystore ni Mongo Atlas, que sí pueden seguir cobrando por hora mientras existan).
"""

import base64
import json
import logging
import os

from google.cloud import run_v2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UMBRAL_FRENADO = 1.0  # 100% del presupuesto notificado


def frenar_gasto(cloud_event):
    """Punto de entrada de la Cloud Function (disparada por Pub/Sub, 2a generación)."""
    datos = _decodificar(cloud_event)
    umbral = datos.get("alertThresholdExceeded")
    nombre_presupuesto = datos.get("budgetDisplayName", "desconocido")

    if umbral is None or umbral < UMBRAL_FRENADO:
        logger.info(
            "Alerta de presupuesto '%s' en %s%%: solo informativa, no se frena nada.",
            nombre_presupuesto,
            int((umbral or 0) * 100),
        )
        return

    logger.warning(
        "Presupuesto '%s' alcanzó %s%%: bloqueando el tráfico de entrada de Cloud Run.",
        nombre_presupuesto,
        int(umbral * 100),
    )
    _bloquear_cloud_run()


def _decodificar(cloud_event) -> dict:
    mensaje = cloud_event.data["message"]["data"]
    return json.loads(base64.b64decode(mensaje).decode("utf-8"))


def _bloquear_cloud_run() -> None:
    import google.auth

    _, proyecto = google.auth.default()
    region = os.environ.get("REGION", "us-central1")
    padre = f"projects/{proyecto}/locations/{region}"

    cliente = run_v2.ServicesClient()
    for servicio in cliente.list_services(parent=padre):
        if servicio.ingress == run_v2.IngressTraffic.INGRESS_TRAFFIC_NONE:
            continue
        servicio.ingress = run_v2.IngressTraffic.INGRESS_TRAFFIC_NONE
        cliente.update_service(service=servicio)
        logger.warning("Tráfico de entrada bloqueado: %s", servicio.name)
