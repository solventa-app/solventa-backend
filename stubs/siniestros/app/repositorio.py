"""Datos del dummy de siniestros: se cargan de `app/datos/*.json` y viven en memoria.

Las acciones (aprobar, rechazar, derivar) cambian el estado mientras el proceso esté vivo;
`reiniciar()` vuelve a los datos originales. No hay base de datos ni llamadas a otros servicios.

Las horas relativas del JSON (`vence_dictamen_horas`) se convierten en fechas al cargar, para que
la cuenta regresiva del detalle siempre tenga sentido en una demo, sin importar el día.
"""

import copy
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

DATOS = Path(__file__).resolve().parent / "datos"
HORA_COLOMBIA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano
MAX_TEXTO = 500

ABIERTOS = ("PENDIENTE", "EN_REVISION")

MENSAJE_NO_EXISTE = "No encontramos el siniestro."
MENSAJE_YA_RESUELTO = "El siniestro ya fue resuelto y no admite cambios."
MENSAJE_YA_DERIVADO = "El siniestro ya tiene un perito asignado."
MENSAJE_PERITO = "El perito seleccionado no está disponible."
MENSAJE_TEXTO_LARGO = f"El texto supera los {MAX_TEXTO} caracteres."

METODO_TRANSFERENCIA = "ACH Colombia Interbancario"
PLAZO_DISPERSION = "1 a 3 días hábiles"
NOTIFICACION_APROBACION = "Enviada al asegurado y a la entidad acreedora"
NOTIFICACION_RECHAZO = "Enviada vía correo certificado y Open Finance"
NOTIFICACION_DERIVACION = "Correo certificado y expediente digital de siniestros"
VIA_RECURSO = (
    "Disponible en los próximos 10 días hábiles conforme al Código de Comercio y Circular "
    "Básica Jurídica de la SFC."
)


class ErrorNegocio(Exception):
    """Regla de negocio incumplida: el mensaje es seguro para mostrar al usuario."""


def _leer(nombre: str):
    return json.loads((DATOS / nombre).read_text(encoding="utf-8"))


def _fecha(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


def sumar_dias_habiles(desde: datetime, dias: int) -> datetime:
    """Suma días hábiles (lunes a viernes) y fija el vencimiento a las 5:00 p. m. hora Colombia.
    No descuenta festivos: es un dummy."""
    fecha = desde.astimezone(HORA_COLOMBIA)
    restantes = dias
    while restantes > 0:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            restantes -= 1
    return fecha.replace(hour=17, minute=0, second=0, microsecond=0)


class Repositorio:
    def __init__(self, reloj: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._reloj = reloj
        self._candado = threading.Lock()
        self.reiniciar()

    def reiniciar(self) -> int:
        ahora = self._reloj()
        siniestros = _leer("siniestros.json")
        for s in siniestros:
            s["vence_dictamen_en"] = ahora + timedelta(hours=s.pop("vence_dictamen_horas"))
        with self._candado:
            self._siniestros = {s["id"]: s for s in siniestros}
            self._polizas = {p["id"]: p for p in _leer("polizas.json")}
            self._peritos = {p["id"]: p for p in _leer("peritos.json")}
        return len(siniestros)

    # ---------- Lectura ----------

    def listar(self, estado: str | None = None) -> list[dict]:
        with self._candado:
            todos = [copy.deepcopy(s) for s in self._siniestros.values()]
        if estado:
            todos = [s for s in todos if s["estado"] == estado]
        return sorted(todos, key=lambda s: s["notificado_en"], reverse=True)

    def obtener(self, siniestro_id: str) -> dict | None:
        with self._candado:
            s = self._siniestros.get(siniestro_id)
            return copy.deepcopy(s) if s else None

    def poliza(self, poliza_id: str) -> dict:
        return copy.deepcopy(self._polizas[poliza_id])

    def peritos(self, siniestro_id: str) -> list[dict]:
        """Peritos disponibles, con el vencimiento legal de su informe si se le deriva hoy."""
        if self.obtener(siniestro_id) is None:
            return []
        ahora = self._reloj()
        return [
            {**p, "vencimiento_informe": sumar_dias_habiles(ahora, p["tiempo_respuesta_dias"])}
            for p in self._peritos.values()
        ]

    def perito(self, perito_id: str) -> dict | None:
        p = self._peritos.get(perito_id)
        return copy.deepcopy(p) if p else None

    # ---------- Acciones ----------

    def _abierto(self, siniestro_id: str) -> dict:
        s = self._siniestros.get(siniestro_id)
        if s is None:
            raise ErrorNegocio(MENSAJE_NO_EXISTE)
        if s["estado"] not in ABIERTOS:
            raise ErrorNegocio(MENSAJE_YA_RESUELTO)
        return s

    @staticmethod
    def _validar_texto(texto: str | None) -> str | None:
        texto = (texto or "").strip() or None
        if texto and len(texto) > MAX_TEXTO:
            raise ErrorNegocio(MENSAJE_TEXTO_LARGO)
        return texto

    def aprobar(self, siniestro_id: str) -> dict:
        with self._candado:
            s = self._abierto(siniestro_id)
            s["estado"] = "APROBADO"
            s["resolucion"] = {
                "tipo": "APROBACION",
                "resuelta_en": self._reloj().isoformat(),
                "notificacion": NOTIFICACION_APROBACION,
                "indemnizacion": copy.deepcopy(s["suma_reclamada"]),
                "metodo_transferencia": METODO_TRANSFERENCIA,
                "plazo_dispersion": PLAZO_DISPERSION,
                "perito_id": (s.get("resolucion") or {}).get("perito_id"),
            }
            return copy.deepcopy(s)

    def rechazar(self, siniestro_id: str, motivo: str, comentario: str | None) -> dict:
        with self._candado:
            s = self._abierto(siniestro_id)
            comentario = self._validar_texto(comentario)
            s["estado"] = "RECHAZADO"
            s["resolucion"] = {
                "tipo": "RECHAZO",
                "resuelta_en": self._reloj().isoformat(),
                "notificacion": NOTIFICACION_RECHAZO,
                "motivo": motivo,
                "comentario": comentario,
                "via_recurso": VIA_RECURSO,
                "perito_id": (s.get("resolucion") or {}).get("perito_id"),
            }
            return copy.deepcopy(s)

    def derivar(self, siniestro_id: str, perito_id: str, instrucciones: str | None) -> dict:
        with self._candado:
            s = self._abierto(siniestro_id)
            if (s.get("resolucion") or {}).get("perito_id"):
                raise ErrorNegocio(MENSAJE_YA_DERIVADO)
            perito = self._peritos.get(perito_id)
            if perito is None:
                raise ErrorNegocio(MENSAJE_PERITO)
            instrucciones = self._validar_texto(instrucciones)
            ahora = self._reloj()
            s["estado"] = "EN_REVISION"
            s["resolucion"] = {
                "tipo": "DERIVACION",
                "resuelta_en": ahora.isoformat(),
                "notificacion": NOTIFICACION_DERIVACION,
                "perito_id": perito_id,
                "instrucciones": instrucciones,
                "vencimiento_informe": sumar_dias_habiles(
                    ahora, perito["tiempo_respuesta_dias"]
                ).isoformat(),
            }
            return copy.deepcopy(s)


def a_fecha(valor: str | datetime | None) -> datetime | None:
    if valor is None or isinstance(valor, datetime):
        return valor
    return _fecha(valor)
