"""Mesa de siniestros (HU-W04): arma el expediente que consume el BFF.

Capas simples (regla #3): el servicio aplica las reglas y el repositorio guarda. La forma de salida es
la del contrato (`contracts/graphql/schema.graphql`, v0.19) en snake_case; el BFF solo resuelve la
póliza con POLICY y la URL firmada de cada evidencia con el almacenamiento.

La `resolucion` del contrato sale de dos colecciones: la aprobación o el rechazo
(`resoluciones_siniestro`) si existe; si no, la derivación a perito (`asignaciones_perito`), que deja
el caso EN_REVISION.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.application.dias_habiles import sumar_dias_habiles


class PuertoRepositorio(Protocol):
    def listar(self, estado: str | None = None) -> list[dict]: ...

    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None: ...

    def evidencias(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]: ...

    def verificaciones(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]: ...

    def asignaciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]: ...

    def resoluciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]: ...

    def peritos(self, primaria: bool = False) -> list[dict]: ...

    def perito(self, perito_id: str, primaria: bool = False) -> dict | None: ...


def _perito(perito: dict | None) -> dict | None:
    if perito is None:
        return None
    return {
        "id": perito["id"],
        "nombre": perito["nombre"],
        "ciudad": perito["ciudad"],
        "red": perito["red"],
        "registro": perito["registro"],
        "tiempo_respuesta_dias": perito["tiempo_respuesta_dias"],
    }


def _evidencia(evidencia: dict) -> dict:
    return {
        "id": evidencia["id"],
        "archivo_id": evidencia["archivo_id"],
        "tipo": evidencia["tipo"],
        "titulo": evidencia["titulo"],
        "archivo": evidencia["nombre_archivo"],
        "registrada_en": evidencia["registrada_en"],
        "geoetiquetada": evidencia["geoetiquetada"],
        "latitud": evidencia.get("latitud"),
        "longitud": evidencia.get("longitud"),
        "nota": evidencia.get("nota"),
    }


class ServicioSiniestros:
    def __init__(
        self,
        repositorio: PuertoRepositorio,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._reloj = reloj

    # ---------- Lectura ----------

    def listar(self, estado: str | None = None) -> list[dict]:
        return self._armar(self._repo.listar(estado), primaria=False)

    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None:
        siniestro = self._repo.obtener(siniestro_id, primaria=primaria)
        if siniestro is None:
            return None
        return self._armar([siniestro], primaria=primaria)[0]

    def peritos_para(self, siniestro_id: str) -> list[dict]:
        """Peritos disponibles, con la fecha límite del informe si se le deriva hoy. Caso
        inexistente: lista vacía."""
        if self._repo.obtener(siniestro_id) is None:
            return []
        ahora = self._reloj()
        return [
            {
                **_perito(p),
                "vencimiento_informe": sumar_dias_habiles(ahora, p["tiempo_respuesta_dias"]),
            }
            for p in self._repo.peritos()
        ]

    def _armar(self, siniestros: list[dict], primaria: bool) -> list[dict]:
        ids = [s["id"] for s in siniestros]
        if not ids:
            return []
        evidencias = self._repo.evidencias(ids, primaria=primaria)
        verificaciones = self._repo.verificaciones(ids, primaria=primaria)
        asignaciones = self._repo.asignaciones(ids, primaria=primaria)
        resoluciones = self._repo.resoluciones(ids, primaria=primaria)
        peritos = {p["id"]: p for p in self._repo.peritos(primaria=primaria)}
        expedientes = []
        for s in siniestros:
            sid = s["id"]
            asignacion = asignaciones.get(sid)
            perito = None
            if asignacion is not None:
                perito = peritos.get(asignacion["perito_id"]) or self._repo.perito(
                    asignacion["perito_id"], primaria=primaria
                )
            expedientes.append(
                {
                    **{k: v for k, v in s.items()},
                    "evidencias": [_evidencia(e) for e in evidencias.get(sid, [])],
                    "verificaciones": [
                        {"descripcion": v["descripcion"], "cumple": v["cumple"]}
                        for v in verificaciones.get(sid, [])
                    ],
                    "resolucion": self._resolucion(resoluciones.get(sid), asignacion, perito),
                }
            )
        return expedientes

    @staticmethod
    def _resolucion(
        resolucion: dict | None, asignacion: dict | None, perito: dict | None
    ) -> dict | None:
        if resolucion is not None:
            return {
                "tipo": resolucion["tipo"],
                "resuelta_en": resolucion["resuelta_en"],
                "notificacion": resolucion["notificacion"],
                "motivo": resolucion.get("motivo"),
                "comentario": resolucion.get("comentario"),
                "via_recurso": resolucion.get("via_recurso"),
                "indemnizacion": resolucion.get("indemnizacion"),
                "metodo_transferencia": resolucion.get("metodo_transferencia"),
                "plazo_dispersion": resolucion.get("plazo_dispersion"),
                "orden_pago_id": resolucion.get("orden_pago_id"),
                # Si el caso pasó por un perito, la resolución final lo conserva (como el dummy).
                "perito": _perito(perito),
                "instrucciones": None,
                "vencimiento_informe": None,
            }
        if asignacion is not None:
            return {
                "tipo": "DERIVACION",
                "resuelta_en": asignacion["asignada_en"],
                "notificacion": asignacion["notificacion"],
                "motivo": None,
                "comentario": None,
                "via_recurso": None,
                "indemnizacion": None,
                "metodo_transferencia": None,
                "plazo_dispersion": None,
                "orden_pago_id": None,
                "perito": _perito(perito),
                "instrucciones": asignacion.get("instrucciones"),
                "vencimiento_informe": asignacion["vence_informe_en"],
            }
        return None
