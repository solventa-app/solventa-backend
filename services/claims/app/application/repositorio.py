"""Repositorio de CLAIMS sobre MongoDB (W04-2): colecciones referenciadas, escritor único.

- Escribe siempre en la primaria. La bandeja y el detalle se leen de la **secundaria**
  (`secondaryPreferred` + `maxTimeMS`, HA-LAT-003), como `lector_perfiles.py` de RATING. Tras una
  mutación se lee de la primaria (`primaria=True`): el operador ve lo que acaba de escribir aunque
  la réplica vaya atrasada.
- Los datos personales (nombre y documento del asegurado, ubicación, descripción, comentario e
  instrucciones) se guardan cifrados por campo con el id del siniestro como dueño (regla #6). Hacia
  afuera salen en claro, solo por la red interna hacia el BFF.
- Las lecturas de varios siniestros traen sus colecciones relacionadas en una consulta por colección
  (`$in`), sin una consulta por caso.
"""

import json
from datetime import datetime
from typing import Any

from pymongo import MongoClient
from pymongo.read_preferences import SecondaryPreferred

from app.application.cifrado import CifradorCampos

SINIESTROS = "siniestros"
PERITOS = "peritos"
EVIDENCIAS = "evidencias_siniestro"
VERIFICACIONES = "verificaciones_siniestro"
ASIGNACIONES = "asignaciones_perito"
RESOLUCIONES = "resoluciones_siniestro"


