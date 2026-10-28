import json
import logging
from pathlib import Path

from fakes import AHORA, PaymentsFalso, PolicyPolizas, repositorio_con_datos

from app.application.bus_eventos import (
    ESQUEMAS,
    BusEventosLog,
    evento_aprobado,
    evento_derivado,
    evento_rechazado,
)
from app.application.servicio_siniestros import ServicioSiniestros

CONTRATOS = Path(__file__).resolve().parents[3] / "contracts" / "events"
SINIESTRO = {"id": "sin-01", "radicado": "SIN-2026-0141", "poliza_id": "pol-01"}


class BusQueGuarda(BusEventosLog):
    def __init__(self):
        super().__init__()
        self.publicados = []

    def publicar(self, evento):
        publicado = super().publicar(evento)
        if publicado:
            self.publicados.append(evento)
        return publicado


def test_las_copias_de_la_imagen_son_iguales_al_contrato():
    copias = {p.name: p.read_bytes() for p in ESQUEMAS.glob("siniestro.*.schema.json")}
    fuentes = {p.name: p.read_bytes() for p in CONTRATOS.glob("siniestro.*.schema.json")}
    assert copias == fuentes and len(copias) == 3


def test_los_tres_eventos_cumplen_su_esquema_y_se_publican():
    bus = BusEventosLog()
    resolucion = {
        "resuelta_en": AHORA,
        "indemnizacion": {"valor": "8650000", "moneda": "COP"},
        "orden_pago_id": "ind-1",
        "motivo": "OTRO",
    }
    asignacion = {"asignada_en": AHORA, "perito_id": "per-01", "vence_informe_en": AHORA}
    assert bus.publicar(evento_aprobado(SINIESTRO, resolucion, "u-1"))
    assert bus.publicar(evento_rechazado(SINIESTRO, resolucion, "u-1"))
    assert bus.publicar(evento_derivado(SINIESTRO, asignacion, None))


def test_un_evento_invalido_no_se_publica_y_no_filtra_el_valor(caplog):
    evento = evento_rechazado(SINIESTRO, {"resuelta_en": AHORA, "motivo": "OTRO"}, "u-1")
    evento["datos"]["comentario"] = (
        "Camilo Rojas, C.C. 79.845.210"  # campo que el esquema no admite
    )
    with caplog.at_level(logging.ERROR, logger="claims.eventos"):
        assert BusEventosLog().publicar(evento) is False
    assert "no se publica" in caplog.text
    assert "Camilo" not in caplog.text


def test_un_tipo_sin_esquema_no_se_publica():
    assert BusEventosLog().publicar({"tipo": "siniestro.inventado", "datos": {}}) is False


def test_el_servicio_publica_un_evento_por_resolucion_sin_datos_personales():
    bus = BusQueGuarda()
    servicio = ServicioSiniestros(
        repositorio_con_datos(), lambda: AHORA, PolicyPolizas(), PaymentsFalso(), bus
    )
    servicio.derivar("sin-01", "per-01", "Revisar el bumper", "u-1")
    servicio.aprobar("sin-01", "u-1")
    servicio.rechazar("sin-02", "OTRO", "Comentario privado", "u-1")
    assert [e["tipo"] for e in bus.publicados] == [
        "siniestro.derivado",
        "siniestro.aprobado",
        "siniestro.rechazado",
    ]
    texto = json.dumps(bus.publicados, ensure_ascii=False)
    for dato in ("Camilo", "79.845.210", "Revisar el bumper", "Comentario privado", "Calle 80"):
        assert dato not in texto
