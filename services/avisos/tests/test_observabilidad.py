"""Las trazas están apagadas por defecto y nunca tumban el servicio."""

import json
import logging

from app import observabilidad


def test_apagadas_por_defecto(monkeypatch):
    monkeypatch.delenv("TRAZAS_GCP", raising=False)
    assert observabilidad.configurar_trazas("prueba", ()) is False


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
