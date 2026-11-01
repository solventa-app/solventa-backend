"""W05-01: POLICY da el nombre del titular que capturó el ASESOR, solo si conserva el contacto."""

from fakes_bloque9 import RepositorioBloque9Falso

from app.application.servicio_firma_y_pago import ServicioFirmaYPago


def _servicio(repositorio: RepositorioBloque9Falso) -> ServicioFirmaYPago:
    servicio = ServicioFirmaYPago.__new__(ServicioFirmaYPago)
    servicio._repo = repositorio
    return servicio


def test_solo_devuelve_las_propuestas_con_nombre() -> None:
    repositorio = RepositorioBloque9Falso()
    repositorio.contactos = {"con": {"nombre": "Carlos"}, "purgada": {"nombre": None}}

    assert _servicio(repositorio).nombres_titulares(["con", "purgada", "otra"]) == {"con": "Carlos"}
