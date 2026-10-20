"""Siembra perfiles sintéticos por la API REAL de RISK para la prueba reducida del batch (EC017).

Meta de EC017: >= 10.000.000 de perfiles reprocesados en < 2 h (~1.389 perfiles/s). La prueba es
reducida (regla 2 de `docs/plan-completar-hu-w01.md`): se siembran y reprocesan unos miles de
perfiles y el caudal que se MIDE en una tarea se proyecta con una regla de tres (ver
`docs/evidencias/EC017-*.md`).

Este script solo siembra: llama a `POST /perfiles` de RISK (el escritor único) con perfiles
sintéticos y deterministas (sin datos personales) e imprime lo que midió. No calcula ni estima nada
del batch. Para que el batch tenga trabajo, los perfiles sembrados se "envejecen" (versión de reglas
vieja) y luego se corre `python -m app.batch_reproceso`:

    python scripts/sembrar_perfiles.py --risk-url http://localhost:8002 --total 5000 --hilos 8
    docker compose exec -T mongo1 mongosh --quiet --eval 'db.getSiblingDB("risk").perfiles.\
      updateMany({cliente_id: /^cli-ec017-/}, {$set: {version_reglas: "v0-semilla"}})'
    docker compose run --rm risk python -m app.batch_reproceso

Solo biblioteca estándar. Solo para local o staging de pruebas."""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

META_PERFILES_TOTAL = 10_000_000
META_VENTANA_SEGUNDOS = 7_200  # 2 horas
META_CAUDAL_POR_SEGUNDO = META_PERFILES_TOTAL / META_VENTANA_SEGUNDOS  # ~1388,89 perfiles/s


def perfil_sintetico(indice: int, semilla: str = "ec017") -> dict:
    """Perfil determinista, sin datos personales (regla #6): solo un id opaco y datos inventados."""
    digest = hashlib.sha256(f"{semilla}-{indice}".encode()).hexdigest()
    puntaje = 550 + (int(digest[24:28], 16) % 350)
    ahora = datetime.now(UTC).isoformat()
    return {
        "cliente_id": f"cli-{semilla}-{digest[:24]}",
        "fuentes": [
            {
                "fuente": "historial-crediticio",
                "datos": {"score_crediticio": puntaje, "obligaciones_vigentes": indice % 5},
                "capturado_en": ahora,
            },
            {
                "fuente": "cuentas-bancarias",
                "datos": {"saldo_promedio_3m": 4_000_000 + (indice % 10) * 250_000},
                "capturado_en": ahora,
            },
        ],
    }


def enviar(risk_url: str, perfil: dict, token: str | None) -> bool:
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(
        f"{risk_url.rstrip('/')}/perfiles",
        data=json.dumps(perfil).encode("utf-8"),
        headers=cabeceras,
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            return respuesta.status == 200
    except Exception:
        return False


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Siembra perfiles sintéticos en RISK (EC017).")
    parser.add_argument("--risk-url", default=os.getenv("RISK_URL", "http://localhost:8002"))
    parser.add_argument("--total", type=int, default=5000, help="cuántos perfiles sembrar")
    parser.add_argument("--hilos", type=int, default=8, help="peticiones en paralelo")
    parser.add_argument("--desde", type=int, default=0, help="índice del primer perfil")
    parser.add_argument("--semilla", default="ec017")
    opciones = parser.parse_args(argumentos)
    token = os.getenv("RISK_TOKEN")

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=opciones.hilos) as pool:
        resultados = list(
            pool.map(
                lambda i: enviar(opciones.risk_url, perfil_sintetico(i, opciones.semilla), token),
                range(opciones.desde, opciones.desde + opciones.total),
            )
        )
    segundos = time.perf_counter() - inicio
    sembrados = sum(resultados)
    print(
        json.dumps(
            {
                "solicitados": opciones.total,
                "sembrados": sembrados,
                "fallidos": opciones.total - sembrados,
                "segundos": round(segundos, 2),
                "perfiles_por_segundo": round(sembrados / segundos, 2) if segundos else None,
                "meta_perfiles_por_segundo_ec017": round(META_CAUDAL_POR_SEGUNDO, 2),
            },
            indent=2,
        )
    )
    return 0 if sembrados == opciones.total else 1


if __name__ == "__main__":
    sys.exit(main())
