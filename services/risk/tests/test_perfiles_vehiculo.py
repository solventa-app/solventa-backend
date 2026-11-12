"""KAN-290: el perfil de riesgo del vehículo (resumen, cifrado, consentimiento y endpoint)."""

import json
from datetime import UTC, datetime
from typing import Any

from fakes import TimestampFalso
from fastapi.testclient import TestClient
from test_cifrado_perfiles import CIFRADO_CLAVE_LOCAL, ClienteMongoFalso

from app.application.cifrado import CifradorAesGcm
from app.application.perfiles_vehiculo import (
    COLECCION,
    RepositorioPerfilesVehiculoMongo,
    ServicioPerfilesVehiculo,
    resumir,
)
from app.application.repositorio_perfiles import NOMBRE_BASE
from app.main import create_app


def fuente(nombre: str, datos: dict, de_cache: bool = False, degradado: bool = False) -> dict:
    return {
        "fuente": nombre,
        "datos": datos,
        "capturado_en": "2026-11-10T12:00:00+00:00",
        "de_cache": de_cache,
        "degradado": degradado,
    }


RUNT = fuente(
    "runt-vehiculo",
    {"encontrada": True, "marca": "MAZDA", "linea": "3", "modelo": 2024, "clase": "AUTOMOVIL",
     "servicio": "PARTICULAR", "estado": "ACTIVO", "odometro_km": 18500},
)  # fmt: skip
FASECOLDA = fuente(
    "fasecolda-valor",
    {"encontrada": True, "valor_comercial": "118500000", "indice_hurto": "MEDIO"},
)
SINIESTRALIDAD = fuente("siniestralidad-placa", {"encontrada": True, "siniestros_3_anios": 1})


def test_resumen_completo_con_las_tres_fuentes() -> None:
    r = resumir([RUNT, FASECOLDA, SINIESTRALIDAD])
    assert r["completo"] is True and r["encontrado"] is True
    assert (r["clase"], r["modelo"], r["valor_comercial"]) == ("AUTOMOVIL", 2024, "118500000")
    assert (r["indice_hurto"], r["siniestros_3_anios"]) == ("MEDIO", 1)
    assert r["fuentes"] == {
        "runt-vehiculo": "OK",
        "fasecolda-valor": "OK",
        "siniestralidad-placa": "OK",
    }


def test_sin_fasecolda_es_incompleto_y_sin_siniestralidad_sigue_completo() -> None:
    caida = fuente("fasecolda-valor", {}, degradado=True)
    assert resumir([RUNT, caida, SINIESTRALIDAD])["completo"] is False
    sin_historial = resumir([RUNT, FASECOLDA, fuente("siniestralidad-placa", {}, degradado=True)])
    assert sin_historial["completo"] is True
    assert sin_historial["siniestros_3_anios"] is None
    desde_cache = resumir([fuente("runt-vehiculo", RUNT["datos"], de_cache=True), FASECOLDA])
    assert desde_cache["completo"] is True
    assert desde_cache["fuentes"]["runt-vehiculo"] == "CACHE"


def test_placa_inexistente_no_es_lo_mismo_que_una_falla() -> None:
    no_existe = resumir([fuente("runt-vehiculo", {"encontrada": False}), FASECOLDA])
    assert no_existe["encontrado"] is False and no_existe["completo"] is False
    caido = resumir([fuente("runt-vehiculo", {}, degradado=True), FASECOLDA])
    assert caido["encontrado"] is True and caido["completo"] is False


def test_en_mongo_no_queda_nada_del_vehiculo_en_claro() -> None:
    """Regla 6 / EC022: el resumen y las respuestas de las fuentes van cifrados."""
    cliente = ClienteMongoFalso()
    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    repo = RepositorioPerfilesVehiculoMongo(cliente, cifrador)
    servicio = ServicioPerfilesVehiculo(repo)

    r1 = servicio.registrar("veh-1", "titular-1", "k-1", [RUNT, FASECOLDA, SINIESTRALIDAD])
    r2 = servicio.registrar("veh-1", "titular-1", "k-1", [RUNT, FASECOLDA, SINIESTRALIDAD])
    assert (r1["perfil_version"], r2["perfil_version"]) == (1, 2)
    assert r1["completo"] is True

    doc = cliente[NOMBRE_BASE][COLECCION].documentos[0]
    crudo = json.dumps(doc, default=str)
    for valor in ("MAZDA", "118500000", "AUTOMOVIL", "18500"):
        assert valor not in crudo
    assert json.loads(cifrador.descifrar(doc["resumen_cifrado"]))["marca"] == "MAZDA"
    assert all(f["datos"] == {} for f in doc["fuentes"])


class RepositorioFalso:
    def __init__(self) -> None:
        self.guardados: list[dict[str, Any]] = []
        self.revocados: list[tuple[str, str]] = []

    def guardar(self, perfil: dict[str, Any]) -> tuple[int, TimestampFalso]:
        self.guardados.append(perfil)
        return len(self.guardados), TimestampFalso(1700000000, 3)

    def borrar_de_consentimiento(self, titular_id: str, consentimiento_id: str) -> int:
        self.revocados.append((titular_id, consentimiento_id))
        return 1

    def borrar_vencidos(self, ahora: datetime) -> int:
        return 2


class ServicioFinancieroFalso:
    def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> int:
        return 0

    def purgar_vencidos(self) -> int:
        return 1


def test_endpoint_revocacion_y_purga_incluyen_el_vehiculo() -> None:
    repo = RepositorioFalso()
    app = create_app(
        servicio_perfiles=ServicioFinancieroFalso(),  # type: ignore[arg-type]
        servicio_vehiculos=ServicioPerfilesVehiculo(repo),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        r = client.post(
            "/vehiculos/perfiles",
            json={
                "vehiculo_id": "veh-1",
                "titular_id": "titular-1",
                "consentimiento_id": "k-1",
                "consentimiento_vence_en": datetime(2026, 12, 10, tzinfo=UTC).isoformat(),
                "fuentes": [RUNT, FASECOLDA],
            },
        )
        assert r.status_code == 200
        assert r.json() == {
            "vehiculo_id": "veh-1",
            "perfil_version": 1,
            "operation_time": "1700000000.3",
            "completo": True,
            "encontrado": True,
        }
        assert "placa" not in json.dumps(repo.guardados[0], default=str)

        revocar = {"titular_id": "titular-1", "consentimiento_id": "k-1"}
        assert client.post("/perfiles/consentimiento-revocado", json=revocar).status_code == 204
        assert repo.revocados == [("titular-1", "k-1")]
        assert client.post("/perfiles/_purgar").json() == {"perfiles_borrados": 3}
