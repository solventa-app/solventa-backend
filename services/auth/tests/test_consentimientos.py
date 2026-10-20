"""Pruebas de los endpoints de consentimiento con un almacén en memoria (sin PostgreSQL).
La tabla append-only real se verifica con `scripts/verificar-migraciones.sh auth` y el humo."""

from fakes import AlmacenConsentimientosMemoria
from fastapi.testclient import TestClient

from app.main import create_app

SOLICITUD = {"finalidad": "cotizacion-vida-hipotecario", "version_texto": "v1"}


def _cliente() -> TestClient:
    return TestClient(create_app(almacen=AlmacenConsentimientosMemoria()))


def test_registrar_devuelve_consentimiento_vigente() -> None:
    r = _cliente().post("/consentimientos", json=SOLICITUD)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["finalidad"] == "cotizacion-vida-hipotecario"
    assert cuerpo["version_texto"] == "v1"
    assert cuerpo["vigente"] is True
    assert cuerpo["revocado_en"] is None
    assert cuerpo["otorgado_en"]


def test_obtener_devuelve_el_registrado() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    r = client.get(f"/consentimientos/{creado['id']}")

    assert r.status_code == 200
    assert r.json() == creado


def test_obtener_inexistente_responde_404() -> None:
    assert _cliente().get("/consentimientos/no-existe").status_code == 404


def test_revocar_marca_revocado_sin_borrar() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    r = client.post(f"/consentimientos/{creado['id']}/revocar")

    assert r.status_code == 200
    assert r.json()["vigente"] is False
    assert r.json()["revocado_en"] is not None
    # Append-only: sigue existiendo, ahora revocado.
    assert client.get(f"/consentimientos/{creado['id']}").json()["vigente"] is False


def test_revocar_dos_veces_conserva_la_primera_fecha() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    primera = client.post(f"/consentimientos/{creado['id']}/revocar").json()
    segunda = client.post(f"/consentimientos/{creado['id']}/revocar").json()

    assert segunda["revocado_en"] == primera["revocado_en"]


def test_revocar_inexistente_responde_404() -> None:
    assert _cliente().post("/consentimientos/no-existe/revocar").status_code == 404


def test_registrar_con_actor_lo_guarda_y_devuelve() -> None:
    client = _cliente()
    actor = {"rol": "ASESOR", "usuario_id": "u-123"}

    creado = client.post("/consentimientos", json={**SOLICITUD, "actor": actor}).json()

    assert creado["actor"] == actor
    assert client.get(f"/consentimientos/{creado['id']}").json()["actor"] == actor


def test_registrar_sin_actor_lo_deja_vacio() -> None:
    assert _cliente().post("/consentimientos", json=SOLICITUD).json()["actor"] is None


# ---------- Traducción de la fila de PostgreSQL ----------


def test_fila_sin_actor_y_sin_revocar() -> None:
    from datetime import UTC, datetime

    from app.consentimientos import _desde_fila

    otorgado = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    c = _desde_fila(
        {
            "id": "c-1",
            "finalidad": "perfilamiento",
            "version_texto": "v1",
            "otorgado_en": otorgado,
            "revocado_en": None,
            "actor_rol": None,
            "actor_usuario_id": None,
        }
    )

    assert c.otorgado_en == "2026-10-17T12:00:00+00:00"
    assert c.actor is None
    assert c.vigente is True


def test_fila_con_actor_y_revocada() -> None:
    from datetime import UTC, datetime

    from app.consentimientos import _desde_fila

    c = _desde_fila(
        {
            "id": "c-1",
            "finalidad": "perfilamiento",
            "version_texto": "v1",
            "otorgado_en": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            "revocado_en": datetime(2026, 10, 17, 13, 0, tzinfo=UTC),
            "actor_rol": "ASESOR",
            "actor_usuario_id": "u-1",
        }
    )

    assert c.actor == {"rol": "ASESOR", "usuario_id": "u-1"}
    assert c.revocado_en == "2026-10-17T13:00:00+00:00"
    assert c.vigente is False
    assert c.estado == "REVOCADO"


