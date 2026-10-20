"""Pruebas de cifrado de campo AES-256-GCM, sobre de KMS y migración idempotente
en RISK (Bloque 3, HA-SEG-002, D-04, EC022)."""

import base64
import json
import os
from contextlib import contextmanager
from typing import Any

import pytest
from fakes import TimestampFalso
from migrations.versiones import v0002_cifrar_datos_fuentes

from app.application.cifrado import (
    CIFRADO_CLAVE_LOCAL,
    CifradorAesGcm,
    ErrorDeCifrado,
)
from app.application.repositorio_perfiles import (
    COLECCION_CONTADORES,
    COLECCION_PERFILES,
    NOMBRE_BASE,
    RepositorioPerfilesMongo,
)


class SesionMongoFalsa:
    def __init__(self) -> None:
        self.operation_time = TimestampFalso(1_700_000_001, 1)


class ColeccionMongoFalsa:
    def __init__(self) -> None:
        self.documentos: list[dict[str, Any]] = []

    def find_one_and_update(
        self,
        filtro: dict[str, Any],
        actualizacion: dict[str, Any],
        upsert: bool = False,
        return_document: Any = None,
        session: Any = None,
    ) -> dict[str, Any]:
        for doc in self.documentos:
            if doc.get("_id") == filtro.get("_id"):
                doc["secuencia"] = doc.get("secuencia", 0) + actualizacion["$inc"]["secuencia"]
                return doc
        nuevo = {"_id": filtro["_id"], "secuencia": actualizacion["$inc"]["secuencia"]}
        self.documentos.append(nuevo)
        return nuevo

    def insert_one(self, documento: dict[str, Any], session: Any = None) -> None:
        copia = dict(documento)
        copia.setdefault("_id", f"doc-{len(self.documentos) + 1}")
        self.documentos.append(copia)

    def find(self, filtro: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self.documentos)

    def update_one(self, filtro: dict[str, Any], actualizacion: dict[str, Any]) -> None:
        for doc in self.documentos:
            if doc.get("_id") == filtro.get("_id"):
                doc.update(actualizacion.get("$set", {}))


class BaseMongoFalsa:
    def __init__(self) -> None:
        self.colecciones: dict[str, ColeccionMongoFalsa] = {
            COLECCION_PERFILES: ColeccionMongoFalsa(),
            COLECCION_CONTADORES: ColeccionMongoFalsa(),
        }

    def __getitem__(self, nombre: str) -> ColeccionMongoFalsa:
        return self.colecciones.setdefault(nombre, ColeccionMongoFalsa())


class ClienteMongoFalso:
    def __init__(self) -> None:
        self.bases: dict[str, BaseMongoFalsa] = {NOMBRE_BASE: BaseMongoFalsa()}

    def __getitem__(self, nombre: str) -> BaseMongoFalsa:
        return self.bases.setdefault(nombre, BaseMongoFalsa())

    @contextmanager
    def start_session(self):
        yield SesionMongoFalsa()


