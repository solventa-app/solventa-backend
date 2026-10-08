"""Productor de jobs de reconciliación diferida (ampliación de alcance pedida
directamente por el usuario, fuera de `docs/sprint-1.md` original — ver la nota
en ese archivo y en `docs/arquitectura-backend.md`).

Cada vez que `ServicioConsultaFuentes.consultar()` (`servicio_fuentes.py`) cae
en la rama de falla (circuito abierto o cualquier excepción) — sin importar si
termina respondiendo `de_cache=True` o `degradado=True` — además de responder
al llamador, se encola (fire-and-forget, nunca bloquea ni tumba esa respuesta)
un job para repetir la consulta real más tarde. El Consolidador
(`services/consolidador-fuentes/`) lo consume y vuelve a llamar al ACL Worker
por HTTP — nunca al proveedor directo (regla de arquitectura #2).

Diseño TRADUCIDO (no copiado) del Consolidador KYC del Experimento 1
(Node.js/TypeScript + BullMQ) a Python + `rq` (Redis Queue), aplicado a Open
Finance y Open Data — KYC sigue diferido al Sprint 2.

Mismo patrón que `PuertoAlmacenClaveValor` en `cache.py`: un puerto mínimo,
protocolo inyectable, para poder testear con un fake sin levantar Redis/RQ de
verdad.
"""

import hashlib
import logging
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("acl_worker.reconciliacion")

NOMBRE_COLA = "fuentes-reconciliacion"
DEDUP_TTL_SEGUNDOS = 30


@dataclass(frozen=True)
class TrabajoReconciliacion:
    """Lo mínimo para repetir la consulta real más tarde (contrato punto 1):
    `fuente`, `cliente_id`, `consentimiento_id` y el instante en que se encoló.
    El `cliente_id` SÍ viaja en crudo dentro del payload del job (en Redis) —
    es imprescindible para poder repetir la consulta real, igual que en el
    diseño original de KYC — pero nunca se loguea en crudo (ver `id_opaco`)."""

    fuente: str
    cliente_id: str
    consentimiento_id: str
    encolado_en: float  # epoch segundos (time.time())

    def a_dict(self) -> dict[str, str | float]:
        return asdict(self)


class PuertoColaReconciliacion(Protocol):
    """Lo mínimo que el productor necesita de una cola de reconciliación."""

    def encolar(self, trabajo: TrabajoReconciliacion) -> None: ...


class PuertoMarcaDedup(Protocol):
    """Lo mínimo para la marca de dedup (contrato punto 2)."""

    def marcar_si_ausente(self, fuente: str, cliente_id: str) -> bool:
        """True si la marca se creó ahora (primera vez en la ventana);
        False si ya existía (no se debe encolar de nuevo)."""
        ...


class PuertoAlmacenClaveValorNX(Protocol):
    """Lo mínimo de un cliente Redis para la marca de dedup (`SET ... NX EX`,
    atómico). Protocolo separado del `PuertoAlmacenClaveValor` de `cache.py`
    (que no necesita `nx`) para no acoplar la caché de lectura a la cola."""

    def set(
        self, nombre: str, valor: str, ex: int | None = None, nx: bool = False
    ) -> object: ...


class MarcaDedupRedis:
    """Evita inundar la cola si muchas peticiones degradan al mismo
    `(fuente, cliente)` en la misma ventana corta (~30 s, contrato punto 2).
    `SET clave 1 NX EX 30` es atómico en Redis: dos peticiones concurrentes
    nunca encolan dos jobs para el mismo `(fuente, cliente)` en esa ventana."""

    def __init__(
        self, almacen: PuertoAlmacenClaveValorNX, ttl_segundos: int = DEDUP_TTL_SEGUNDOS
    ) -> None:
        self._almacen = almacen
        self._ttl_segundos = ttl_segundos

    @staticmethod
    def _clave(fuente: str, cliente_id: str) -> str:
        hash_cliente = hashlib.sha256(cliente_id.encode("utf-8")).hexdigest()
        return f"acl:reconciliacion:dedup:{fuente}:{hash_cliente}"

    def marcar_si_ausente(self, fuente: str, cliente_id: str) -> bool:
        try:
            creada = self._almacen.set(
                self._clave(fuente, cliente_id), "1", ex=self._ttl_segundos, nx=True
            )
        except Exception:
            # Si Redis falla acá, se prefiere encolar de más (reconciliar de
            # más es barato e idempotente: solo repite la consulta real) a
            # perder silenciosamente la oportunidad de reconciliar.
            return True
        return bool(creada)


class ColaReconciliacionRQ:
    """Adaptador delgado sobre `rq.Queue`. El job se encola por RUTA DE IMPORT
    (string), no por referencia de función: `acl-worker` (productor) no conoce
    ni importa el código de `consolidador-fuentes` (son servicios/imágenes
    distintas) — solo el Consolidador necesita poder importar
    `app.tareas.reconciliar_fuente` para ejecutarlo."""

    RUTA_TAREA = "app.tareas.reconciliar_fuente"

    def __init__(self, cola: object, reintentos: object) -> None:
        self._cola = cola
        self._reintentos = reintentos

    def encolar(self, trabajo: TrabajoReconciliacion) -> None:
        self._cola.enqueue(  # type: ignore[attr-defined]
            self.RUTA_TAREA,
            fuente=trabajo.fuente,
            cliente_id=trabajo.cliente_id,
            consentimiento_id=trabajo.consentimiento_id,
            encolado_en=trabajo.encolado_en,
            retry=self._reintentos,
        )


class ProductorReconciliacionNulo:
    """No-op: se usa cuando no se configura cola de reconciliación (p. ej. en
    pruebas de `ServicioConsultaFuentes` ajenas a la reconciliación)."""

    def encolar_si_corresponde(self, fuente: str, cliente_id: str, consentimiento_id: str) -> None:
        return None


class ProductorReconciliacion:
    """Dedup + encolar (contrato puntos 1 y 2). Fire-and-forget de punta a
    punta: cualquier fallo (Redis caído, cola caída) se registra en logs y
    nunca se propaga — la respuesta ya resuelta al llamador por
    `ServicioConsultaFuentes` no puede depender de esto."""

    def __init__(self, cola: PuertoColaReconciliacion, dedup: PuertoMarcaDedup) -> None:
        self._cola = cola
        self._dedup = dedup

    def encolar_si_corresponde(self, fuente: str, cliente_id: str, consentimiento_id: str) -> None:
        cliente_opaco = id_opaco(cliente_id)
        try:
            if not self._dedup.marcar_si_ausente(fuente, cliente_id):
                return  # ya se encoló un job para este (fuente, cliente) en esta ventana
            self._cola.encolar(
                TrabajoReconciliacion(
                    fuente=fuente,
                    cliente_id=cliente_id,
                    consentimiento_id=consentimiento_id,
                    encolado_en=time.time(),
                )
            )
            logger.info(
                "reconciliacion encolada fuente=%s cliente=%s", fuente, cliente_opaco
            )
        except Exception as error:
            logger.warning(
                "no se pudo encolar reconciliacion fuente=%s cliente=%s error=%s",
                fuente,
                cliente_opaco,
                type(error).__name__,
            )
