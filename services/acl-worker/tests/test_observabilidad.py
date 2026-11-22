"""Las trazas están apagadas por defecto y nunca tumban el servicio."""

import json
import logging

from app import observabilidad


def test_apagadas_por_defecto(monkeypatch):
    monkeypatch.delenv("TRAZAS_GCP", raising=False)
    assert observabilidad.configurar_trazas("prueba", ("httpx",)) is False


def test_un_fallo_al_configurar_no_tumba_el_servicio(monkeypatch):
    monkeypatch.setenv("TRAZAS_GCP", "true")

    def sin_credenciales(*args, **kwargs):
        raise RuntimeError("sin credenciales")

    monkeypatch.setattr("google.auth.default", sin_credenciales)
    assert observabilidad.configurar_trazas("prueba") is False


def test_log_en_json_con_el_id_de_traza():
    from opentelemetry.sdk.trace import TracerProvider

    formato = observabilidad.FormatoJsonConTraza("mi-proyecto")
    registro = logging.LogRecord(
        "app.prueba", logging.WARNING, __file__, 1, "hola %s", ("mundo",), None
    )
    sin_traza = json.loads(formato.format(registro))
    assert sin_traza == {"severity": "WARNING", "message": "app.prueba: hola mundo"}

    with TracerProvider().get_tracer("prueba").start_as_current_span("tramo"):
        con_traza = json.loads(formato.format(registro))
    assert con_traza["logging.googleapis.com/trace"].startswith("projects/mi-proyecto/traces/")
    assert len(con_traza["logging.googleapis.com/spanId"]) == 16


# ---- las claves que viajan en la URL no llegan a Cloud Logging ni a Cloud Trace ----

URL_CON_CLAVE = "http://api.aviationstack.com/v1/flights?access_key=CLAVE-SECRETA&limit=100"


def test_ocultar_secretos_solo_toca_el_valor_de_las_claves():
    limpia = observabilidad.ocultar_secretos(URL_CON_CLAVE)
    assert limpia == "http://api.aviationstack.com/v1/flights?access_key=***&limit=100"
    otra = "https://x.test/p?flight_iata=AV9340&api_key=abc&flight_date=2026-11-22"
    assert (
        observabilidad.ocultar_secretos(otra)
        == "https://x.test/p?flight_iata=AV9340&api_key=***&flight_date=2026-11-22"
    )
    assert observabilidad.ocultar_secretos("sin parámetros") == "sin parámetros"


def test_el_filtro_limpia_el_log_de_httpx_como_lo_escribe_httpx():
    # httpx escribe: 'HTTP Request: %s %s "%s %d %s"' con el método, la URL y el estado.
    registro = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        ("GET", URL_CON_CLAVE, "HTTP/1.1", 429, "Too Many Requests"),
        None,
    )
    assert observabilidad.FiltroSinSecretos().filter(registro) is True
    mensaje = registro.getMessage()
    assert "CLAVE-SECRETA" not in mensaje
    assert "access_key=***&limit=100" in mensaje and "429" in mensaje


def test_el_filtro_deja_igual_lo_que_no_tiene_claves_y_no_falla_con_args_malos():
    sano = logging.LogRecord("x", logging.INFO, __file__, 1, "hola %s", ("mundo",), None)
    observabilidad.FiltroSinSecretos().filter(sano)
    assert sano.getMessage() == "hola mundo"
    malo = logging.LogRecord("x", logging.INFO, __file__, 1, "hola %s %s", ("solo uno",), None)
    assert observabilidad.FiltroSinSecretos().filter(malo) is True


def test_proteger_logs_pone_el_filtro_una_sola_vez():
    manejador = logging.StreamHandler()
    raiz = logging.getLogger()
    raiz.addHandler(manejador)
    try:
        observabilidad.proteger_logs()
        observabilidad.proteger_logs()
        assert sum(isinstance(f, observabilidad.FiltroSinSecretos) for f in manejador.filters) == 1
    finally:
        raiz.removeHandler(manejador)


def test_el_tramo_de_httpx_no_lleva_la_clave():
    from opentelemetry.sdk.trace import TracerProvider

    with (
        TracerProvider()
        .get_tracer("prueba")
        .start_as_current_span(
            "GET", attributes={"http.url": URL_CON_CLAVE, "http.method": "GET"}
        ) as tramo
    ):
        observabilidad._sin_secretos_en_el_tramo(tramo, None)
        assert "CLAVE-SECRETA" not in tramo.attributes["http.url"]
        assert tramo.attributes["http.url"].endswith("access_key=***&limit=100")
        assert tramo.attributes["http.method"] == "GET"
