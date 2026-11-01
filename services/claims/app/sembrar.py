"""Semilla de la mesa de siniestros (HU-W04, W04-3) y reinicio de la demo (W04-15).

Uso: `python -m app.sembrar` (tarea `sembrar-siniestros` de docker compose). Solo para local y
pruebas.

Lee los datos de `services/claims/semilla/` (`siniestros.json`, `peritos.json` y `medios/`,
montados en `SEMILLA_DIR`): los 10 casos que tenía el dummy de siniestros, ya retirado (W04-13),
más los que agrega `semilla_aleatoria` para probar la paginación. Los guarda en el modelo real:

- **Pólizas reales:** cada siniestro apunta a la póliza que creó la semilla de POLICY
  (`demo-siniestros-prop-NN`, `python -m app.sembrar_demo`); su id se pide a POLICY.
- **Evidencias en el almacenamiento:** cada imagen se sube como `servicio:claims` (tipos
  `siniestro-foto` y `siniestro-documento`) y se guarda solo su `archivo_id`. Una evidencia que ya
  tiene archivo no se vuelve a subir.
- **Plazos relativos:** `vence_dictamen_horas` y `vence_informe_horas` se convierten en fecha al
  sembrar, para que los plazos del detalle tengan sentido en cualquier día.
- **Derivaciones y resoluciones:** se dejan como en los datos, borrando las que haya creado la demo.

Es idempotente (los ids son fijos). `reiniciar` hace lo mismo sin llamar a POLICY ni al
almacenamiento: conserva la póliza y las evidencias ya guardadas. Las fotos de la semilla no traen
EXIF: la geoetiqueta y las coordenadas las fija la semilla (W04-14).
"""

import json
import logging
import mimetypes
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pymongo import MongoClient

from app.application.autenticacion_servicios import autenticacion_para, modo_desde_entorno
from app.application.cifrado import CifradorAesGcm
from app.application.clientes import ClienteAlmacenamiento, ClientePolicy, reglas_de_reescritura
from app.application.configuracion import Configuracion
from app.application.repositorio import RepositorioSiniestrosMongo
from app.semilla_aleatoria import generar

log = logging.getLogger("sembrar")

TIPO_ARCHIVO = {"FOTO": "siniestro-foto", "DOCUMENTO": "siniestro-documento"}