# ---------- Vencimiento de 30 días y titular opaco (Bloque 4, decisiones 20 y 23) ----------


def test_registrar_fija_vencimiento_a_30_dias_y_estado_vigente() -> None:
    from datetime import datetime, timedelta

    cuerpo = _cliente().post("/consentimientos", json=SOLICITUD).json()

    otorgado = datetime.fromisoformat(cuerpo["otorgado_en"])
    vence = datetime.fromisoformat(cuerpo["vence_en"])
    assert vence - otorgado == timedelta(days=30)
    assert cuerpo["estado"] == "VIGENTE"
    assert cuerpo["vigente"] is True


def test_consentimiento_vencido_se_detecta_al_leer_sin_tocar_la_base() -> None:
    from datetime import UTC, datetime, timedelta

    from app.consentimientos import Consentimiento

    almacen = AlmacenConsentimientosMemoria()
    client = TestClient(create_app(almacen=almacen))
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    # Simulamos que pasaron más de 30 días dejando `vence_en` en el pasado, sin revocar.
    pasado = datetime.now(UTC) - timedelta(days=31)
    almacen._por_id[creado["id"]] = Consentimiento(
        id=creado["id"],
        finalidad=creado["finalidad"],
        version_texto=creado["version_texto"],
        otorgado_en=(pasado - timedelta(days=30)).isoformat(),
        vence_en=pasado.isoformat(),
    )

    leido = client.get(f"/consentimientos/{creado['id']}").json()
    assert leido["revocado_en"] is None
    assert leido["vigente"] is False
    assert leido["estado"] == "VENCIDO"


def test_registrar_con_documento_calcula_titular_id_opaco_y_nunca_guarda_ni_loguea_documento(
    caplog,
) -> None:
    import logging

    from app.configuracion import CLIENTE_ID_CLAVE_LOCAL
    from app.identificadores import cliente_id_desde_documento

    documento = "1020304050"
    almacen = AlmacenConsentimientosMemoria()
    client = TestClient(create_app(almacen=almacen))

    with caplog.at_level(logging.DEBUG):
        r = client.post(
            "/consentimientos",
            json={
                **SOLICITUD,
                "documento_identidad": f"  {documento} ",
                "tipo_dato": "FINANCIEROS_Y_ABIERTOS",
                "fuente_datos": "OPEN_FINANCE_OPEN_DATA",
                "canal": "WEB",
            },
        )

    assert r.status_code == 200
    cuerpo = r.json()
    esperado_titular = cliente_id_desde_documento(documento, CLIENTE_ID_CLAVE_LOCAL)
    assert cuerpo["titular_id"] == esperado_titular
    assert documento not in cuerpo["titular_id"]
    assert "documento_identidad" not in cuerpo
    assert cuerpo["tipo_dato"] == "FINANCIEROS_Y_ABIERTOS"
    assert cuerpo["fuente_datos"] == "OPEN_FINANCE_OPEN_DATA"
    assert cuerpo["canal"] == "WEB"
    assert documento not in str(almacen._por_id[cuerpo["id"]].como_dict())
    assert documento not in caplog.text


def test_registrar_con_titular_id_explicito_lo_conserva() -> None:
    cuerpo = (
        _cliente()
        .post(
            "/consentimientos",
            json={
                **SOLICITUD,
                "titular_id": "hash-opaco-del-bff",
                "tipo_dato": "FINANCIEROS_Y_ABIERTOS",
                "fuente_datos": "OPEN_FINANCE_OPEN_DATA",
                "canal": "WEB",
            },
        )
        .json()
    )

    assert cuerpo["titular_id"] == "hash-opaco-del-bff"
    assert cuerpo["tipo_dato"] == "FINANCIEROS_Y_ABIERTOS"
    assert cuerpo["fuente_datos"] == "OPEN_FINANCE_OPEN_DATA"
    assert cuerpo["canal"] == "WEB"
