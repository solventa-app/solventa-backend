"""Trazas distribuidas (OpenTelemetry) hacia Cloud Trace y logs con el id de traza.

APAGADO por defecto: solo se activa con `TRAZAS_GCP=true` (lo pone Terraform en Cloud Run desde
`.github/servicios.json`). En local y en CI no se importa nada de OpenTelemetry y los logs no
cambian.

Qué se traza:
- La petición entrante (la telemetría nativa de FastAPI): un tramo de servidor por petición, con
  la ruta con plantilla, no con los datos.
- Las llamadas `httpx` salientes, que propagan el contexto (`traceparent`) al siguiente servicio:
  así un flujo, por ejemplo una oferta, se ve de punta a punta desde el BFF hasta las fuentes.
- Según el servicio, Postgres (`psycopg`), MongoDB (`pymongo`) y Redis.

Regla 6 (sin datos personales): los tramos de base de datos no llevan valores. `psycopg` no
captura parámetros, `pymongo` no captura la sentencia (`capture_statement=False`) y `redis`
enmascara los argumentos (`GET ?`). La ruta real solo trae identificadores opacos, nunca un
documento.

Los tramos se envían por OTLP/HTTP a la API de Telemetry de Google con la identidad de la cuenta
de servicio de Cloud Run (rol `roles/telemetry.tracesWriter`). Un fallo al configurar se registra
y el servicio sigue sin trazas: nunca lo tumba.

Nota: Cloud Run limita la CPU entre peticiones, así que el envío en segundo plano puede
retrasarse hasta la siguiente petición o hasta que la instancia se apague (se vacía al cerrar)."""

import json
import logging
import os
import re

logger = logging.getLogger("observabilidad")

ENDPOINT_TRAZAS = "https://telemetry.googleapis.com/v1/traces"

# Claves que viajan en la URL (AviationStack exige `access_key` como parámetro de consulta). El log INFO de
# `httpx` y el atributo de URL de los tramos de OpenTelemetry (que va a Cloud Trace) escriben la URL completa:
# sin esto la clave queda en claro en Cloud Logging y Cloud Trace.
_PARAMETRO_SECRETO = re.compile(
    r"([?&](?:access_key|api_key|apikey|key|token|secret|password|client_secret)=)[^&\s\"']+",
    re.IGNORECASE,
)


def ocultar_secretos(texto: str) -> str:
    """Reemplaza el valor de los parámetros de URL que son claves por `***`."""
    return _PARAMETRO_SECRETO.sub(r"\1***", texto)


class FiltroSinSecretos(logging.Filter):
    """Oculta las claves de las URLs en cualquier mensaje de log antes de que se escriba."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            mensaje = record.getMessage()
        except Exception:  # un mensaje mal formado no debe tumbar el log
            return True
        limpio = ocultar_secretos(mensaje)
        if limpio != mensaje:
            record.msg, record.args = limpio, None
        return True


def proteger_logs() -> None:
    """Pone el filtro en los manejadores del logger raíz (llamar después de `logging.basicConfig`)."""
    for manejador in logging.getLogger().handlers:
        if not any(isinstance(f, FiltroSinSecretos) for f in manejador.filters):
            manejador.addFilter(FiltroSinSecretos())


def _sin_secretos_en_el_tramo(span, request) -> None:
    """Hook de la instrumentación de httpx: reescribe los atributos de URL del tramo sin las claves."""
    atributos = getattr(span, "attributes", None) or {}
    for clave in ("http.url", "url.full"):
        valor = atributos.get(clave)
        if isinstance(valor, str) and ocultar_secretos(valor) != valor:
            span.set_attribute(clave, ocultar_secretos(valor))


class FormatoJsonConTraza(logging.Formatter):
    """Una línea JSON por log, con los campos con que Cloud Logging enlaza el log con su traza."""

    def __init__(self, proyecto: str | None) -> None:
        super().__init__()
        self._proyecto = proyecto

    def format(self, record: logging.LogRecord) -> str:
        from opentelemetry import trace

        mensaje = f"{record.name}: {record.getMessage()}"
        if record.exc_info:
            mensaje += "\n" + self.formatException(record.exc_info)
        linea: dict[str, object] = {"severity": record.levelname, "message": mensaje}
        contexto = trace.get_current_span().get_span_context()
        if contexto.is_valid and self._proyecto:
            traza = f"projects/{self._proyecto}/traces/{contexto.trace_id:032x}"
            linea["logging.googleapis.com/trace"] = traza
            linea["logging.googleapis.com/spanId"] = f"{contexto.span_id:016x}"
            linea["logging.googleapis.com/trace_sampled"] = contexto.trace_flags.sampled
        return json.dumps(linea, ensure_ascii=False)


def _instrumentar(nombre: str) -> None:
    if nombre == "httpx":
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(request_hook=_sin_secretos_en_el_tramo)
    elif nombre == "psycopg":
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(capture_parameters=False)
    elif nombre == "pymongo":
        from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

        PymongoInstrumentor().instrument(capture_statement=False)
    elif nombre == "redis":
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    else:
        raise ValueError(f"instrumento desconocido: {nombre}")


def configurar_trazas(servicio: str, instrumentos: tuple[str, ...] = ()) -> bool:
    """Activa las trazas si `TRAZAS_GCP=true`. Devuelve `True` si quedaron activas."""
    if os.getenv("TRAZAS_GCP", "").strip().lower() != "true":
        return False
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        credenciales, detectado = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        proyecto = os.getenv("GCP_PROYECTO") or detectado
        recurso = Resource.create(
            {
                "service.name": servicio,
                "service.version": os.getenv("K_REVISION", "local"),
                "gcp.project_id": proyecto,
            }
        )
        # Muestreo por id de traza (determinista): todos los servicios deciden lo mismo para la
        # misma traza. Se ignora la bandera del padre a propósito: Cloud Run marca como no
        # muestreada casi toda petición entrante.
        muestreo = float(os.getenv("TRAZAS_MUESTREO", "1.0"))
        proveedor = TracerProvider(resource=recurso, sampler=TraceIdRatioBased(muestreo))
        sesion = AuthorizedSession(credenciales)
        exportador = OTLPSpanExporter(endpoint=ENDPOINT_TRAZAS, session=sesion)
        proveedor.add_span_processor(
            BatchSpanProcessor(exportador, schedule_delay_millis=1000, max_export_batch_size=64)
        )
        trace.set_tracer_provider(proveedor)
        for nombre in instrumentos:
            _instrumentar(nombre)
        formato = FormatoJsonConTraza(proyecto)
        for manejador in logging.getLogger().handlers:
            manejador.setFormatter(formato)
    except Exception:  # las trazas nunca deben tumbar el servicio
        logger.exception("no se pudieron activar las trazas; el servicio sigue sin ellas")
        return False
    detalle = ",".join(instrumentos) or "ninguno"
    logger.info("trazas activadas servicio=%s instrumentos=%s", servicio, detalle)
    return True
