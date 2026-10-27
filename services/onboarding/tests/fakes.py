"""Dobles de prueba de ONBOARDING (sin PostgreSQL, storage ni ACL reales)."""

import copy
import itertools
from datetime import UTC, datetime
from typing import Any

from app.application.cifrado import CifradorAesGcm
from app.application.clientes import ErrorAlmacenamiento, ServicioNoDisponible
from app.application.repositorio import TitularYaVerificado
from app.application.servicio_onboarding import Reglas, ServicioOnboarding

AHORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
CLAVE_HMAC = "clave-de-prueba"

APROBADA = {
    "estado": "aprobada",
    "motivo": None,
    "datos": {
        "nombres": "ANA MARIA",
        "apellidos": "PRUEBA FICTICIA",
        "numero": "1000000001",
        "fecha_nacimiento": "1990-05-17",
        "pais": "COL",
        "tipo": "Identity Card",
    },
    "puntaje_rostro": 92.0,
    "puntaje_vida": 97.0,
    "alertas": [],
}


class RepositorioFalso:
    def __init__(self) -> None:
        self.sesiones: dict[str, dict[str, Any]] = {}
        self.datos: dict[str, dict[str, Any]] = {}
        self.verificaciones: list[dict[str, Any]] = []
        self.eventos: list[tuple[str, str, str | None]] = []
        self._reloj = itertools.count()

    def crear(self, sesion: dict[str, Any]) -> dict[str, Any]:
        fila = {
            "estado": "INICIADO",
            "intento": 1,
            "consentimiento_id": None,
            "usuario_id": None,
            "titular_id": None,
            "anverso_id": None,
            "reverso_id": None,
            "rostro_id": None,
            "video_id": None,
            "motivo": None,
            "creado_en": next(self._reloj),
            **sesion,
        }
        self.sesiones[fila["id"]] = fila
        return copy.deepcopy(fila)

    def obtener(self, sesion_id: str) -> dict[str, Any] | None:
        fila = self.sesiones.get(sesion_id)
        return copy.deepcopy(fila) if fila else None

    def obtener_por_usuario(self, usuario_id: str) -> dict[str, Any] | None:
        filas = [s for s in self.sesiones.values() if s["usuario_id"] == usuario_id]
        return copy.deepcopy(max(filas, key=lambda s: s["creado_en"])) if filas else None

    def actualizar(self, sesion_id, cambios, estados):
        fila = self.sesiones.get(sesion_id)
        if fila is None or fila["estado"] not in estados:
            return None
        nueva = {**fila, **cambios}
        if nueva["estado"] == "VERIFICADA" and any(
            s["titular_id"] == nueva["titular_id"] and s["estado"] == "VERIFICADA"
            for k, s in self.sesiones.items()
            if k != sesion_id
        ):
            raise TitularYaVerificado()
        self.sesiones[sesion_id] = nueva
        return copy.deepcopy(nueva)

    def titular_verificado_en_otra(self, titular_id, sesion_id) -> bool:
        return any(
            s["titular_id"] == titular_id and s["estado"] == "VERIFICADA" and k != sesion_id
            for k, s in self.sesiones.items()
        )

    def guardar_datos(self, sesion_id, datos) -> None:
        self.datos[sesion_id] = dict(datos)

    def registrar_verificacion(self, fila) -> None:
        self.verificaciones.append(dict(fila))

    def registrar_evento(self, sesion_id, tipo, detalle=None) -> None:
        self.eventos.append((sesion_id, tipo, detalle))


