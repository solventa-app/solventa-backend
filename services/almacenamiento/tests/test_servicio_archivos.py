"""Ciclo de vida de un archivo con dobles en memoria (sin Postgres ni Cloud Storage)."""

from datetime import timedelta

import pytest
from fakes import (
    AHORA,
    JPEG,
    MP4,
    PNG,
    AlmacenFalso,
    BusFalso,
    RepositorioFalso,
    catalogo_de_prueba,
)

from app.application.servicio_archivos import Actor, ErrorArchivo, ServicioArchivos, Vigencias

CLIENTE = Actor("CLIENTE", "u-1")
OTRO_CLIENTE = Actor("CLIENTE", "u-2")
ASESOR = Actor("ASESOR", "a-1")
BACKOFFICE = Actor("BACKOFFICE", "b-1")
ONBOARDING = Actor("SERVICIO", "onboarding")


class Entorno:
    def __init__(self, ahora=AHORA) -> None:
        self.repo = RepositorioFalso()
        self.almacen = AlmacenFalso()
        self.bus = BusFalso()
        self.ahora = ahora
        self.servicio = ServicioArchivos(
            self.repo,
            self.almacen,
            catalogo_de_prueba(),
            self.bus,
            Vigencias(600, 300),
            reloj=lambda: self.ahora,
        )

    def subir_foto(
        self, contenido=JPEG, tipo_contenido="image/jpeg", actor=CLIENTE, declarado=None
    ):
        solicitud = self.servicio.solicitar_subida(
            actor, "foto-cliente", tipo_contenido, declarado or len(contenido), actor.id
        )
        archivo = solicitud["archivo"]
        self.almacen.subir(archivo["objeto"], contenido, tipo_contenido)
        return archivo

    def subir_video(self):
        solicitud = self.servicio.solicitar_subida(
            ONBOARDING, "video-onboarding", "video/mp4", len(MP4), "onb-1", "cons-1"
        )
        archivo = solicitud["archivo"]
        self.almacen.subir(archivo["objeto"], MP4, "video/mp4")
        return self.servicio.confirmar(archivo["id"])


@pytest.fixture
def entorno() -> Entorno:
    return Entorno()


def _codigo(funcion, *argumentos) -> str:
    with pytest.raises(ErrorArchivo) as error:
        funcion(*argumentos)
    return error.value.codigo


# ------------------------------------------------------------------ solicitar la subida


def test_solicitar_subida_registra_pendiente_y_devuelve_url_firmada(entorno: Entorno) -> None:
    resultado = entorno.servicio.solicitar_subida(CLIENTE, "foto-cliente", "image/jpeg", 64, "u-1")

    archivo = resultado["archivo"]
    assert archivo["estado"] == "PENDIENTE"
    assert archivo["objeto"] == f"foto-cliente/2026/10/{archivo['id']}"
    assert archivo["subido_por"] == "CLIENTE" and archivo["subido_por_id"] == "u-1"
    assert resultado["metodo"] == "PUT"
    assert resultado["encabezados"]["x-goog-content-length-range"] == "0,1000"
    assert resultado["encabezados"]["x-goog-if-generation-match"] == "0"
    assert resultado["vence_en"] == AHORA + timedelta(seconds=600)
    assert entorno.repo.accesos[0]["accion"] == "SUBIDA"


@pytest.mark.parametrize(
    ("actor", "tipo", "contenido", "tamano", "propietario", "consentimiento", "codigo"),
    [
        (CLIENTE, "no-existe", "image/jpeg", 10, "u-1", None, "TIPO_NO_ADMITIDO"),
        (BACKOFFICE, "pdf-viejo", "application/pdf", 10, "p-1", None, "TIPO_NO_ADMITIDO"),
        (BACKOFFICE, "foto-cliente", "image/jpeg", 10, "u-1", None, "NO_AUTORIZADO"),
        (CLIENTE, "video-onboarding", "video/mp4", 10, "onb", "c", "NO_AUTORIZADO"),
        (CLIENTE, "foto-cliente", "application/pdf", 10, "u-1", None, "TIPO_CONTENIDO_NO_ADMITIDO"),
        (CLIENTE, "foto-cliente", "image/jpeg", 1001, "u-1", None, "TAMANO_NO_ADMITIDO"),
        (ONBOARDING, "video-onboarding", "video/mp4", 10, "onb", None, "CONSENTIMIENTO_REQUERIDO"),
        (CLIENTE, "foto-cliente", "image/jpeg", 10, "u-2", None, "PROPIETARIO_INVALIDO"),
    ],
)
def test_solicitar_subida_aplica_el_catalogo(
    entorno: Entorno, actor, tipo, contenido, tamano, propietario, consentimiento, codigo
) -> None:
    assert (
        _codigo(
            entorno.servicio.solicitar_subida,
            actor,
            tipo,
            contenido,
            tamano,
            propietario,
            consentimiento,
        )
        == codigo
    )
    assert entorno.repo.archivos == {}


