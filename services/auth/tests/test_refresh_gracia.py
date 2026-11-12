"""Período de gracia del refresh (HU-M02, KAN-227).

Si la respuesta de una renovación se pierde en la red, la app reintenta con el mismo refresh. Dentro
de la gracia y con el sucesor sin usar, AUTH reemplaza ese sucesor en vez de revocar la cadena.
Fuera de la gracia, o si el sucesor ya se usó, el reuso sigue revocando todo (posible robo)."""

import json
import logging
from datetime import timedelta

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    Reloj,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
    extras_cliente,
)

from app.configuracion import RUTA_SESION, Configuracion
from app.sesiones import RefreshInvalido, ServicioSesiones
from app.usuarios import ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE = "Clave-segura-1"
GRACIA = 30


def _preparar(gracia: int = GRACIA):
    repositorio = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    reloj = Reloj()
    ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS).crear(
        "u.m02", CLAVE, "CLIENTE", **extras_cliente("u.m02")
    )
    sesiones = ServicioSesiones(
        repositorio,
        HASHER_RAPIDO,
        SECRETO,
        60,
        reloj=reloj,
        refrescos=refrescos,
        refresh_gracia_segundos=gracia,
    )
    return sesiones, refrescos, reloj


def _vigentes(refrescos: RepositorioRefrescosMemoria) -> list[dict]:
    return [f for f in refrescos.filas.values() if f["usado_en"] is None and not f["revocado_en"]]


def test_reintento_dentro_de_la_gracia_reemplaza_el_sucesor_perdido(caplog) -> None:
    sesiones, refrescos, reloj = _preparar()
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    r2_perdido = sesiones.renovar_con_refresh(r1, "app")["refresh_token"]  # la respuesta no llega
    reloj.ahora += timedelta(seconds=GRACIA - 1)

    with caplog.at_level(logging.INFO, logger="auth.sesiones"):
        reintento = sesiones.renovar_con_refresh(r1, "app")

    r2 = reintento["refresh_token"]
    assert r2 not in (r1, r2_perdido)
    assert reintento["expira_en"] == reloj.ahora + timedelta(minutes=60)
    # El sucesor perdido queda revocado y solo el nuevo sirve.
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r2_perdido, "app")
    assert sesiones.renovar_con_refresh(r2, "app")["refresh_token"]
    assert len({f["familia_id"] for f in refrescos.filas.values()}) == 1
    assert "refresh reintentado en gracia" in caplog.text
    assert r1 not in caplog.text and r2 not in caplog.text


def test_en_el_limite_exacto_de_la_gracia_todavia_se_acepta() -> None:
    sesiones, _, reloj = _preparar()
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    sesiones.renovar_con_refresh(r1, "app")
    reloj.ahora += timedelta(seconds=GRACIA)

    assert sesiones.renovar_con_refresh(r1, "app")["refresh_token"]


def test_fuera_de_la_gracia_el_reuso_revoca_toda_la_cadena() -> None:
    sesiones, refrescos, reloj = _preparar()
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    r2 = sesiones.renovar_con_refresh(r1, "app")["refresh_token"]
    reloj.ahora += timedelta(seconds=GRACIA + 1)

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "app")
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r2, "app")
    assert _vigentes(refrescos) == []


def test_si_el_sucesor_ya_se_uso_el_reuso_revoca_aunque_este_en_gracia() -> None:
    sesiones, refrescos, reloj = _preparar()
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    r2 = sesiones.renovar_con_refresh(r1, "app")["refresh_token"]
    reloj.ahora += timedelta(seconds=1)
    r3 = sesiones.renovar_con_refresh(r2, "app")["refresh_token"]  # el cliente legítimo avanzó
    reloj.ahora += timedelta(seconds=1)

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "app")  # solo lo puede tener otro
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r3, "app")
    assert _vigentes(refrescos) == []


def test_un_refresh_revocado_no_se_salva_por_la_gracia() -> None:
    sesiones, _, _ = _preparar()
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    r2 = sesiones.renovar_con_refresh(r1, "app")["refresh_token"]
    sesiones.cerrar_sesion(r2)

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "app")


def test_con_gracia_cero_el_reuso_inmediato_revoca_como_antes() -> None:
    sesiones, refrescos, _ = _preparar(gracia=0)
    r1 = sesiones.iniciar_sesion("u.m02", CLAVE, "app")["refresh_token"]
    sesiones.renovar_con_refresh(r1, "app")

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "app")
    assert _vigentes(refrescos) == []


def test_la_gracia_tambien_vale_en_la_web() -> None:
    repositorio = RepositorioUsuariosMemoria()
    reloj = Reloj()
    ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS).crear(
        "asesor.m02", CLAVE, "ASESOR"
    )
    sesiones = ServicioSesiones(
        repositorio,
        HASHER_RAPIDO,
        SECRETO,
        60,
        reloj=reloj,
        refrescos=RepositorioRefrescosMemoria(),
        refresh_gracia_segundos=GRACIA,
    )
    r1 = sesiones.iniciar_sesion("asesor.m02", CLAVE, "web")["refresh_token"]
    sesiones.renovar_con_refresh(r1, "web")

    assert sesiones.renovar_con_refresh(r1, "web")["refresh_token"]


def test_la_gracia_sale_del_json_y_el_entorno_la_sobrescribe(monkeypatch) -> None:
    datos = json.loads(RUTA_SESION.read_text(encoding="utf-8"))
    monkeypatch.delenv("REFRESH_GRACIA_SEGUNDOS", raising=False)
    assert Configuracion.desde_entorno().refresh_gracia_segundos == datos["refresh_gracia_segundos"]
    assert datos["refresh_gracia_segundos"] == GRACIA
    monkeypatch.setenv("REFRESH_GRACIA_SEGUNDOS", "0")
    assert Configuracion.desde_entorno().refresh_gracia_segundos == 0
