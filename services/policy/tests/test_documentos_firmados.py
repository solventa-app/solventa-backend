"""Los documentos firmados, guardados y cifrados (KAN-26, T6, D-1): el contrato firmado al firmar y
la póliza al emitir, en ALMACENAMIENTO y siempre cifrados; servidos desde ahí; con el flujo intacto
si guardar falla."""

import logging
import re
from typing import Any

import pytest
from fakes_bloque9 import png
from test_firma_y_pago import Entorno

from app.application.cifrado import CIFRADO_CLAVE_LOCAL
from app.application.cliente_almacenamiento import AlmacenamientoNoDisponible, ArchivoRechazado
from app.application.servicio_documentos import (
    TIPO_CONTRATO,
    TIPO_POLIZA,
    DocumentoAlterado,
    ServicioDocumentos,
)
from app.application.servicio_firma_y_pago import Autorizacion
from app.application.servicio_polizas import ServicioPolizas
from app.application.servicio_purga import ServicioPurga
from app.application.sobre_documentos import EnvolvedorLocal, ErrorDeSobre


def adjuntar_documentos(repo: Any) -> None:
    """Lo que hace el repositorio de Postgres (`documentos_firmados`), en memoria."""
    repo.documentos: dict[tuple[str, str], dict[str, Any]] = {}

    def obtener_documento(propuesta_id: str, tipo: str) -> dict | None:
        return repo.documentos.get((propuesta_id, tipo))

    def guardar_documento(propuesta_id, tipo, archivo_id, clave_envuelta, sha256, tamano_bytes):
        if (propuesta_id, tipo) in repo.documentos:
            return False
        repo.documentos[(propuesta_id, tipo)] = {
            "propuesta_id": propuesta_id,
            "tipo": tipo,
            "archivo_id": archivo_id,
            "clave_envuelta": clave_envuelta,
            "sha256": sha256,
            "tamano_bytes": tamano_bytes,
        }
        return True

    def documentos_de(propuesta_id: str) -> list[dict]:
        return [d for (p, _), d in repo.documentos.items() if p == propuesta_id]

    repo.obtener_documento = obtener_documento
    repo.guardar_documento = guardar_documento
    repo.documentos_de = documentos_de


class EntornoDocumentos(Entorno):
    def __init__(self) -> None:
        super().__init__()
        adjuntar_documentos(self.repo)
        self.documentos = ServicioDocumentos(
            self.repo, self.almacen, EnvolvedorLocal.desde_base64(CIFRADO_CLAVE_LOCAL)
        )
        self.polizas = ServicioPolizas(
            self.repo, almacenamiento=self.almacen, documentos=self.documentos
        )
        # El servicio de firma guarda el contrato firmado, como lo cablea `main.py`.
        self.servicio._guardar_contrato = self.polizas.guardar_contrato_firmado

    def firmar_con_imagen(self) -> tuple[dict, Autorizacion]:
        propuesta, token = self.hasta_en_firma()
        auth = Autorizacion(enlace_token=token)
        archivo = self.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 100).archivo[
            "id"
        ]
        self.almacen.subir(archivo, png())
        self.servicio.firmar(propuesta["id"], auth, "v1", True, True, "IMAGEN", archivo)
        return propuesta, auth

    def emitir(self, propuesta: dict, auth: Autorizacion) -> dict:
        sesion = self.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
        self.pagos.pagar(sesion.sesion_id)
        return self.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)["poliza"]

    def guardados(self, tipo: str) -> list[bytes]:
        """Los bytes que de verdad llegaron al bucket para ese tipo de documento."""
        return [
            self.almacen.contenidos[a["id"]]
            for a in self.almacen.archivos.values()
            if a["tipo"] == tipo
        ]


@pytest.fixture
def e() -> EntornoDocumentos:
    return EntornoDocumentos()


# ---------- al firmar: el contrato firmado


def test_al_firmar_se_guarda_el_contrato_firmado_cifrado(e) -> None:
    propuesta, _ = e.firmar_con_imagen()

    (contenedor,) = e.guardados(TIPO_CONTRATO)
    assert contenedor.startswith(b"SOLVENC1")
    assert b"%PDF" not in contenedor and b"Camila" not in contenedor
    fila = e.repo.documentos[(propuesta["id"], TIPO_CONTRATO)]
    assert fila["clave_envuelta"] and fila["tamano_bytes"] > 1000 and len(fila["sha256"]) == 64
    # Lo que se guardó es el contrato firmado, con la evidencia y la imagen estampada.
    contrato = e.documentos.leer(propuesta["id"], TIPO_CONTRATO)
    assert contrato is not None and contrato.startswith(b"%PDF")
    assert b"Contrato firmado" in contrato and b"/Subtype /Image" in contrato
    assert b"Camila Rojas" in contrato  # el contacto del asesor: queda dentro, ya cifrado


