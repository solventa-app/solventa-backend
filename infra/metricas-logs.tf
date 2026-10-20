# Métricas basadas en logs (bloque 1 de docs/plan-completar-hu-w01.md): cuentan eventos de negocio que los
# servicios ya escriben en sus logs, sin código nuevo ni Managed Service for Prometheus (que cobra). Las
# etiquetas son valores acotados (tipo de oferta, estado del cobro, circuito) y nunca llevan datos
# personales (regla #6): los logs ya salen con ids opacos.
#
# Costo: ~$0 (volumen mínimo de staging, dentro de la capa gratuita de Cloud Monitoring).
# Se leen en Grafana (`logging.googleapis.com/user/<nombre>`): paneles de hu-w01.json y hu-w05.json.
#
# Gate: var.crear_metricas_logs (default false).
#
# Los filtros asumen que Cloud Run entrega el log de Python como texto (`textPayload`, formato
# "NIVEL logger: mensaje"). Verificar contra staging tras aplicar: Logs Explorer con el mismo filtro.

resource "google_logging_metric" "ofertas_por_tipo" {
  count       = var.crear_metricas_logs ? 1 : 0
  project     = var.project_id
  name        = "solventa_ofertas_por_tipo"
  description = "Ofertas entregadas por el BFF, por tipo (COMPLETA o PRELIMINAR)."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"bff\" AND textPayload:\"oferta entregada\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key         = "tipo"
      value_type  = "STRING"
      description = "COMPLETA o PRELIMINAR"
    }
  }
  label_extractors = {
    "tipo" = "REGEXP_EXTRACT(textPayload, \"tipo=([A-Z]+)\")"
  }
}

resource "google_logging_metric" "circuito_abierto" {
  count       = var.crear_metricas_logs ? 1 : 0
  project     = var.project_id
  name        = "solventa_circuito_abierto"
  description = "Llamadas a un proveedor rechazadas de inmediato por tener el circuito abierto, por circuito."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"acl-worker\" AND textPayload:\"abierto, fail-fast\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key         = "circuito"
      value_type  = "STRING"
      description = "Circuito del proveedor (open-finance, open-data, pasarela)"
    }
  }
  label_extractors = {
    "circuito" = "REGEXP_EXTRACT(textPayload, \"circuito=([a-z-]+)\")"
  }
}

resource "google_logging_metric" "cobros_por_estado" {
  count       = var.crear_metricas_logs ? 1 : 0
  project     = var.project_id
  name        = "solventa_cobros_por_estado"
  description = "Cambios de estado de cobros registrados por PAYMENTS, por estado resultante."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"payments\" AND textPayload:\"cobro actualizado\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key         = "estado"
      value_type  = "STRING"
      description = "cobrado, rechazado o pendiente"
    }
  }
  label_extractors = {
    "estado" = "REGEXP_EXTRACT(textPayload, \"estado=([a-z]+)\")"
  }
}
