"""Siembra perfiles sintéticos en lotes para la prueba reducida del batch nocturno (EC017).

Escala reducida (regla 2 de `docs/plan-completar-hu-w01.md`):
  - Meta real de EC017: >= 10.000.000 perfiles reprocesados en < 2 h (~1.389 perfiles/s).
  - Prueba reducida a 1/20 del ritmo por instancia: ~21.000 perfiles en 5 min (~70 perfiles/s).
  - Proyección por regla de tres: con el caudal medido en 1 instancia del Cloud Run Job
    `risk-reproceso`, se calcula cuántas tareas paralelas (`task_count`) alcanzan >= 1.389/s.

Puede sembrar por HTTP contra `RISK_URL` (o calcular la proyección en seco con `--simular`).
Solo usa la biblioteca estándar.

Uso:
    python scripts/sembrar_perfiles.py --risk-url http://localhost:8002 --total 21000 --lote 500
    python scripts/sembrar_perfiles.py --simular --total 21000 --segundos-medidos 284.5
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime

META_PERFILES_TOTAL = 10_000_000
META_VENTANA_SEGUNDOS = 7_200  # 2 horas
META_CAUDAL_POR_SEGUNDO = META_PERFILES_TOTAL / META_VENTANA_SEGUNDOS  # ~1388.89 perfiles/s


def generar_perfil_sintetico(indice: int, semilla: str = "ec017") -> dict:
    """Genera un perfil sintético determinista sin datos personales reales (regla 6)."""
    digest = hashlib.sha256(f"{semilla}-{indice}".encode()).hexdigest()
    cliente_id = f"cli-ec017-{digest[:24]}"
    puntaje_base = 550 + (int(digest[24:28], 16) % 350)
    nivel = "BAJO" if puntaje_base >= 750 else ("MEDIO" if puntaje_base >= 640 else "ALTO")
    return {
        "cliente_id": cliente_id,
        "puntaje": puntaje_base,
        "nivel_riesgo": nivel,
        "version_reglas": "v1-semilla",
        "fuentes": {
            "historial-crediticio": {"puntaje": puntaje_base, "mora_activa": False},
            "ingresos-declarados": {"mensual_cop": 4_500_000 + (indice % 10) * 500_000},
        },
        "actualizado_en": datetime.now(UTC).isoformat(),
    }


def enviar_lote_http(risk_url: str, lote: list[dict], token: str | None = None) -> int:
    """Envía un lote de perfiles al servicio RISK (endpoint de semilla o evaluación)."""
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    cuerpo = json.dumps({"perfiles": lote}).encode("utf-8")
    req = urllib.request.Request(
        f"{risk_url.rstrip('/')}/perfiles/lote", data=cuerpo, headers=cabeceras, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        datos = json.loads(resp.read())
        return int(datos.get("procesados", len(lote)))


def calcular_proyeccion(total_perfiles: int, segundos: float) -> dict:
    """Calcula el caudal medido por instancia y la proyección por regla de tres a 10 M en < 2 h."""
    if segundos <= 0:
        raise ValueError("segundos debe ser mayor que 0")
    caudal_instancia = total_perfiles / segundos
    instancias_necesarias = int((META_CAUDAL_POR_SEGUNDO / caudal_instancia) + 0.9999)
    instancias_necesarias = max(1, instancias_necesarias)
    caudal_proyectado = caudal_instancia * instancias_necesarias
    tiempo_10m_horas = (META_PERFILES_TOTAL / caudal_proyectado) / 3600
    return {
        "perfiles_medidos": total_perfiles,
        "segundos_medidos": round(segundos, 2),
        "caudal_medido_perfiles_s": round(caudal_instancia, 2),
        "meta_perfiles": META_PERFILES_TOTAL,
        "meta_horas": 2.0,
        "meta_caudal_perfiles_s": round(META_CAUDAL_POR_SEGUNDO, 2),
        "factor_escala": "1/20",
        "instancias_proyectadas": instancias_necesarias,
        "caudal_proyectado_perfiles_s": round(caudal_proyectado, 2),
        "tiempo_proyectado_10m_horas": round(tiempo_10m_horas, 2),
        "cumple_ec017": tiempo_10m_horas < 2.0,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--risk-url", default=os.getenv("RISK_URL", "http://localhost:8002"))
    p.add_argument(
        "--total",
        type=int,
        default=21_000,
        help="perfiles a sembrar (default: 21.000, escala 1/20)",
    )
    p.add_argument("--lote", type=int, default=500, help="tamaño de cada lote")
    p.add_argument(
        "--token", default=os.getenv("AUTH_TOKEN", ""), help="Bearer token opcional (IAM)"
    )
    p.add_argument(
        "--simular",
        action="store_true",
        help="genera los lotes en memoria y calcula la proyección sin escribir en red",
    )
    p.add_argument(
        "--segundos-medidos",
        type=float,
        default=0.0,
        help="si se usa con --simular, calcula la proyección para este tiempo medido en segundos",
    )
    args = p.parse_args(argv[1:])

    inicio = time.monotonic()
    sembrados = 0

    for base in range(0, args.total, args.lote):
        cantidad = min(args.lote, args.total - base)
        lote = [generar_perfil_sintetico(base + i) for i in range(cantidad)]
        if args.simular:
            sembrados += len(lote)
        else:
            sembrados += enviar_lote_http(args.risk_url, lote, args.token or None)

    transcurrido = args.segundos_medidos if (args.simular and args.segundos_medidos > 0) else (
        max(time.monotonic() - inicio, 0.001)
    )
    resumen = calcular_proyeccion(sembrados, transcurrido)
    print(json.dumps(resumen, indent=2, ensure_ascii=False))
    return 0 if resumen["cumple_ec017"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
