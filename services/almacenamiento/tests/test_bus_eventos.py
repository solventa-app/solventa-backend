"""Los eventos cumplen su contrato y el publicador de Pub/Sub nunca tumba la confirmación."""

import base64
import json
from pathlib import Path

from fakes import AHORA
from jsonschema import Draft202012Validator

from app.application.bus_eventos import (
    BusEventosLog,
    BusEventosPubSub,
    construir_bus,
    construir_evento,
)

EVENTOS = Path(__file__).resolve().parents[3] / "contracts" / "events"

ARCHIVO = {
    "id": "arc-1",
    "tipo": "onboarding-video",
    "propietario_tipo": "onboarding",
    "propietario_id": "onb-1",
    "tipo_contenido": "video/mp4",
    "tamano_bytes": 1234,
    "derivado_de": None,
    "motivo": None,
    "creado_en": AHORA,
}


def _validar(evento: dict) -> None:
    esquema = json.loads((EVENTOS / f"{evento['tipo']}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(esquema).validate(evento)


def test_archivo_disponible_cumple_el_contrato() -> None:
    _validar(construir_evento("archivo.disponible", ARCHIVO))
    _validar(construir_evento("archivo.disponible", {**ARCHIVO, "derivado_de": "arc-0"}))


def test_archivo_rechazado_cumple_el_contrato() -> None:
    evento = construir_evento("archivo.rechazado", {**ARCHIVO, "motivo": "TAMANO_DISTINTO"})
    _validar(evento)
    assert "tamanoBytes" not in evento["datos"]


class Respuesta:
    def __init__(self, estado: int) -> None:
        self.status_code = estado


class SesionFalsa:
    def __init__(self, estado: int = 200, falla: bool = False) -> None:
        self.estado = estado
        self.falla = falla
        self.llamadas: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, timeout: float) -> Respuesta:
        if self.falla:
            raise ConnectionError("sin red")
        self.llamadas.append((url, json))
        return Respuesta(self.estado)


def test_pubsub_publica_con_el_atributo_para_filtrar() -> None:
    sesion = SesionFalsa()
    bus = BusEventosPubSub({"archivo.disponible": "projects/p/topics/archivo-disponible"}, sesion)

    bus.publicar(construir_evento("archivo.disponible", ARCHIVO))

    url, cuerpo = sesion.llamadas[0]
    assert url.endswith("projects/p/topics/archivo-disponible:publish")
    mensaje = cuerpo["messages"][0]
    assert mensaje["attributes"]["tipoArchivo"] == "onboarding-video"
    assert json.loads(base64.b64decode(mensaje["data"]))["tipo"] == "archivo.disponible"


def test_pubsub_caido_o_con_error_no_lanza() -> None:
    evento = construir_evento("archivo.disponible", ARCHIVO)
    BusEventosPubSub({"archivo.disponible": "t"}, SesionFalsa(falla=True)).publicar(evento)
    BusEventosPubSub({"archivo.disponible": "t"}, SesionFalsa(estado=403)).publicar(evento)


def test_sin_tema_se_loguea() -> None:
    sesion = SesionFalsa()
    BusEventosPubSub({"archivo.disponible": ""}, sesion).publicar(
        construir_evento("archivo.disponible", ARCHIVO)
    )
    assert sesion.llamadas == []
    assert isinstance(construir_bus("", ""), BusEventosLog)
    assert isinstance(construir_bus("projects/p/topics/a", ""), BusEventosPubSub)