FUENTES_SENSIBLES = [
    {
        "fuente": "historial-crediticio",
        "datos": {"score_crediticio": 780, "obligaciones_vigentes": 2},
        "capturado_en": "2026-10-18T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
    {
        "fuente": "cuentas-bancarias",
        "datos": {"saldo_promedio_3m": 8_500_000, "productos_activos": 3},
        "capturado_en": "2026-10-18T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
]


def test_ec022_documento_crudo_en_mongo_tiene_datos_vacios_y_datos_cifrados() -> None:
    """EC022: en MongoDB Atlas M0 nunca queda un solo campo de `datos` en claro;
    el documento crudo tiene `datos == {}` y `datos_cifrados` ilegible sin la llave."""
    cliente_mongo = ClienteMongoFalso()
    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    repositorio = RepositorioPerfilesMongo(cliente_mongo, cifrador=cifrador)

    version, _ = repositorio.guardar("cliente-ec022", FUENTES_SENSIBLES)

    assert version == 1
    guardados = cliente_mongo[NOMBRE_BASE][COLECCION_PERFILES].documentos
    assert len(guardados) == 1
    doc_crudo = guardados[0]

    # El perfil en MongoDB también guarda score_total, nivel_riesgo, fecha_vigencia y version_reglas
    assert isinstance(doc_crudo["score_total"], int)
    assert doc_crudo["nivel_riesgo"] in ("BAJO", "MEDIO", "ALTO")
    assert doc_crudo["fecha_vigencia"]
    assert doc_crudo["version_reglas"] == "v1"

    for fuente_cruda in doc_crudo["fuentes"]:
        assert fuente_cruda["datos"] == {}
        texto_cifrado = fuente_cruda["datos_cifrados"]
        assert isinstance(texto_cifrado, str) and len(texto_cifrado) > 20
        assert "780" not in texto_cifrado
        assert "8500000" not in texto_cifrado
        assert "score_crediticio" not in texto_cifrado

    # Con la llave correcta sí se recupera el JSON original
    descifrado_0 = json.loads(cifrador.descifrar(doc_crudo["fuentes"][0]["datos_cifrados"]))
    assert descifrado_0 == {"obligaciones_vigentes": 2, "score_crediticio": 780}


def test_ec022_sin_la_llave_correcta_no_se_puede_descifrar() -> None:
    cifrador_escritor = CifradorAesGcm(os.urandom(32))
    cifrador_intruso = CifradorAesGcm(os.urandom(32))

    texto_cifrado = cifrador_escritor.cifrar(
        json.dumps({"score_crediticio": 712, "cuenta": "999-000"})
    )

    with pytest.raises(ErrorDeCifrado):
        cifrador_intruso.descifrar(texto_cifrado)


def test_ec022_sobre_kms_desenvuelve_llave_y_falla_si_kms_esta_deshabilitado() -> None:
    """EC022: cuando la versión de la llave en Cloud KMS se deshabilita, el
    servicio no puede desenvolver `CIFRADO_CLAVE_ENVUELTA` ni descifrar datos."""
    dek_real = os.urandom(32)
    sobre_falso_b64 = base64.urlsafe_b64encode(b"sobre-kms-envuelto").decode("ascii")
    kms_habilitado = True

    class ClienteKmsFalso:
        def decrypt(self, request: dict[str, Any]) -> dict[str, bytes]:
            if not kms_habilitado:
                raise RuntimeError("KMS CryptoKeyVersion DISABLED")
            assert (
                request["name"]
                == "projects/solventa/locations/us-central1/keyRings/kr/cryptoKeys/campos"
            )
            return {"plaintext": dek_real}

    cifrador = CifradorAesGcm.desde_entorno(
        kms_key_name="projects/solventa/locations/us-central1/keyRings/kr/cryptoKeys/campos",
        cifrado_clave_envuelta=sobre_falso_b64,
        cliente_kms=ClienteKmsFalso(),
    )
    cifrado = cifrador.cifrar('{"score_crediticio": 810}')
    assert json.loads(cifrador.descifrar(cifrado)) == {"score_crediticio": 810}

    # Al deshabilitar la versión de la llave en KMS, no puede arrancar/desenvolver
    kms_habilitado = False
    with pytest.raises(ErrorDeCifrado):
        CifradorAesGcm.desde_entorno(
            kms_key_name="projects/solventa/locations/us-central1/keyRings/kr/cryptoKeys/campos",
            cifrado_clave_envuelta=sobre_falso_b64,
            cliente_kms=ClienteKmsFalso(),
        )


def test_migracion_v0002_cifrar_datos_fuentes_es_idempotente() -> None:
    db = BaseMongoFalsa()
    db[COLECCION_PERFILES].insert_one(
        {
            "_id": "perfil-viejo-1",
            "cliente_id": "cliente-1",
            "perfil_version": 1,
            "fuentes": [
                {
                    "fuente": "historial-crediticio",
                    "datos": {"score_crediticio": 690},
                    "capturado_en": "2026-10-01T00:00:00+00:00",
                    "de_cache": False,
                    "degradado": False,
                },
                {
                    "fuente": "afiliacion-pila",
                    "datos": {},
                    "capturado_en": "2026-10-01T00:00:00+00:00",
                    "de_cache": False,
                    "degradado": True,
                },
            ],
        }
    )

    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)

    # Primera ejecución: cifra el documento que tenía datos en claro
    migrados_primera = v0002_cifrar_datos_fuentes.aplicar(db, cifrador=cifrador)
    assert migrados_primera == 1

    doc = db[COLECCION_PERFILES].documentos[0]
    assert doc["fuentes"][0]["datos"] == {}
    cifrado_primera_vez = doc["fuentes"][0]["datos_cifrados"]
    assert json.loads(cifrador.descifrar(cifrado_primera_vez)) == {"score_crediticio": 690}
    # La fuente que tenía datos={} (degradada) sigue vacía sin datos_cifrados
    assert doc["fuentes"][1]["datos"] == {}
    assert "datos_cifrados" not in doc["fuentes"][1]

    # Segunda ejecución: idempotente, no toca nada ni recifra
    migrados_segunda = v0002_cifrar_datos_fuentes.aplicar(db, cifrador=cifrador)
    assert migrados_segunda == 0
    fuente_actual = db[COLECCION_PERFILES].documentos[0]["fuentes"][0]
    assert fuente_actual["datos_cifrados"] == cifrado_primera_vez