def test_firmar_con_nombre_estampa_el_nombre_en_el_contrato_guardado(e) -> None:
    propuesta, token = e.hasta_en_firma()
    e.firmar_nombre(propuesta["id"], Autorizacion(enlace_token=token))

    contrato = e.documentos.leer(propuesta["id"], TIPO_CONTRATO)
    assert contrato is not None and b"Contrato firmado" in contrato


def test_si_guardar_falla_la_firma_sigue_y_el_contrato_se_guarda_en_la_siguiente_consulta(
    e,
) -> None:
    e.almacen.documentos_caidos = True
    propuesta, auth = e.firmar_con_imagen()

    assert e.repo.obtener_evidencia(propuesta["id"]) is not None  # la firma quedó registrada
    assert e.guardados(TIPO_CONTRATO) == []
    assert not e.documentos.existe(propuesta["id"], TIPO_CONTRATO)

    e.almacen.documentos_caidos = False
    datos = e.repo.obtener(propuesta["id"])
    contrato = e.polizas.contrato(datos)  # `contrato.pdf`: lo genera, lo guarda y lo entrega

    assert contrato.startswith(b"%PDF") and b"Contrato firmado" in contrato
    assert e.documentos.existe(propuesta["id"], TIPO_CONTRATO)


def test_contrato_pdf_es_el_borrador_antes_de_firmar_y_el_firmado_despues(e) -> None:
    propuesta, token = e.hasta_en_firma()
    datos = e.repo.obtener(propuesta["id"])

    antes = e.polizas.contrato(datos)
    assert b"en tr" in antes and b"Contrato firmado" not in antes  # «en trámite»

    auth = Autorizacion(enlace_token=token)
    archivo = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 100).archivo["id"]
    e.almacen.subir(archivo, png())
    e.servicio.firmar(propuesta["id"], auth, "v1", True, True, "IMAGEN", archivo)
    despues = e.polizas.contrato(e.repo.obtener(propuesta["id"]))

    assert b"Contrato firmado" in despues
    assert e.polizas.contrato(e.repo.obtener(propuesta["id"])) == despues  # el mismo, guardado


# ---------- al emitir: la póliza


def test_la_poliza_se_guarda_cifrada_y_se_sirve_siempre_la_misma(e) -> None:
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)

    primero = e.polizas.documento(poliza["id"], "Camila Rojas")
    (contenedor,) = e.guardados(TIPO_POLIZA)
    assert primero.startswith(b"%PDF") and b"Camila Rojas" in primero
    assert contenedor.startswith(b"SOLVENC1") and b"Camila" not in contenedor

    # Quien lo pida después recibe el que se guardó, no uno regenerado con otro nombre.
    segundo = e.polizas.documento(poliza["id"], "Otro Nombre")
    assert segundo == primero and b"Otro Nombre" not in segundo
    assert len(e.guardados(TIPO_POLIZA)) == 1


def test_el_documento_guardado_se_sirve_aunque_la_imagen_de_la_firma_ya_no_este(e) -> None:
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)
    primero = e.polizas.documento(poliza["id"], "Camila Rojas")
    for archivo in list(e.almacen.archivos.values()):
        if archivo["tipo"] == "firma-imagen":
            archivo["estado"] = "BORRADO"  # p. ej. la retención del bucket la borró

    assert e.polizas.documento(poliza["id"], "Camila Rojas") == primero


def test_si_guardar_la_poliza_falla_se_genera_como_antes_y_se_guarda_despues(e) -> None:
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)
    e.almacen.documentos_caidos = True

    pdf = e.polizas.documento(poliza["id"], "Camila Rojas")
    assert pdf.startswith(b"%PDF") and e.guardados(TIPO_POLIZA) == []

    e.almacen.documentos_caidos = False
    e.polizas.documento(poliza["id"], "Camila Rojas")
    assert len(e.guardados(TIPO_POLIZA)) == 1


def test_si_el_almacenamiento_no_responde_al_leer_se_regenera_y_no_se_toca_lo_guardado(e) -> None:
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)
    e.polizas.documento(poliza["id"], "Camila Rojas")
    guardado = e.repo.documentos[(propuesta["id"], TIPO_POLIZA)]

    def caido(archivo_id, tamano_max=None):
        raise AlmacenamientoNoDisponible("HTTP 503")

    e.almacen.bajar = caido  # type: ignore[method-assign]
    pdf = e.polizas.documento(poliza["id"], "Camila Rojas")

    assert pdf.startswith(b"%PDF")  # el usuario recibe su documento (regla 4)
    assert e.repo.documentos[(propuesta["id"], TIPO_POLIZA)] is guardado


# ---------- integridad