def test_un_asesor_puede_subir_a_nombre_de_su_cliente(entorno: Entorno) -> None:
    resultado = entorno.servicio.solicitar_subida(ASESOR, "foto-cliente", "image/png", 64, "u-1")
    assert resultado["archivo"]["propietario_id"] == "u-1"


def test_derivado_de_exige_un_origen_disponible(entorno: Entorno) -> None:
    pendiente = entorno.servicio.solicitar_subida(CLIENTE, "foto-cliente", "image/jpeg", 64, "u-1")
    assert (
        _codigo(
            entorno.servicio.solicitar_subida,
            CLIENTE,
            "foto-cliente",
            "image/jpeg",
            64,
            "u-1",
            None,
            pendiente["archivo"]["id"],
        )
        == "ORIGEN_NO_ENCONTRADO"
    )
    origen = entorno.subir_foto()
    entorno.servicio.confirmar(origen["id"])
    hijo = entorno.servicio.solicitar_subida(
        CLIENTE, "foto-cliente", "image/jpeg", 64, "u-1", None, origen["id"]
    )
    assert hijo["archivo"]["derivado_de"] == origen["id"]


# ------------------------------------------------------------------ confirmar


def test_confirmar_deja_disponible_y_publica_el_evento(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()

    confirmado = entorno.servicio.confirmar(archivo["id"])

    assert confirmado["estado"] == "DISPONIBLE"
    assert confirmado["tamano_bytes"] == len(JPEG)
    assert [e["tipo"] for e in entorno.bus.eventos] == ["archivo.disponible"]
    assert entorno.bus.eventos[0]["datos"]["tipoArchivo"] == "foto-cliente"


def test_confirmar_es_idempotente_y_publica_una_sola_vez(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()
    entorno.servicio.confirmar(archivo["id"])
    otra_vez = entorno.servicio.confirmar(archivo["id"])

    assert otra_vez["estado"] == "DISPONIBLE"
    assert len(entorno.bus.eventos) == 1


def test_si_otro_resuelve_primero_no_se_publica_dos_veces(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()
    entorno.repo.perder_proxima_carrera = True

    resultado = entorno.servicio.confirmar(archivo["id"])

    assert resultado["estado"] == "DISPONIBLE"
    assert entorno.bus.eventos == []


def test_confirmar_sin_objeto_responde_objeto_no_subido(entorno: Entorno) -> None:
    pendiente = entorno.servicio.solicitar_subida(CLIENTE, "foto-cliente", "image/jpeg", 64, "u-1")
    assert _codigo(entorno.servicio.confirmar, pendiente["archivo"]["id"]) == "OBJETO_NO_SUBIDO"
    assert entorno.repo.obtener(pendiente["archivo"]["id"])["estado"] == "PENDIENTE"


@pytest.mark.parametrize(
    ("contenido", "tipo_real", "declarado", "motivo"),
    [
        (b"\xff\xd8\xff" + b"\x00" * 1200, "image/jpeg", 1000, "TAMANO_EXCEDIDO"),
        (JPEG, "image/jpeg", 10, "TAMANO_DISTINTO"),
        (PNG, "image/png", None, "TIPO_CONTENIDO_DISTINTO"),
        (b"MZ" + b"\x00" * 62, "image/jpeg", None, "CONTENIDO_NO_COINCIDE"),
    ],
)
def test_confirmar_rechaza_y_borra_lo_que_no_coincide(
    entorno: Entorno, contenido, tipo_real, declarado, motivo
) -> None:
    solicitud = entorno.servicio.solicitar_subida(
        CLIENTE, "foto-cliente", "image/jpeg", declarado or len(contenido), "u-1"
    )
    archivo = solicitud["archivo"]
    entorno.almacen.subir(archivo["objeto"], contenido, tipo_real)

    rechazado = entorno.servicio.confirmar(archivo["id"])

    assert rechazado["estado"] == "RECHAZADO"
    assert rechazado["motivo"] == motivo
    assert archivo["objeto"] in entorno.almacen.borrados
    assert entorno.bus.eventos[0]["tipo"] == "archivo.rechazado"
    assert entorno.bus.eventos[0]["datos"]["motivo"] == motivo


def test_confirmar_por_api_solo_lo_hace_quien_pidio_la_subida(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()
    assert (
        _codigo(entorno.servicio.confirmar, archivo["id"], OTRO_CLIENTE) == "ARCHIVO_NO_ENCONTRADO"
    )
    assert entorno.servicio.confirmar(archivo["id"], CLIENTE)["estado"] == "DISPONIBLE"


# ------------------------------------------------------------------ notificaciones del bucket


def test_object_finalize_confirma_y_object_delete_marca_eliminado(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()

    assert (
        entorno.servicio.notificacion_objeto("OBJECT_FINALIZE", archivo["objeto"]) == "DISPONIBLE"
    )
    assert (
        entorno.servicio.notificacion_objeto("OBJECT_FINALIZE", archivo["objeto"]) == "sin cambios"
    )
    assert entorno.servicio.notificacion_objeto("OBJECT_DELETE", archivo["objeto"]) == "ELIMINADO"
    assert entorno.repo.obtener(archivo["id"])["estado"] == "ELIMINADO"


def test_notificaciones_de_objetos_ajenos_o_de_rechazados_no_cambian_nada(entorno: Entorno) -> None:
    assert entorno.servicio.notificacion_objeto("OBJECT_FINALIZE", "otro/objeto") == "ignorado"
    archivo = entorno.subir_foto(contenido=b"MZ" + b"\x00" * 62)
    entorno.servicio.confirmar(archivo["id"])
    # Borrar el objeto rechazado también dispara OBJECT_DELETE: sigue RECHAZADO.
    assert entorno.servicio.notificacion_objeto("OBJECT_DELETE", archivo["objeto"]) == "sin cambios"
    assert entorno.repo.obtener(archivo["id"])["estado"] == "RECHAZADO"


# ------------------------------------------------------------------ leer


def test_un_cliente_solo_lee_lo_suyo_y_sin_revelar_que_existe(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()
    entorno.servicio.confirmar(archivo["id"])

    assert entorno.servicio.obtener(CLIENTE, archivo["id"])["id"] == archivo["id"]
    assert entorno.servicio.obtener(BACKOFFICE, archivo["id"])["id"] == archivo["id"]
    assert _codigo(entorno.servicio.obtener, OTRO_CLIENTE, archivo["id"]) == "ARCHIVO_NO_ENCONTRADO"
    assert _codigo(entorno.servicio.obtener, ONBOARDING, archivo["id"]) == "ARCHIVO_NO_ENCONTRADO"


def test_descarga_firma_una_url_y_deja_rastro(entorno: Entorno) -> None:
    archivo = entorno.subir_foto()
    assert _codigo(entorno.servicio.descarga, CLIENTE, archivo["id"]) == "ARCHIVO_NO_DISPONIBLE"
    entorno.servicio.confirmar(archivo["id"])

    descarga = entorno.servicio.descarga(CLIENTE, archivo["id"])

    assert "firma=descarga" in descarga["url"]
    assert descarga["vence_en"] == AHORA + timedelta(seconds=300)
    assert [a["accion"] for a in entorno.repo.accesos] == ["SUBIDA", "DESCARGA"]


def test_listar_filtra_por_lo_que_el_actor_puede_leer(entorno: Entorno) -> None:
    entorno.subir_foto()
    entorno.subir_foto()
    assert len(entorno.servicio.listar(CLIENTE, "usuario", "u-1")) == 2
    assert entorno.servicio.listar(OTRO_CLIENTE, "usuario", "u-1") == []


# ------------------------------------------------------------------ retención


def test_retencion_marca_custom_time_una_sola_vez(entorno: Entorno) -> None:
    video = entorno.subir_video()

    retenido = entorno.servicio.fijar_retencion(ONBOARDING, video["id"])
    entorno.ahora = AHORA + timedelta(days=1)
    otra_vez = entorno.servicio.fijar_retencion(ONBOARDING, video["id"])

    assert retenido["retener_desde"] == AHORA
    assert otra_vez["retener_desde"] == AHORA
    assert entorno.almacen.custom_time[video["objeto"]] == AHORA


def test_retencion_no_aplica_a_tipos_sin_retencion_ni_la_pide_un_cliente(entorno: Entorno) -> None:
    foto = entorno.subir_foto()
    entorno.servicio.confirmar(foto["id"])
    assert _codigo(entorno.servicio.fijar_retencion, CLIENTE, foto["id"]) == "NO_AUTORIZADO"
    assert _codigo(entorno.servicio.fijar_retencion, BACKOFFICE, foto["id"]) == "SIN_RETENCION"


def test_purgar_borra_solo_lo_vencido(entorno: Entorno) -> None:
    video = entorno.subir_video()
    entorno.servicio.fijar_retencion(ONBOARDING, video["id"])

    entorno.ahora = AHORA + timedelta(days=29)
    assert entorno.servicio.purgar_vencidos() == 0
    entorno.ahora = AHORA + timedelta(days=30)
    assert entorno.servicio.purgar_vencidos() == 1

    assert entorno.repo.obtener(video["id"])["estado"] == "ELIMINADO"
    assert video["objeto"] in entorno.almacen.borrados
    assert entorno.servicio.purgar_vencidos() == 0


def test_el_actor_debe_ser_coherente() -> None:
    with pytest.raises(ValueError):
        Actor("ADMIN", "x")
    with pytest.raises(ValueError):
        Actor("SERVICIO")
    with pytest.raises(ValueError):
        Actor("CLIENTE")
    assert ONBOARDING.nombre == "servicio:onboarding"
    assert CLIENTE.nombre == "CLIENTE"
