"""Dispositivos de confianza (KAN-26): entrar con huella o rostro con la llave del teléfono."""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fakes import (
    CIFRADOR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    Reloj,
    RepositorioDispositivosMemoria,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.dispositivos import DispositivoInvalido, ServicioDispositivos
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE_HMAC = "clave-hmac-de-prueba"
CORREO = "ana.prueba@ejemplo.com"
CLAVE = "Clave-segura-1"


class Telefono:
    """Hace de Keystore: la privada nunca sale de aquí."""

    def __init__(self) -> None:
        self._privada = ec.generate_private_key(ec.SECP256R1())

    @property
    def publica(self) -> str:
        der = self._privada.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode()

    def firmar(self, reto: str) -> str:
        return base64.b64encode(
            self._privada.sign(reto.encode(), ec.ECDSA(hashes.SHA256()))
        ).decode()


def _preparar():
    repo = RepositorioUsuariosMemoria()
    reloj = Reloj()
    reloj.ahora = datetime.now(UTC)
    usuarios = ServicioUsuarios(repo, HASHER_RAPIDO, CIFRADOR_PRUEBAS, clave_titular=CLAVE_HMAC)
    sesiones = ServicioSesiones(
        repo,
        HASHER_RAPIDO,
        SECRETO,
        15,
        reloj=reloj,
        clave_correo=CLAVE_HMAC,
        refrescos=RepositorioRefrescosMemoria(),
    )
    dispositivos = ServicioDispositivos(
        RepositorioDispositivosMemoria(), SECRETO, reloj=lambda: reloj.ahora
    )
    cuenta = usuarios.crear_cuenta_onboarding(CORREO, CLAVE)
    usuarios.cerrar_verificacion(cuenta["id"], True, "ANA PRUEBA")
    return usuarios, sesiones, dispositivos, reloj, cuenta


def test_firmar_el_reto_abre_la_sesion_de_la_app() -> None:
    _, sesiones, dispositivos, _, cuenta = _preparar()
    tel = Telefono()
    dispositivo = dispositivos.registrar(cuenta["id"], tel.publica)
    reto, _ = dispositivos.reto(dispositivo)
    usuario_id = dispositivos.verificar(dispositivo, reto, tel.firmar(reto))
    sesion = sesiones.iniciar_sesion_de_confianza(usuario_id, "app")
    assert sesion["usuario"]["id"] == cuenta["id"] and "refresh_token" in sesion


def test_la_firma_de_otro_telefono_no_entra() -> None:
    _, _, dispositivos, _, cuenta = _preparar()
    dispositivo = dispositivos.registrar(cuenta["id"], Telefono().publica)
    reto, _ = dispositivos.reto(dispositivo)
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar(dispositivo, reto, Telefono().firmar(reto))


def test_el_reto_vence_a_los_60_segundos_y_no_se_puede_inventar() -> None:
    _, _, dispositivos, reloj, cuenta = _preparar()
    tel = Telefono()
    dispositivo = dispositivos.registrar(cuenta["id"], tel.publica)
    reto, _ = dispositivos.reto(dispositivo)
    # Un reto armado por el cliente (sin el MAC de AUTH) no sirve aunque la firma sea buena.
    falso = f"{dispositivo}.nonce.{int((reloj.ahora + timedelta(minutes=5)).timestamp())}.mac"
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar(dispositivo, falso, tel.firmar(falso))
    reloj.ahora += timedelta(seconds=61)
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar(dispositivo, reto, tel.firmar(reto))


def test_el_reto_de_un_dispositivo_no_sirve_para_otro() -> None:
    _, _, dispositivos, _, cuenta = _preparar()
    tel = Telefono()
    uno = dispositivos.registrar(cuenta["id"], tel.publica)
    reto, _ = dispositivos.reto(uno)
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar("otro-id", reto, tel.firmar(reto))


def test_registrar_otro_revoca_el_anterior_y_cambiar_la_clave_los_revoca_todos() -> None:
    _, _, dispositivos, _, cuenta = _preparar()
    viejo, nuevo = Telefono(), Telefono()
    id_viejo = dispositivos.registrar(cuenta["id"], viejo.publica)
    id_nuevo = dispositivos.registrar(cuenta["id"], nuevo.publica)
    reto, _ = dispositivos.reto(id_viejo)
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar(id_viejo, reto, viejo.firmar(reto))
    reto, _ = dispositivos.reto(id_nuevo)
    assert dispositivos.verificar(id_nuevo, reto, nuevo.firmar(reto)) == cuenta["id"]
    dispositivos.revocar_de_usuario(cuenta["id"])
    reto, _ = dispositivos.reto(id_nuevo)
    with pytest.raises(DispositivoInvalido):
        dispositivos.verificar(id_nuevo, reto, nuevo.firmar(reto))


def test_llave_que_no_es_p256_se_rechaza() -> None:
    _, _, dispositivos, _, cuenta = _preparar()
    for mala in ("no-es-base64!", base64.b64encode(b"basura").decode()):
        with pytest.raises(DispositivoInvalido):
            dispositivos.registrar(cuenta["id"], mala)


def test_una_cuenta_suspendida_o_de_otro_rol_no_entra_con_la_llave() -> None:
    usuarios, sesiones, dispositivos, _, cuenta = _preparar()
    from app.sesiones import CredencialesInvalidas

    usuarios.suspender(cuenta["id"])
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion_de_confianza(cuenta["id"], "app")
    otra = usuarios.crear("asesor.017", CLAVE, "ASESOR", correo="a@ejemplo.com")
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion_de_confianza(otra["id"], "app")


def test_rutas_http_y_mismo_401_generico() -> None:
    usuarios, sesiones, dispositivos, _, cuenta = _preparar()
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=usuarios,
        servicio_sesiones=sesiones,
        servicio_dispositivos=dispositivos,
        config=Configuracion("postgresql://no-se-usa", SECRETO, 15),
    )
    tel = Telefono()
    with TestClient(app) as cliente:
        malo = cliente.post(
            "/dispositivos", json={"usuario_id": cuenta["id"], "clave_publica": "xx"}
        )
        assert malo.status_code == 400
        ok = cliente.post(
            "/dispositivos", json={"usuario_id": cuenta["id"], "clave_publica": tel.publica}
        )
        dispositivo = ok.json()["dispositivo_id"]
        reto = cliente.post(f"/dispositivos/{dispositivo}/reto").json()["reto"]
        sesion = cliente.post(
            f"/dispositivos/{dispositivo}/sesion",
            json={"reto": reto, "firma": tel.firmar(reto), "canal": "app"},
        )
        assert sesion.status_code == 200
        assert sesion.json()["usuario"]["nombre"] == "ANA PRUEBA"
        # Un dispositivo que no existe pide reto igual y falla igual que una firma mala.
        reto2 = cliente.post("/dispositivos/no-existe/reto").json()["reto"]
        inexistente = cliente.post(
            "/dispositivos/no-existe/sesion",
            json={"reto": reto2, "firma": tel.firmar(reto2), "canal": "app"},
        )
        firma_mala = cliente.post(
            f"/dispositivos/{dispositivo}/sesion",
            json={"reto": reto, "firma": Telefono().firmar(reto), "canal": "app"},
        )
        assert inexistente.status_code == firma_mala.status_code == 401
        assert inexistente.json() == firma_mala.json()
