"""Recorrido de onboarding de HU-M01: sesión, capturas, verificación y reglas de aprobación.

Capa simple (regla 3). El flujo:
1. `iniciar` crea la sesión y un token de 30 min que solo la app conoce (se guarda su hash).
2. Con el consentimiento `onboarding-biometrico` registrado (lo exige el BFF en AUTH y aquí se
   guarda su id), la app pide una URL firmada por cada pieza (anverso, reverso, rostro, video) y
   sube directo al bucket; `confirmar_subida` es el respaldo de la notificación del bucket.
3. `enviar` comprueba que las cuatro piezas estén DISPONIBLE y agenda la verificación (Cloud
   Tasks o un hilo en local).
4. `verificar` manda las imágenes al ACL Worker (`/identidad/verificar`, Didit detrás) y aplica las
   reglas: documento auténtico, rostro y prueba de vida (el proveedor), edad mínima y un documento
   por cuenta (`titular_id`). Con el proveedor caído la sesión queda EN_REVISION (EC010).

Hasta 3 intentos por sesión (P-7). Nada de datos personales en logs, eventos ni métricas: lo que
leyó el proveedor se guarda cifrado por campo y solo en sesiones VERIFICADAS."""

import json
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento
from app.application.cifrado import CifradorCampos
from app.application.clientes import (
    ErrorAlmacenamiento,
    PuertoAlmacenamiento,
    PuertoIdentidad,
    ServicioNoDisponible,
)
from app.application.identificadores import hash_token, id_opaco, titular_id_desde_documento
from app.application.planificador import PuertoPlanificador, VerificacionPendiente
from app.application.repositorio import TitularYaVerificado

logger = logging.getLogger("onboarding")

JURISDICCIONES = frozenset({"CO"})
TIPOS_DOCUMENTO = frozenset({"CC", "CE"})
# Paso de la app -> (tipo del catálogo de archivos, columna de la sesión).
PASOS = {
    "ANVERSO": ("onboarding-documento", "anverso_id"),
    "REVERSO": ("onboarding-documento", "reverso_id"),
    "ROSTRO": ("onboarding-rostro", "rostro_id"),
    "VIDEO": ("onboarding-video", "video_id"),
}
COLUMNAS_PIEZAS = tuple(columna for _, columna in PASOS.values())
# Motivo del ACL -> motivo de M01 (contrato v0.20).
MOTIVOS_PROVEEDOR = {
    "documento_rechazado": "ILEGIBLE",
    "prueba_de_vida_fallida": "SUPLANTACION",
    "rostro_no_coincide": "ROSTRO_NO_COINCIDE",
}
SIN_REINTENTO = frozenset({"DOCUMENTO_YA_REGISTRADO"})


