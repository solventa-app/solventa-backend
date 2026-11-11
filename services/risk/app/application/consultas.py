"""Historial de consultas de fuentes (QA 2026-11-10, Q-08, decisión D-2).

Cada cotización consulta las fuentes de un titular. RISK, que ya escribe el riesgo y guarda el
consentimiento, deja un renglón por solicitud para que el asesor vea lo que consultó: cuándo, a
quién (documento enmascarado), qué tipo de oferta salió y el **estado** de cada fuente.

- **Nunca los datos de las fuentes** (regla 6): solo `id`, `nombre`, `origen`, `estado` y, si el
  dato vino de caché, cuándo se capturó. Por eso la consulta sobrevive a la revocación o al
  vencimiento del consentimiento, cuando el perfil sí se borra: queda como rastro de lo consultado.
- **El filtro es el actor:** el BFF manda el id del asesor (sale de su token) y RISK solo devuelve
  lo suyo. Una consulta ajena responde igual que una inexistente.
- **Escritor único (regla 1):** solo RISK escribe la colección `consultas`.

Capa simple, sin puertos y adaptadores (regla 3).
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient

from app.application.identificadores import id_opaco
from app.application.repositorio_perfiles import NOMBRE_BASE

logger = logging.getLogger("risk.consultas")

COLECCION = "consultas"
TAMANO_MAXIMO = 50


def _fuente(fuente: dict[str, Any]) -> dict[str, Any]:
    """Solo lo que el contrato (`FuenteConsultada`) expone; cualquier otro campo se descarta."""
    return {
        "id": fuente["id"],
        "nombre": fuente["nombre"],
        "origen": fuente["origen"],
        "estado": fuente["estado"],
        "capturado_en": fuente.get("capturado_en"),
    }


def _salida(documento: dict[str, Any]) -> dict[str, Any]:
    creada_en = documento["creada_en"]
    return {
        "id": documento["_id"],
        "creada_en": creada_en.isoformat() if isinstance(creada_en, datetime) else creada_en,
        "documento_enmascarado": documento["documento_enmascarado"],
        "tipo_oferta": documento["tipo_oferta"],
        "producto": documento["producto"],
        "oferta_id": documento.get("oferta_id"),
        "fuentes": documento["fuentes"],
    }


class RepositorioConsultasMongo:
    def __init__(self, cliente: MongoClient) -> None:
        self._coleccion = cliente[NOMBRE_BASE][COLECCION]

    def guardar(self, documento: dict[str, Any]) -> None:
        self._coleccion.insert_one(documento)

    def listar(self, actor_id: str, pagina: int, tamano: int) -> tuple[list[dict[str, Any]], int]:
        filtro = {"actor_id": actor_id}
        total = self._coleccion.count_documents(filtro)
        cursor = (
            self._coleccion.find(filtro)
            .sort([("creada_en", -1), ("_id", -1)])
            .skip((pagina - 1) * tamano)
            .limit(tamano)
        )
        return list(cursor), total

    def obtener(self, consulta_id: str, actor_id: str) -> dict[str, Any] | None:
        return self._coleccion.find_one({"_id": consulta_id, "actor_id": actor_id})


class ServicioConsultas:
    def __init__(self, repositorio: RepositorioConsultasMongo) -> None:
        self._repositorio = repositorio

    def registrar(
        self,
        actor_id: str,
        actor_rol: str,
        titular_id: str,
        documento_enmascarado: str,
        consentimiento_id: str,
        tipo_oferta: str,
        producto: str,
        fuentes: list[dict[str, Any]],
        oferta_id: str | None = None,
        creada_en: datetime | None = None,
    ) -> str:
        consulta_id = uuid.uuid4().hex
        self._repositorio.guardar(
            {
                "_id": consulta_id,
                "actor_id": actor_id,
                "actor_rol": actor_rol,
                "titular_id": titular_id,
                "documento_enmascarado": documento_enmascarado,
                "consentimiento_id": consentimiento_id,
                "oferta_id": oferta_id,
                "tipo_oferta": tipo_oferta,
                "producto": producto,
                "fuentes": [_fuente(f) for f in fuentes],
                "creada_en": creada_en or datetime.now(UTC),
            }
        )
        logger.info(
            "consulta registrada actor=%s consulta=%s fuentes=%d",
            id_opaco(actor_id),
            consulta_id,
            len(fuentes),
        )
        return consulta_id

    def listar(self, actor_id: str, pagina: int, tamano: int) -> dict[str, Any]:
        pagina = max(1, pagina)
        tamano = min(max(1, tamano), TAMANO_MAXIMO)
        documentos, total = self._repositorio.listar(actor_id, pagina, tamano)
        return {
            "consultas": [_salida(d) for d in documentos],
            "total": total,
            "pagina": pagina,
            "tamano": tamano,
        }

    def obtener(self, consulta_id: str, actor_id: str) -> dict[str, Any] | None:
        documento = self._repositorio.obtener(consulta_id, actor_id)
        return _salida(documento) if documento else None