def _fecha(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def _propuesta_demo(poliza_dummy: str) -> str:
    """`pol-01` (id del dummy) -> `demo-siniestros-prop-01` (propuesta de la semilla de POLICY)."""
    return f"demo-siniestros-prop-{poliza_dummy.split('-')[-1]}"


def sembrar(
    repositorio,
    datos: Path,
    reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    almacenamiento: ClienteAlmacenamiento | None = None,
    policy: ClientePolicy | None = None,
) -> int:
    """Guarda peritos, siniestros, verificaciones, evidencias, derivaciones y resoluciones. Devuelve
    cuántos siniestros quedaron. Sin `almacenamiento` ni `policy` (reinicio) solo usa lo ya
    guardado."""
    ahora = reloj()
    for p in json.loads((datos / "peritos.json").read_text(encoding="utf-8")):
        repositorio.guardar_perito({**p, "activo": True, "creado_en": ahora})

    siniestros = json.loads((datos / "siniestros.json").read_text(encoding="utf-8"))
    siniestros += generar(siniestros, ahora)
    for s in siniestros:
        sid = s["id"]
        existente = repositorio.obtener(sid, primaria=True)
        if existente is not None:
            poliza_id = existente["poliza_id"]
        elif policy is not None:
            poliza_id = policy.poliza_de_propuesta(_propuesta_demo(s["poliza_id"]))
        else:
            log.warning("siniestro %s sin sembrar: hace falta POLICY para su póliza", sid)
            continue
        repositorio.guardar_siniestro(
            {
                **s,
                "poliza_id": poliza_id,
                "ocurrido_en": _fecha(s["ocurrido_en"]),
                "notificado_en": _fecha(s["notificado_en"]),
                "vence_dictamen_en": ahora + timedelta(hours=s["vence_dictamen_horas"]),
            },
            ahora,
        )
        for orden, v in enumerate(s["verificaciones"], start=1):
            repositorio.guardar_verificacion(
                {
                    "id": f"{sid}-ver-{orden}",
                    "siniestro_id": sid,
                    "orden": orden,
                    "descripcion": v["descripcion"],
                    "cumple": v["cumple"],
                    "creado_en": ahora,
                }
            )
        for e in s["evidencias"]:
            _sembrar_evidencia(repositorio, datos, s, e, ahora, almacenamiento)
        _sembrar_resolucion(repositorio, s, ahora)
    return len(siniestros)


def _sembrar_evidencia(repositorio, datos: Path, s: dict, e: dict, ahora, almacenamiento) -> None:
    existente = repositorio.evidencia(e["id"])
    if existente is not None:
        archivo_id = existente["archivo_id"]
    elif almacenamiento is not None:
        ruta = datos / "medios" / e["archivo"]
        tipo_contenido = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        archivo_id = almacenamiento.subir(
            TIPO_ARCHIVO[e["tipo"]], tipo_contenido, ruta.read_bytes(), s["id"]
        )
    else:
        log.warning("evidencia %s sin sembrar: hace falta el almacenamiento", e["id"])
        return
    geo = e["geoetiquetada"]
    repositorio.guardar_evidencia(
        {
            "id": e["id"],
            "siniestro_id": s["id"],
            "archivo_id": archivo_id,
            "tipo": e["tipo"],
            "titulo": e["titulo"],
            "nombre_archivo": e["archivo"],
            "registrada_en": _fecha(e["registrada_en"]),
            "geoetiquetada": geo,
            "latitud": float(s["ubicacion"]["latitud"]) if geo else None,
            "longitud": float(s["ubicacion"]["longitud"]) if geo else None,
            "nota": e.get("nota"),
            "creado_en": ahora,
        }
    )


def _sembrar_resolucion(repositorio, s: dict, ahora: datetime) -> None:
    r = s.get("resolucion")
    sid = s["id"]
    asignacion = resolucion = None
    if r and r.get("perito_id"):
        asignacion = {
            "id": f"{sid}-asignacion",
            "siniestro_id": sid,
            "perito_id": r["perito_id"],
            "instrucciones": r.get("instrucciones"),
            "notificacion": r["notificacion"],
            "asignada_en": _fecha(r["resuelta_en"]),
            "vence_informe_en": ahora + timedelta(hours=r["vence_informe_horas"]),
        }
    if r and r["tipo"] in ("APROBACION", "RECHAZO"):
        resolucion = {
            "id": f"{sid}-resolucion",
            "siniestro_id": sid,
            "tipo": r["tipo"],
            "motivo": r.get("motivo"),
            "comentario": r.get("comentario"),
            "notificacion": r["notificacion"],
            "via_recurso": r.get("via_recurso"),
            "indemnizacion": r.get("indemnizacion"),
            "metodo_transferencia": r.get("metodo_transferencia"),
            "plazo_dispersion": r.get("plazo_dispersion"),
            "resuelta_en": _fecha(r["resuelta_en"]),
        }
    repositorio.reiniciar_resolucion(sid, asignacion, resolucion)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    # Las URLs firmadas del almacenamiento no deben quedar en los logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = Configuracion.desde_entorno()
    datos = Path(os.environ.get("SEMILLA_DIR", "/semilla"))
    if not (datos / "siniestros.json").is_file():
        log.error("No hay datos de semilla en %s", datos)
        return 1
    modo = modo_desde_entorno()
    cliente_mongo = MongoClient(config.mongo_uri, tz_aware=True)
    almacenamiento = ClienteAlmacenamiento(
        config.almacenamiento_url,
        config.servicios_timeout_segundos,
        autenticacion_para(modo, "almacenamiento"),
        reglas_de_reescritura(config.almacen_reescritura_origenes),
    )
    policy = ClientePolicy(
        config.policy_url, config.servicios_timeout_segundos, autenticacion_para(modo, "policy")
    )
    try:
        cifrador = CifradorAesGcm.desde_entorno(
            cifrado_clave=config.cifrado_clave,
            kms_key_name=config.kms_key_name,
            cifrado_clave_envuelta=config.cifrado_clave_envuelta,
        )
        repositorio = RepositorioSiniestrosMongo(cliente_mongo, cifrador, config.mongo_db)
        total = sembrar(repositorio, datos, almacenamiento=almacenamiento, policy=policy)
    finally:
        almacenamiento.cerrar()
        policy.cerrar()
        cliente_mongo.close()
    log.info("Siniestros de demostración listos: %d", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