class ErrorOnboarding(Exception):
    def __init__(self, codigo: str, estado_http: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.estado_http = estado_http


class PuertoRepositorio(Protocol):
    def crear(self, sesion: dict[str, Any]) -> dict[str, Any]: ...
    def obtener(self, sesion_id: str) -> dict[str, Any] | None: ...
    def obtener_por_usuario(self, usuario_id: str) -> dict[str, Any] | None: ...
    def actualizar(
        self, sesion_id: str, cambios: dict[str, Any], estados: tuple[str, ...]
    ) -> dict[str, Any] | None: ...
    def titular_verificado_en_otra(self, titular_id: str, sesion_id: str) -> bool: ...
    def guardar_datos(self, sesion_id: str, datos: dict[str, str | None]) -> None: ...
    def registrar_verificacion(self, fila: dict[str, Any]) -> None: ...
    def registrar_evento(self, sesion_id: str, tipo: str, detalle: str | None = None) -> None: ...


@dataclass(frozen=True)
class Reglas:
    cliente_id_clave: str
    edad_minima: int = 18
    max_intentos: int = 3
    vigencia_token_s: int = 1800


def _edad(nacimiento: date, hoy: date) -> int:
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


class ServicioOnboarding:
    def __init__(
        self,
        repo: PuertoRepositorio,
        almacenamiento: PuertoAlmacenamiento,
        identidad: PuertoIdentidad,
        cifrador: CifradorCampos,
        planificador: PuertoPlanificador,
        bus: PuertoBusEventos,
        reglas: Reglas,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repo
        self._storage = almacenamiento
        self._identidad = identidad
        self._cifrador = cifrador
        self._planificador = planificador
        self._bus = bus
        self._reglas = reglas
        self._reloj = reloj

    # --- Sesión y token -------------------------------------------------------------------

    def iniciar(self, jurisdiccion: str, tipo_documento: str) -> tuple[dict[str, Any], str]:
        if jurisdiccion not in JURISDICCIONES:
            raise ErrorOnboarding("JURISDICCION_NO_SOPORTADA", 422)
        if tipo_documento not in TIPOS_DOCUMENTO:
            raise ErrorOnboarding("TIPO_DOCUMENTO_NO_SOPORTADO", 422)
        token = secrets.token_urlsafe(32)
        sesion = self._repo.crear(
            {
                "id": str(uuid.uuid4()),
                "token_hash": hash_token(token),
                "token_vence_en": self._reloj() + timedelta(seconds=self._reglas.vigencia_token_s),
                "jurisdiccion": jurisdiccion,
                "tipo_documento": tipo_documento,
            }
        )
        self._repo.registrar_evento(sesion["id"], "INICIADA")
        logger.info("sesion=%s iniciada", id_opaco(sesion["id"]))
        return sesion, token

    def autenticar(self, sesion_id: str, token: str | None) -> dict[str, Any]:
        """La sesión si el token es el suyo y sigue vigente. Mismo 401 para todo lo demás: no
        revela si la sesión existe."""
        sesion = self._repo.obtener(sesion_id)
        if (
            sesion is None
            or not token
            or not secrets.compare_digest(sesion["token_hash"], hash_token(token))
            or sesion["token_vence_en"] <= self._reloj()
        ):
            raise ErrorOnboarding("TOKEN_INVALIDO", 401)
        return sesion

    def obtener(self, sesion_id: str) -> dict[str, Any]:
        sesion = self._repo.obtener(sesion_id)
        if sesion is None:
            raise ErrorOnboarding("SESION_NO_ENCONTRADA", 404)
        return sesion

    def de_usuario(self, usuario_id: str) -> dict[str, Any]:
        sesion = self._repo.obtener_por_usuario(usuario_id)
        if sesion is None:
            raise ErrorOnboarding("SESION_NO_ENCONTRADA", 404)
        return sesion

    def vista(self, sesion: dict[str, Any]) -> dict[str, Any]:
        restantes = self._reglas.max_intentos - sesion["intento"]
        if sesion["estado"] == "RECHAZADA" and sesion["motivo"] in SIN_REINTENTO:
            restantes = 0
        return {
            "id": sesion["id"],
            "estado": sesion["estado"],
            "motivo": sesion["motivo"],
            "intento": sesion["intento"],
            "intentos_restantes": max(0, restantes),
        }

    def _cambiar(
        self, sesion: dict[str, Any], cambios: dict[str, Any], estados: tuple[str, ...]
    ) -> dict[str, Any]:
        nueva = self._repo.actualizar(sesion["id"], cambios, estados)
        if nueva is None:
            raise ErrorOnboarding("ESTADO_INVALIDO", 409)
        return nueva

    # --- Capturas -------------------------------------------------------------------------

    def registrar_consentimiento(
        self, sesion: dict[str, Any], consentimiento_id: str
    ) -> dict[str, Any]:
        nueva = self._cambiar(sesion, {"consentimiento_id": consentimiento_id}, ("INICIADO",))
        self._repo.registrar_evento(sesion["id"], "CONSENTIMIENTO")
        return nueva

    def vincular_cuenta(self, sesion: dict[str, Any], usuario_id: str) -> dict[str, Any]:
        if sesion["usuario_id"] == usuario_id:
            return sesion
        if sesion["usuario_id"] is not None:
            raise ErrorOnboarding("SESION_CON_CUENTA", 409)
        nueva = self._cambiar(
            sesion,
            {"usuario_id": usuario_id},
            ("INICIADO", "EN_VERIFICACION", "VERIFICADA", "RECHAZADA", "EN_REVISION"),
        )
        self._repo.registrar_evento(sesion["id"], "CUENTA_VINCULADA")
        return nueva

    def solicitar_subida(
        self, sesion: dict[str, Any], paso: str, tipo_contenido: str, tamano_bytes: int
    ) -> dict[str, Any]:
        if paso not in PASOS:
            raise ErrorOnboarding("PASO_INVALIDO", 422)
        if not sesion["consentimiento_id"]:
            raise ErrorOnboarding("SIN_CONSENTIMIENTO", 409)
        if sesion["estado"] != "INICIADO":
            raise ErrorOnboarding("ESTADO_INVALIDO", 409)
        tipo, columna = PASOS[paso]
        try:
            subida = self._storage.solicitar_subida(
                tipo, tipo_contenido, tamano_bytes, sesion["id"], sesion["consentimiento_id"]
            )
        except ErrorAlmacenamiento as error:
            raise ErrorOnboarding(error.codigo, 422 if error.estado_http == 422 else 409) from error
        except ServicioNoDisponible as error:
            raise ErrorOnboarding("ALMACENAMIENTO_NO_DISPONIBLE", 503) from error
        # Repetir un paso reemplaza la pieza: la anterior queda huérfana y vence con su retención.
        self._cambiar(sesion, {columna: subida["archivo"]["id"]}, ("INICIADO",))
        return {
            "archivo_id": subida["archivo"]["id"],
            "url": subida["url"],
            "metodo": subida["metodo"],
            "encabezados": subida["encabezados"],
            "vence_en": subida["vence_en"],
        }

    def confirmar_subida(self, sesion: dict[str, Any], archivo_id: str) -> str:
        if archivo_id not in {sesion[c] for c in COLUMNAS_PIEZAS}:
            raise ErrorOnboarding("ARCHIVO_NO_ENCONTRADO", 404)
        try:
            return self._storage.confirmar(archivo_id)["estado"]
        except ErrorAlmacenamiento as error:
            raise ErrorOnboarding(error.codigo, 409) from error
        except ServicioNoDisponible as error:
            raise ErrorOnboarding("ALMACENAMIENTO_NO_DISPONIBLE", 503) from error

    # --- Verificación ---------------------------------------------------------------------

    def enviar(self, sesion: dict[str, Any]) -> dict[str, Any]:
        """Idempotente: una sesión ya EN_VERIFICACION se vuelve a encolar (la tarea no duplica)."""
        if sesion["estado"] == "EN_VERIFICACION":
            self._planificador.encolar(sesion["id"])
            return sesion
        if sesion["estado"] != "INICIADO":
            raise ErrorOnboarding("ESTADO_INVALIDO", 409)
        try:
            listas = all(
                sesion[c] and self._storage.obtener(sesion[c])["estado"] == "DISPONIBLE"
                for c in COLUMNAS_PIEZAS
            )
        except ErrorAlmacenamiento:
            listas = False
        except ServicioNoDisponible as error:
            raise ErrorOnboarding("ALMACENAMIENTO_NO_DISPONIBLE", 503) from error
        if not listas:
            raise ErrorOnboarding("PIEZAS_INCOMPLETAS", 409)
        nueva = self._cambiar(sesion, {"estado": "EN_VERIFICACION"}, ("INICIADO",))
        self._repo.registrar_evento(sesion["id"], "ENVIADA", f"intento={sesion['intento']}")
        self._planificador.encolar(sesion["id"])
        return nueva

    def verificar(self, sesion_id: str) -> dict[str, Any]:
        """La tarea. Solo avanza desde EN_VERIFICACION (idempotente). Una falla transitoria del
        storage o del ACL lanza `VerificacionPendiente` y la tarea se reintenta."""
        sesion = self.obtener(sesion_id)
        if sesion["estado"] != "EN_VERIFICACION":
            return sesion
        try:
            anverso, reverso, rostro = (
                self._storage.url_descarga(sesion[c])
                for c in ("anverso_id", "reverso_id", "rostro_id")
            )
        except ErrorAlmacenamiento:
            return self._cerrar(sesion, "EN_REVISION", "IMAGEN_NO_DISPONIBLE")
        except ServicioNoDisponible as error:
            raise VerificacionPendiente("almacenamiento") from error
        try:
            resultado = self._identidad.verificar(
                f"{sesion['id']}-{sesion['intento']}", anverso, rostro, reverso
            )
        except ServicioNoDisponible as error:
            raise VerificacionPendiente("acl") from error

        if resultado["estado"] == "pendiente":
            return self._cerrar(sesion, "EN_REVISION", "PROVEEDOR_NO_DISPONIBLE", resultado)
        if resultado["estado"] == "rechazada":
            motivo = MOTIVOS_PROVEEDOR.get(resultado.get("motivo") or "", "ILEGIBLE")
            return self._cerrar(sesion, "RECHAZADA", motivo, resultado)
        return self._aprobar(sesion, resultado)

    def _aprobar(self, sesion: dict[str, Any], resultado: dict[str, Any]) -> dict[str, Any]:
        datos = resultado.get("datos") or {}
        numero = (datos.get("numero") or "").strip()
        try:
            nacimiento = date.fromisoformat(datos.get("fecha_nacimiento") or "")
        except ValueError:
            nacimiento = None
        if not numero or nacimiento is None:
            return self._cerrar(sesion, "RECHAZADA", "ILEGIBLE", resultado)
        if _edad(nacimiento, self._reloj().date()) < self._reglas.edad_minima:
            return self._cerrar(sesion, "RECHAZADA", "MENOR_DE_EDAD", resultado)
        titular_id = titular_id_desde_documento(numero, self._reglas.cliente_id_clave)
        if self._repo.titular_verificado_en_otra(titular_id, sesion["id"]):
            return self._cerrar(sesion, "RECHAZADA", "DOCUMENTO_YA_REGISTRADO", resultado)
        dueno = sesion["id"]
        self._repo.guardar_datos(
            dueno,
            {
                campo: self._cifrador.cifrar(str(datos[campo]), dueno) if datos.get(campo) else None
                for campo in ("nombres", "apellidos", "numero", "fecha_nacimiento", "pais")
            },
        )
        try:
            return self._cerrar(sesion, "VERIFICADA", None, resultado, titular_id=titular_id)
        except TitularYaVerificado:
            # Otra sesión con el mismo documento se verificó entre la consulta y el cierre.
            return self._cerrar(sesion, "RECHAZADA", "DOCUMENTO_YA_REGISTRADO", resultado)

    def _cerrar(
        self,
        sesion: dict[str, Any],
        estado: str,
        motivo: str | None,
        resultado: dict[str, Any] | None = None,
        titular_id: str | None = None,
    ) -> dict[str, Any]:
        cambios: dict[str, Any] = {"estado": estado, "motivo": motivo}
        if titular_id:
            cambios["titular_id"] = titular_id
        nueva = self._repo.actualizar(sesion["id"], cambios, ("EN_VERIFICACION",))
        if nueva is None:  # otra tarea ya la cerró
            return self.obtener(sesion["id"])
        resultado = resultado or {}
        self._repo.registrar_verificacion(
            {
                "sesion_id": sesion["id"],
                "intento": sesion["intento"],
                "estado": estado,
                "motivo": motivo,
                "puntaje_rostro": resultado.get("puntaje_rostro"),
                "puntaje_vida": resultado.get("puntaje_vida"),
                "alertas": json.dumps(list(resultado.get("alertas") or [])),
            }
        )
        self._repo.registrar_evento(sesion["id"], estado, motivo)
        logger.info(
            "sesion=%s intento=%s -> %s %s",
            id_opaco(sesion["id"]),
            sesion["intento"],
            estado,
            motivo or "",
        )
        if estado == "VERIFICADA":
            self._bus.publicar(construir_evento("onboarding.verificado", nueva))
        elif estado == "RECHAZADA":
            self._bus.publicar(construir_evento("onboarding.rechazado", nueva))
        self._fijar_retencion(nueva)
        return nueva

    def _fijar_retencion(self, sesion: dict[str, Any]) -> None:
        """Desde el cierre corren los 30 días de retención de cada pieza. Mejor esfuerzo: si el
        storage no responde, las piezas se quedan sin fecha y se reintenta en el próximo cierre."""
        for columna in COLUMNAS_PIEZAS:
            if not sesion[columna]:
                continue
            try:
                self._storage.fijar_retencion(sesion[columna])
            except (ErrorAlmacenamiento, ServicioNoDisponible) as error:
                logger.warning(
                    "sin retención sesion=%s pieza=%s error=%s",
                    id_opaco(sesion["id"]),
                    columna,
                    type(error).__name__,
                )

    def reintentar(self, sesion: dict[str, Any]) -> dict[str, Any]:
        if sesion["estado"] != "RECHAZADA" or self.vista(sesion)["intentos_restantes"] <= 0:
            raise ErrorOnboarding("SIN_INTENTOS", 409)
        cambios: dict[str, Any] = {c: None for c in COLUMNAS_PIEZAS}
        cambios.update(estado="INICIADO", motivo=None, intento=sesion["intento"] + 1)
        nueva = self._cambiar(sesion, cambios, ("RECHAZADA",))
        self._repo.registrar_evento(sesion["id"], "REINTENTO", f"intento={nueva['intento']}")
        return nueva
