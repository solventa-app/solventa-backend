"""KAN-290: tarifa y oferta de la póliza de auto."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.application.oferta_auto import ServicioOfertaAuto
from app.application.tarifa_auto import (
    DatosVehiculoInsuficientes,
    VehiculoNoAsegurable,
    VehiculoNoEncontrado,
    cotizar,
)
from app.main import create_app

ANIO = 2026
COMPLETO = {"runt-vehiculo": "OK", "fasecolda-valor": "OK", "siniestralidad-placa": "OK"}


def resumen(**cambios):
    """El Mazda 3 2024 de la placa de demo ABC123, como lo guarda RISK."""
    base = {
        "encontrado": True,
        "completo": True,
        "fuentes": dict(COMPLETO),
        "marca": "MAZDA",
        "linea": "3 GRAND TOURING",
        "modelo": 2024,
        "clase": "AUTOMOVIL",
        "servicio": "PARTICULAR",
        "estado": "ACTIVO",
        "odometro_km": 18500,
        "valor_comercial": "118500000",
        "indice_hurto": "MEDIO",
        "siniestros_3_anios": 0,
    }
    return {**base, **cambios}


def cotizar_base(r, km=12000, ciudad="Bogotá", uso="PARTICULAR", deducible="DIEZ_POR_CIENTO"):
    return cotizar(r, km, ciudad, uso, deducible, ANIO)


def test_prima_del_vehiculo_nuevo_sin_siniestros() -> None:
    c = cotizar_base(resumen())
    # 118.500.000 × 3,2 % × uso 1 × antigüedad 0,95 × km 1 × hurto 1 × sin siniestros 0,90
    # × Bogotá 1,10 × deducible 1 + RCE 420.000 = 3.986.376 al año → 332.198,00 al mes.
    assert c.tipo == "COMPLETA"
    assert c.prima_mensual == Decimal("332198.00")
    assert c.requiere_revision is False
    assert [f["nombre"] for f in c.factores] == [
        "USO", "ANTIGUEDAD", "KILOMETRAJE", "INDICE_HURTO", "SINIESTRALIDAD", "CIUDAD", "DEDUCIBLE",
    ]  # fmt: skip
    tipos = [cob["tipo"] for cob in c.coberturas]
    assert tipos[0] == "RESPONSABILIDAD_CIVIL" and tipos[-1] == "ASISTENCIA" and len(tipos) == 6
    assert c.coberturas[1]["deducible"] == "10 % mín. 1 SMMLV"


def test_cada_factor_mueve_la_prima_en_su_direccion() -> None:
    base = cotizar_base(resumen()).prima_mensual
    assert cotizar_base(resumen(indice_hurto="MUY_ALTO")).prima_mensual > base
    assert cotizar_base(resumen(siniestros_3_anios=2)).prima_mensual > base
    assert cotizar_base(resumen(), ciudad="Tunja").prima_mensual < base
    assert cotizar_base(resumen(), deducible="SIN_DEDUCIBLE").prima_mensual > base
    assert cotizar_base(resumen(), km=35000).prima_mensual > base
    assert cotizar_base(resumen(), uso="COMERCIAL").prima_mensual > base
    assert cotizar_base(resumen(modelo=2012)).prima_mensual > cotizar_base(
        resumen(modelo=2012, valor_comercial="118500000", odometro_km=None)
    ).prima_mensual * Decimal("0.99")


def test_el_odometro_corrige_un_kilometraje_declarado_bajo() -> None:
    # 164.200 km en 14 años = 11.728 km/año: pesa más que los 5.000 declarados.
    c = cotizar_base(resumen(modelo=2012, odometro_km=164200), km=5000)
    km = next(f for f in c.factores if f["nombre"] == "KILOMETRAJE")
    assert km["factor"] == 1.0 and km["detalle"] == "11.728 km/año"


def test_servicio_publico_en_runt_tarifa_como_comercial() -> None:
    c = cotizar_base(resumen(servicio="PUBLICO"))
    assert next(f for f in c.factores if f["nombre"] == "USO")["factor"] == 1.15


def test_tres_siniestros_requieren_revision() -> None:
    assert cotizar_base(resumen(siniestros_3_anios=3)).requiere_revision is True


def test_sin_fasecolda_la_oferta_es_preliminar_con_valor_de_referencia() -> None:
    c = cotizar_base(resumen(valor_comercial=None, fuentes={"fasecolda-valor": "FALLO"}))
    assert c.tipo == "PRELIMINAR" and c.valor_estimado is True
    assert c.valor_comercial == Decimal("64800000")  # 80 M × 0,9² (2 años)


def test_sin_siniestralidad_tarifa_neutra_y_preliminar() -> None:
    c = cotizar_base(resumen(siniestros_3_anios=None))
    assert c.tipo == "PRELIMINAR"
    assert next(f for f in c.factores if f["nombre"] == "SINIESTRALIDAD")["factor"] == 1.0


@pytest.mark.parametrize(
    ("cambios", "texto"),
    [
        ({"estado": "CON_LIMITACION"}, "limitación"),
        ({"estado": "HURTADO"}, "hurto"),
        ({"modelo": 2008}, "más de 15 años"),
    ],
)
def test_vehiculos_no_asegurables(cambios, texto) -> None:
    with pytest.raises(VehiculoNoAsegurable, match=texto):
        cotizar_base(resumen(**cambios))


def test_placa_inexistente_y_runt_caido() -> None:
    with pytest.raises(VehiculoNoEncontrado):
        cotizar_base(resumen(encontrado=False))
    with pytest.raises(DatosVehiculoInsuficientes):
        cotizar_base(resumen(clase=None, modelo=None))


class LectorFalso:
    def __init__(self, perfil):
        self.perfil = perfil

    def leer(self, vehiculo_id, operation_time):
        return self.perfil


class AlmacenFalso:
    def __init__(self):
        self.guardadas = []

    def guardar(self, oferta, cliente_id, consentimiento_id, plazo_meses):
        self.guardadas.append((oferta["id"], cliente_id, consentimiento_id, plazo_meses))


CUERPO = {
    "cliente_id": "titular-1",
    "vehiculo_id": "veh-1",
    "operation_time": "1794327276.2",
    "consentimiento_id": "k-1",
    "placa_enmascarada": "ABC•••",
    "kilometraje_anual": 12000,
    "ciudad_circulacion": "Bogotá",
}


def cliente_con(perfil, almacen=None):
    servicio = ServicioOfertaAuto(LectorFalso(perfil), anio_actual=ANIO)
    app = create_app(
        lector_perfiles=LectorFalso(None),  # type: ignore[arg-type]
        servicio_oferta=object(),  # type: ignore[arg-type]
        cache_score=object(),  # type: ignore[arg-type]
        almacen_ofertas=almacen or AlmacenFalso(),  # type: ignore[arg-type]
        servicio_oferta_auto=servicio,
    )
    return TestClient(app)


def perfil(**cambios):
    return {"vehiculo_id": "veh-1", "titular_id": "titular-1", "perfil_version": 1,
            "resumen": resumen(**cambios)}  # fmt: skip


def test_endpoint_devuelve_la_oferta_y_la_guarda_para_aceptarla() -> None:
    almacen = AlmacenFalso()
    with cliente_con(perfil(), almacen) as client:
        r = client.post("/ofertas/auto", json=CUERPO)
    assert r.status_code == 200
    oferta = r.json()
    assert oferta["producto"] == "AUTO" and oferta["tipo"] == "COMPLETA"
    assert oferta["prima"] == {"valor": "332198.00", "moneda": "COP"}
    assert oferta["cobertura"] == {"valor": "118500000.00", "moneda": "COP"}
    assert oferta["vehiculo"]["placaEnmascarada"] == "ABC•••"
    assert oferta["desgloseScore"] == [] and len(oferta["factoresTarifa"]) == 7
    assert {f["estado"] for f in oferta["fuentes"]} == {"OK"}
    assert almacen.guardadas == [(oferta["id"], "titular-1", "k-1", 12)]


def test_endpoint_preliminar_trae_rango() -> None:
    with cliente_con(perfil(valor_comercial=None)) as client:
        oferta = client.post("/ofertas/auto", json=CUERPO).json()
    assert oferta["tipo"] == "PRELIMINAR" and oferta["prima"] is None
    assert set(oferta["rangoPrima"]) == {"minima", "maxima"}


@pytest.mark.parametrize(
    ("cambios", "esperado"),
    [
        ({"encontrado": False}, "VEHICULO_NO_ENCONTRADO"),
        ({"estado": "HURTADO"}, "VEHICULO_NO_ASEGURABLE"),
    ],
)
def test_endpoint_errores_de_negocio_son_422_con_motivo(cambios, esperado) -> None:
    with cliente_con(perfil(**cambios)) as client:
        r = client.post("/ofertas/auto", json=CUERPO)
    assert r.status_code == 422
    assert r.json()["detail"]["motivo"] == esperado
    assert r.json()["detail"]["mensaje"]


def test_endpoint_sin_perfil_404_y_runt_caido_datos_insuficientes() -> None:
    with cliente_con(None) as client:
        assert client.post("/ofertas/auto", json=CUERPO).status_code == 404
    with cliente_con(perfil(clase=None, modelo=None)) as client:
        r = client.post("/ofertas/auto", json=CUERPO)
    assert r.status_code == 422 and r.json()["detail"] == "datos_insuficientes"