class RepositorioSiniestrosMongo:
    def __init__(
        self,
        cliente: MongoClient,
        cifrador: CifradorCampos,
        nombre_db: str = "claims",
        lectura_max_ms: int = 500,
    ) -> None:
        self._db = cliente[nombre_db]
        self._cliente = cliente
        self._cifrador = cifrador
        self._lectura_max_ms = lectura_max_ms

    # ---------- Utilidades ----------

    def _coleccion(self, nombre: str, primaria: bool):
        coleccion = self._db[nombre]
        return (
            coleccion if primaria else coleccion.with_options(read_preference=SecondaryPreferred())
        )

    def _buscar(self, nombre: str, filtro: dict, primaria: bool, orden=None) -> list[dict]:
        cursor = self._coleccion(nombre, primaria).find(filtro).max_time_ms(self._lectura_max_ms)
        if orden:
            cursor = cursor.sort(orden)
        return list(cursor)

    def _cifrar(self, texto: str | None, campo: str, siniestro_id: str) -> str | None:
        if texto is None:
            return None
        return self._cifrador.cifrar(texto, f"{campo}:{siniestro_id}")

    def _descifrar(self, cifrado: str | None, campo: str, siniestro_id: str) -> str | None:
        if cifrado is None:
            return None
        return self._cifrador.descifrar(cifrado, f"{campo}:{siniestro_id}")

    def _a_siniestro(self, doc: dict) -> dict:
        sid = doc["_id"]
        return {
            "id": sid,
            "radicado": doc["radicado"],
            "poliza_id": doc["poliza_id"],
            "estado": doc["estado"],
            "asegurado": {
                "nombre": self._descifrar(doc["asegurado_nombre_cifrado"], "asegurado_nombre", sid),
                "documento": self._descifrar(
                    doc["asegurado_documento_cifrado"], "asegurado_documento", sid
                ),
            },
            "cobertura_aplicada": doc["cobertura_aplicada"],
            "entidad_acreedora": doc["entidad_acreedora"],
            "ocurrido_en": doc["ocurrido_en"],
            "notificado_en": doc["notificado_en"],
            "ubicacion": json.loads(self._descifrar(doc["ubicacion_cifrada"], "ubicacion", sid)),
            "descripcion": self._descifrar(doc["descripcion_cifrada"], "descripcion", sid),
            "suma_reclamada": doc["suma_reclamada"],
            "vence_dictamen_en": doc["vence_dictamen_en"],
        }

    @staticmethod
    def _sin_id(doc: dict) -> dict:
        return {("id" if k == "_id" else k): v for k, v in doc.items()}

    # ---------- Lectura ----------

    def listar(self, estado: str | None = None) -> list[dict]:
        """Del más reciente al más antiguo, como la bandeja del dummy."""
        filtro = {"estado": estado} if estado else {}
        docs = self._buscar(SINIESTROS, filtro, primaria=False, orden=[("notificado_en", -1)])
        return [self._a_siniestro(d) for d in docs]

    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None:
        docs = self._buscar(SINIESTROS, {"_id": siniestro_id}, primaria)
        return self._a_siniestro(docs[0]) if docs else None

    def evidencias(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]:
        por_siniestro: dict[str, list[dict]] = {i: [] for i in ids}
        for doc in self._buscar(
            EVIDENCIAS, {"siniestro_id": {"$in": ids}}, primaria, orden=[("registrada_en", 1)]
        ):
            por_siniestro[doc["siniestro_id"]].append(self._sin_id(doc))
        return por_siniestro

    def verificaciones(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]:
        por_siniestro: dict[str, list[dict]] = {i: [] for i in ids}
        for doc in self._buscar(
            VERIFICACIONES, {"siniestro_id": {"$in": ids}}, primaria, orden=[("orden", 1)]
        ):
            por_siniestro[doc["siniestro_id"]].append(self._sin_id(doc))
        return por_siniestro

    def asignaciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]:
        resultado = {}
        for doc in self._buscar(ASIGNACIONES, {"siniestro_id": {"$in": ids}}, primaria):
            sid = doc["siniestro_id"]
            asignacion = self._sin_id(doc)
            asignacion["instrucciones"] = self._descifrar(
                asignacion.pop("instrucciones_cifradas", None), "instrucciones", sid
            )
            resultado[sid] = asignacion
        return resultado

    def resoluciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]:
        resultado = {}
        for doc in self._buscar(RESOLUCIONES, {"siniestro_id": {"$in": ids}}, primaria):
            sid = doc["siniestro_id"]
            resolucion = self._sin_id(doc)
            resolucion["comentario"] = self._descifrar(
                resolucion.pop("comentario_cifrado", None), "comentario", sid
            )
            resultado[sid] = resolucion
        return resultado

    def peritos(self, primaria: bool = False) -> list[dict]:
        docs = self._buscar(PERITOS, {"activo": True}, primaria, orden=[("nombre", 1)])
        return [self._sin_id(d) for d in docs]

    def perito(self, perito_id: str, primaria: bool = False) -> dict | None:
        docs = self._buscar(PERITOS, {"_id": perito_id}, primaria)
        return self._sin_id(docs[0]) if docs else None

    # ---------- Semilla de demostración (W04-3, W04-15) ----------
    # Idempotente por `_id`. Solo la usan `app/sembrar.py` y `/_demo/reiniciar`: la API normal no
    # reemplaza ni borra asignaciones ni resoluciones (son append-only).

    def guardar_perito(self, perito: dict) -> None:
        doc = {**{k: v for k, v in perito.items() if k != "id"}, "_id": perito["id"]}
        self._db[PERITOS].replace_one({"_id": perito["id"]}, doc, upsert=True)

    def guardar_siniestro(self, siniestro: dict, ahora: datetime) -> None:
        sid = siniestro["id"]
        doc = {
            "_id": sid,
            "radicado": siniestro["radicado"],
            "poliza_id": siniestro["poliza_id"],
            "estado": siniestro["estado"],
            "asegurado_nombre_cifrado": self._cifrar(
                siniestro["asegurado"]["nombre"], "asegurado_nombre", sid
            ),
            "asegurado_documento_cifrado": self._cifrar(
                siniestro["asegurado"]["documento"], "asegurado_documento", sid
            ),
            "cobertura_aplicada": siniestro["cobertura_aplicada"],
            "entidad_acreedora": siniestro["entidad_acreedora"],
            "ocurrido_en": siniestro["ocurrido_en"],
            "notificado_en": siniestro["notificado_en"],
            "ubicacion_cifrada": self._cifrar(
                json.dumps(siniestro["ubicacion"], ensure_ascii=False), "ubicacion", sid
            ),
            "descripcion_cifrada": self._cifrar(siniestro["descripcion"], "descripcion", sid),
            "suma_reclamada": siniestro["suma_reclamada"],
            "vence_dictamen_en": siniestro["vence_dictamen_en"],
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        self._db[SINIESTROS].replace_one({"_id": sid}, doc, upsert=True)

    def evidencia(self, evidencia_id: str) -> dict | None:
        doc = self._db[EVIDENCIAS].find_one({"_id": evidencia_id})
        return self._sin_id(doc) if doc else None

    def guardar_evidencia(self, evidencia: dict) -> None:
        doc = {**{k: v for k, v in evidencia.items() if k != "id"}, "_id": evidencia["id"]}
        self._db[EVIDENCIAS].replace_one({"_id": evidencia["id"]}, doc, upsert=True)

    def guardar_verificacion(self, verificacion: dict) -> None:
        doc = {**{k: v for k, v in verificacion.items() if k != "id"}, "_id": verificacion["id"]}
        self._db[VERIFICACIONES].replace_one({"_id": verificacion["id"]}, doc, upsert=True)

    def reiniciar_resolucion(
        self, siniestro_id: str, asignacion: dict | None, resolucion: dict | None
    ) -> None:
        """Deja la derivación y la resolución del caso como en la semilla (borra las de la demo)."""
        self._db[ASIGNACIONES].delete_many({"siniestro_id": siniestro_id})
        self._db[RESOLUCIONES].delete_many({"siniestro_id": siniestro_id})
        if asignacion is not None:
            self._db[ASIGNACIONES].insert_one(self._doc_asignacion(asignacion))
        if resolucion is not None:
            self._db[RESOLUCIONES].insert_one(self._doc_resolucion(resolucion))

    # ---------- Documentos de asignación y resolución ----------

    def _doc_asignacion(self, asignacion: dict) -> dict[str, Any]:
        sid = asignacion["siniestro_id"]
        return {
            "_id": asignacion["id"],
            "siniestro_id": sid,
            "perito_id": asignacion["perito_id"],
            "instrucciones_cifradas": self._cifrar(
                asignacion.get("instrucciones"), "instrucciones", sid
            ),
            "notificacion": asignacion["notificacion"],
            "asignada_en": asignacion["asignada_en"],
            "vence_informe_en": asignacion["vence_informe_en"],
            "actor_usuario_id": asignacion.get("actor_usuario_id"),
        }

    def _doc_resolucion(self, resolucion: dict) -> dict[str, Any]:
        sid = resolucion["siniestro_id"]
        return {
            "_id": resolucion["id"],
            "siniestro_id": sid,
            "tipo": resolucion["tipo"],
            "motivo": resolucion.get("motivo"),
            "comentario_cifrado": self._cifrar(resolucion.get("comentario"), "comentario", sid),
            "notificacion": resolucion["notificacion"],
            "via_recurso": resolucion.get("via_recurso"),
            "indemnizacion": resolucion.get("indemnizacion"),
            "metodo_transferencia": resolucion.get("metodo_transferencia"),
            "plazo_dispersion": resolucion.get("plazo_dispersion"),
            "orden_pago_id": resolucion.get("orden_pago_id"),
            "resuelta_en": resolucion["resuelta_en"],
            "actor_usuario_id": resolucion.get("actor_usuario_id"),
        }
