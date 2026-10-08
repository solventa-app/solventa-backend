"""Persistencia del perfil de riesgo en MongoDB (T-W01-6/7, HU-W01).

Capa simple (sin puertos y adaptadores formales — regla de arquitectura #3:
hexagonal es solo para `acl-worker`). RISK es el **único escritor** del
perfil de riesgo (regla de arquitectura #1): este módulo es el único lugar
del sistema que escribe en la colección `perfiles`.

Escribe con el *write concern* por defecto que ya fija `mongo-init`
(`{w: 1}`, deliberadamente NO `majority` — ver `dev/mongo/init-replica-set.js`
y `docs/arquitectura-backend.md`: forzar `majority` aquí ocultaría el lag de
replicación que el experimento D-02 necesita medir). Usa una `ClientSession`
explícita para poder leer `session.operation_time` después de escribir — es
el valor que D-02/HA-LAT-003 exige devolver para que RATING arme su sesión
causal (`afterClusterTime`).

Versión incremental por cliente: se asigna de forma ATÓMICA con
`find_one_and_update($inc)` sobre una colección de contadores (`_id` =
`cliente_id`), en la MISMA sesión que el `insert_one` del documento de
perfil — así ambas operaciones comparten un único `operationTime` final (el
de la escritura más reciente de la sesión, el `insert_one`), y dos
escrituras concurrentes para el mismo cliente nunca chocan de versión (a
diferencia de leer el máximo existente y sumar 1 sin atomicidad).

Cifrado de campo (SEG-002/D-04/CA-W01-10) está **fuera de alcance de esta
tarea**: no hay Cloud KMS disponible (sin proyecto GCP, ver `CLAUDE.md`) y no
existe todavía ningún helper de cifrado en el repo (AUTH tampoco lo ha
construido). Los datos de las fuentes (`fuentes`) se persisten tal cual se
reciben, sin cifrar — hueco real documentado en `README.md`, deliberadamente
NO simulado con un cifrado de juguete (daría falsa confianza)."""

from datetime import UTC, datetime
from typing import Any

from bson import Timestamp
from pymongo import MongoClient, ReturnDocument

NOMBRE_BASE = "risk"
COLECCION_PERFILES = "perfiles"
COLECCION_CONTADORES = "contadores_version"


class RepositorioPerfilesMongo:
    def __init__(self, cliente: MongoClient) -> None:
        self._cliente = cliente
        base = cliente[NOMBRE_BASE]
        self._perfiles = base[COLECCION_PERFILES]
        self._contadores = base[COLECCION_CONTADORES]

    def guardar(
        self, cliente_id: str, fuentes: list[dict[str, Any]]
    ) -> tuple[int, Timestamp]:
        with self._cliente.start_session() as session:
            contador = self._contadores.find_one_and_update(
                {"_id": cliente_id},
                {"$inc": {"secuencia": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            perfil_version = contador["secuencia"]

            documento = {
                "cliente_id": cliente_id,
                "perfil_version": perfil_version,
                "fuentes": fuentes,
                "creado_en": datetime.now(UTC),
            }
            self._perfiles.insert_one(documento, session=session)

            operation_time = session.operation_time

        return perfil_version, operation_time