def test_un_documento_alterado_en_el_bucket_no_se_sirve(e) -> None:
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)
    e.polizas.documento(poliza["id"], "Camila Rojas")
    archivo_id = e.repo.documentos[(propuesta["id"], TIPO_POLIZA)]["archivo_id"]
    alterado = bytearray(e.almacen.contenidos[archivo_id])
    alterado[-3] ^= 0xFF
    e.almacen.contenidos[archivo_id] = bytes(alterado)

    with pytest.raises(DocumentoAlterado):
        e.polizas.documento(poliza["id"], "Camila Rojas")


def test_un_contenedor_de_otra_propuesta_no_se_sirve(e) -> None:
    uno, auth = e.firmar_con_imagen()
    poliza = e.emitir(uno, auth)
    e.polizas.documento(poliza["id"], "Camila Rojas")
    del e.repo.documentos[(uno["id"], TIPO_CONTRATO)]
    e.documentos.guardar("otra-propuesta", TIPO_POLIZA, b"%PDF-1.4 ajeno")
    ajena = e.repo.documentos[("otra-propuesta", TIPO_POLIZA)]
    # Se copia la fila de la otra propuesta sobre la de esta (lo que haría un atacante con la base).
    e.repo.documentos[(uno["id"], TIPO_POLIZA)] = {**ajena, "propuesta_id": uno["id"]}

    with pytest.raises(DocumentoAlterado):
        e.documentos.leer(uno["id"], TIPO_POLIZA)


def test_el_bucket_rechaza_un_pdf_en_claro_declarado_como_contenedor(e) -> None:
    with pytest.raises(ArchivoRechazado):
        e.almacen.subir_documento(TIPO_POLIZA, "prop-x", b"%PDF-1.4 sin cifrar")


# ---------- idempotencia, concurrencia y servicio


def test_guardar_dos_veces_el_mismo_documento_no_sube_otro_archivo(e) -> None:
    assert e.documentos.guardar("prop-1", TIPO_POLIZA, b"%PDF-1.4 uno") is True
    assert e.documentos.guardar("prop-1", TIPO_POLIZA, b"%PDF-1.4 distinto") is True

    assert len(e.guardados(TIPO_POLIZA)) == 1
    assert e.documentos.leer("prop-1", TIPO_POLIZA) == b"%PDF-1.4 uno"  # inmutable: gana el primero


def test_leer_sin_documento_guardado_es_none(e) -> None:
    assert e.documentos.leer("prop-1", TIPO_POLIZA) is None


def test_una_falla_de_kms_al_guardar_no_lanza_y_al_leer_se_regenera() -> None:
    class EnvolvedorCaido:
        def envolver(self, llave: bytes, contexto: str) -> bytes:
            raise RuntimeError("KMS no responde")

        def desenvolver(self, envuelta: bytes, contexto: str) -> bytes:
            raise RuntimeError("KMS no responde")

    e = EntornoDocumentos()
    servicio = ServicioDocumentos(e.repo, e.almacen, EnvolvedorCaido())

    assert servicio.guardar("prop-1", TIPO_POLIZA, b"%PDF-1.4") is False
    assert e.guardados(TIPO_POLIZA) == []  # nada sin cifrar llegó al bucket

    bueno = EnvolvedorLocal.desde_base64(CIFRADO_CLAVE_LOCAL)
    ServicioDocumentos(e.repo, e.almacen, bueno).guardar("prop-1", TIPO_POLIZA, b"%PDF-1.4")
    assert servicio.leer("prop-1", TIPO_POLIZA) is None  # KMS caído: disponibilidad, no integridad


def test_el_error_de_integridad_es_distinto_del_de_disponibilidad() -> None:
    assert not issubclass(ErrorDeSobre, (ConnectionError, TimeoutError))


# ---------- purga


def test_si_la_propuesta_se_cancela_por_consentimiento_se_pide_retener_el_contrato(e) -> None:
    propuesta, _ = e.firmar_con_imagen()
    archivo_contrato = e.repo.documentos[(propuesta["id"], TIPO_CONTRATO)]["archivo_id"]
    purga = ServicioPurga(e.repo, _Consentimientos(), e.almacen, documentos=e.documentos)

    purga._purgar(propuesta["id"])

    assert archivo_contrato in e.almacen.retenidos


class _Consentimientos:
    def consentimiento_vigente(self, consentimiento_id: str) -> bool:
        return False


# ---------- privacidad


def test_ni_el_contenido_ni_la_llave_salen_en_los_logs(e, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    propuesta, auth = e.firmar_con_imagen()
    poliza = e.emitir(propuesta, auth)
    e.polizas.documento(poliza["id"], "Camila Rojas")

    fila = e.repo.documentos[(propuesta["id"], TIPO_POLIZA)]
    for dato in ("Camila", fila["clave_envuelta"], fila["sha256"], "%PDF"):
        assert dato not in caplog.text
    assert re.search(r"documento guardado propuesta=\w+ tipo=poliza-pdf", caplog.text)
