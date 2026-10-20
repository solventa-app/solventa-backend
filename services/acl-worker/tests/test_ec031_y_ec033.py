"""Pruebas de modificabilidad e integrabilidad del ACL Worker:
- EC031 (HA-MOD-001): reemplazo del adaptador de la pasarela de pagos por un
  `AdaptadorPasarelaAlternativo` que implementa `PuertoPasarelaPagos`, inyectado
  en `ServicioCobro` con 0 líneas modificadas en `ServicioCobro` ni en `PAYMENTS`.
- EC033 (HA-INT-003): incorporación de una sexta fuente (`calificacion-buro-v2`)
  únicamente por configuración (`contracts/fuentes.json` / `FUENTES_CONFIG_PATH`),
  enrutada por el ACL sin tocar el dominio (`app/domain/puertos.py`).
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.circuitos import NOMBRE_CIRCUITO_PASARELA, construir_fabrica
from app.application.configuracion import Configuracion
from app.application.fuentes_config import (
    cargar_fuentes,
    fuentes_open_data,
    fuentes_open_finance,
    todas_las_fuentes,
)
from app.application.servicio_pagos import ServicioCobro
from app.domain.puertos import OrdenCobro, PuertoPasarelaPagos, ResultadoCobro
from app.main import create_app


class AdaptadorPasarelaAlternativo:
    """Segundo proveedor de pasarela (p. ej. migración Wompi -> PayU) que
    implementa `PuertoPasarelaPagos` sin tocar `ServicioCobro` (EC031)."""

    def __init__(self) -> None:
        self.transacciones: dict[str, ResultadoCobro] = {}

    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro:
        _ = timeout_s
        resultado = ResultadoCobro(
            estado="cobrado",
            referencia=f"alt-{orden.id_intento_cobro}",
            detalle={"proveedor": "pasarela-alternativa-v2", "moneda": orden.moneda},
        )
        self.transacciones[orden.id_intento_cobro] = resultado
        return resultado

    def consultar(
        self, referencia_comercio: str, timeout_s: float | None = None
    ) -> ResultadoCobro | None:
        _ = timeout_s
        return self.transacciones.get(referencia_comercio)


def test_ec031_reemplazar_adaptador_pasarela_sin_tocar_servicio_cobro() -> None:
    adaptador_alternativo: PuertoPasarelaPagos = AdaptadorPasarelaAlternativo()
    fabrica = construir_fabrica(umbral_fallos=3, ttl_segundos=10)
    servicio = ServicioCobro(
        adaptador_alternativo,  # type: ignore[arg-type]
        fabrica,
        NOMBRE_CIRCUITO_PASARELA,
        presupuesto_s=0.12,
    )
    orden = OrdenCobro(
        id_intento_cobro="intento-ec031-1",
        poliza_id="pol-ec031",
        monto="210000.00",
        moneda="COP",
        token_medio_pago="tok_pci_alt_99",
    )

    cobro = servicio.cobrar(orden)
    assert cobro.estado == "cobrado"
    assert cobro.referencia == "alt-intento-ec031-1"
    assert cobro.detalle["proveedor"] == "pasarela-alternativa-v2"

    conciliacion = servicio.consultar("intento-ec031-1")
    assert conciliacion.resultado == "encontrado"
    assert conciliacion.cobro == cobro


def test_ec033_sexta_fuente_por_configuracion_se_enruta_sin_tocar_dominio(
    tmp_path: Path,
) -> None:
    catalogo_base = [
        {
            "nombre": f.nombre,
            "origen": f.origen,
            "peso": f.peso,
            "obligatoria": f.obligatoria,
        }
        for f in cargar_fuentes()
    ]
    catalogo_v2 = [
        *catalogo_base,
        {
            "nombre": "calificacion-buro-v2",
            "origen": "OPEN_FINANCE",
            "peso": "0.10",
            "obligatoria": False,
        },
    ]
    archivo_fuentes = tmp_path / "fuentes-v2.json"
    archivo_fuentes.write_text(json.dumps(catalogo_v2), encoding="utf-8")

    assert "calificacion-buro-v2" in fuentes_open_finance(archivo_fuentes)
    assert len(todas_las_fuentes(archivo_fuentes)) == 6
    assert len(fuentes_open_data(archivo_fuentes)) == 3

    config = Configuracion(
        redis_url="redis://localhost:6399/0",
        open_finance_url="http://127.0.0.1:1",
        open_data_url="http://127.0.0.1:1",
        pasarela_url="http://127.0.0.1:1",
        timeout_s=0.2,
        breaker_umbral_fallos=3,
        breaker_ttl_segundos=5,
        sonda_intervalo_segundos=60,
        cache_ttl_segundos=300,
        cola_reconciliacion_redis_url="redis://localhost:6399/2",
        fuentes_config_path=str(archivo_fuentes),
    )

    with TestClient(create_app(config)) as client:
        # La 6ª fuente (`calificacion-buro-v2`) existe en el enrutador por configuración
        # (responde 200 degradado porque el proveedor está apagado, nunca 404).
        r_v2 = client.post(
            "/fuentes/consultar",
            json={
                "cliente_id": "c-1",
                "consentimiento_id": "k-1",
                "fuente": "calificacion-buro-v2",
                "finalidad": "perfilamiento",
            },
        )
        assert r_v2.status_code == 200
        assert r_v2.json()["fuente"] == "calificacion-buro-v2"

        # Y las fuentes v1 siguen funcionando en paralelo sin interrupción:
        r_v1 = client.post(
            "/fuentes/consultar",
            json={
                "cliente_id": "c-1",
                "consentimiento_id": "k-1",
                "fuente": "historial-crediticio",
                "finalidad": "perfilamiento",
            },
        )
        assert r_v1.status_code == 200
        assert r_v1.json()["fuente"] == "historial-crediticio"