class AlmacenamientoFalso:
    def __init__(self) -> None:
        self.archivos: dict[str, dict[str, Any]] = {}
        self.retenidos: list[str] = []
        self.caido = False
        self._ids = itertools.count(1)

    def _revisar(self) -> None:
        if self.caido:
            raise ServicioNoDisponible("ConnectError")

    def solicitar_subida(self, tipo, tipo_contenido, tamano, propietario_id, consentimiento):
        self._revisar()
        if tipo == "onboarding-rostro" and tipo_contenido != "image/jpeg":
            raise ErrorAlmacenamiento("TIPO_CONTENIDO_NO_PERMITIDO", 422)
        archivo_id = f"a-{next(self._ids)}"
        self.archivos[archivo_id] = {
            "id": archivo_id,
            "tipo": tipo,
            "estado": "PENDIENTE",
            "propietario_id": propietario_id,
            "consentimiento_id": consentimiento,
        }
        return {
            "archivo": dict(self.archivos[archivo_id]),
            "url": f"http://gcs/{archivo_id}",
            "metodo": "PUT",
            "encabezados": {"Content-Type": tipo_contenido},
            "vence_en": AHORA.isoformat(),
        }

    def confirmar(self, archivo_id):
        self._revisar()
        self.archivos[archivo_id]["estado"] = "DISPONIBLE"
        return dict(self.archivos[archivo_id])

    def obtener(self, archivo_id):
        self._revisar()
        if archivo_id not in self.archivos:
            raise ErrorAlmacenamiento("ARCHIVO_NO_ENCONTRADO", 404)
        return dict(self.archivos[archivo_id])

    def url_descarga(self, archivo_id):
        self._revisar()
        if self.archivos.get(archivo_id, {}).get("estado") != "DISPONIBLE":
            raise ErrorAlmacenamiento("ARCHIVO_NO_DISPONIBLE", 409)
        return f"http://gcs/{archivo_id}?firma=x"

    def fijar_retencion(self, archivo_id):
        self._revisar()
        self.retenidos.append(archivo_id)


class IdentidadFalsa:
    def __init__(self, respuesta: dict[str, Any] | None = None) -> None:
        self.respuesta = respuesta or APROBADA
        self.llamadas: list[tuple] = []
        self.caido = False

    def verificar(self, referencia, anverso_url, rostro_url, reverso_url):
        if self.caido:
            raise ServicioNoDisponible("ConnectError")
        self.llamadas.append((referencia, anverso_url, rostro_url, reverso_url))
        return copy.deepcopy(self.respuesta)


class PlanificadorFalso:
    def __init__(self) -> None:
        self.encoladas: list[str] = []

    def encolar(self, sesion_id, demora_segundos=0) -> None:
        self.encoladas.append(sesion_id)


class BusFalso:
    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento) -> None:
        self.eventos.append(evento)


class Pila:
    def __init__(self, respuesta: dict[str, Any] | None = None, reloj=lambda: AHORA) -> None:
        self.repo = RepositorioFalso()
        self.storage = AlmacenamientoFalso()
        self.identidad = IdentidadFalsa(respuesta)
        self.planificador = PlanificadorFalso()
        self.bus = BusFalso()
        self.cifrador = CifradorAesGcm(b"k" * 32)
        self.servicio = ServicioOnboarding(
            self.repo,
            self.storage,
            self.identidad,
            self.cifrador,
            self.planificador,
            self.bus,
            Reglas(CLAVE_HMAC),
            reloj=reloj,
        )

    def sesion_lista(self) -> tuple[dict[str, Any], str]:
        """Una sesión con consentimiento y las cuatro piezas subidas y confirmadas."""
        sesion, token = self.servicio.iniciar("CO", "CC")
        sesion = self.servicio.registrar_consentimiento(sesion, "c-1")
        for paso, contenido in (
            ("ANVERSO", "image/jpeg"),
            ("REVERSO", "image/jpeg"),
            ("ROSTRO", "image/jpeg"),
            ("VIDEO", "video/mp4"),
        ):
            subida = self.servicio.solicitar_subida(
                self.repo.obtener(sesion["id"]), paso, contenido, 100
            )
            self.servicio.confirmar_subida(self.repo.obtener(sesion["id"]), subida["archivo_id"])
        return self.repo.obtener(sesion["id"]), token

    def verificada(self) -> dict[str, Any]:
        sesion, _ = self.sesion_lista()
        self.servicio.enviar(sesion)
        return self.servicio.verificar(sesion["id"])
