"""Valida que `infra/gateway/openapi.yaml` (nube) e `infra/gateway/envoy.yaml` (local) coincidan.

Comprueba que ambos archivos declaren las mismas rutas HTTP, el mismo método por ruta y el mismo
corte duro de 700 ms (`deadline: 0.7` en OpenAPI 2.0 y `timeout: 0.7s` en Envoy), y que Envoy
escuche en el puerto 8080 hacia `bff:8000`.

La API de los socios (HU-W07) va en otro gateway de la nube (`openapi-socios.yaml`) y en el mismo
Envoy local: sus rutas (`/v1/...` fuera de `/v1/graphql`) deben ser las mismas en el contrato
(`contracts/openapi/socios-v1.json`), en `openapi-socios.yaml` y en Envoy, con el mismo corte.

Solo usa la biblioteca estándar. Uso: `python scripts/validar_gateway.py`
"""

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
OPENAPI = RAIZ / "infra" / "gateway" / "openapi.yaml"
ENVOY = RAIZ / "infra" / "gateway" / "envoy.yaml"
OPENAPI_SOCIOS = RAIZ / "infra" / "gateway" / "openapi-socios.yaml"
CONTRATO_SOCIOS = RAIZ / "contracts" / "openapi" / "socios-v1.json"

PLAZO_SEGUNDOS = 0.7
# El canal móvil tiene 2 s: crear la cuenta del onboarding (hash de la clave) tarda ~1 s.
PLAZO_CANAL_APP = 2.0
RUTAS_CANAL_APP = {("POST", "/graphql/app"), ("POST", "/v1/graphql/app")}
RUTAS_ESPERADAS = {
    ("GET", "/health"),
    ("POST", "/graphql/web"),
    ("POST", "/graphql/movil"),
    ("POST", "/graphql/app"),
    ("POST", "/v1/graphql/web"),
    ("POST", "/v1/graphql/movil"),
    ("POST", "/v1/graphql/app"),
    ("GET", "/polizas/{*}/documento"),
    ("GET", "/propuestas/{*}/contrato.pdf"),
}
PARAMETRO = "{*}"  # cómo se escribe cualquier parámetro de ruta al comparar OpenAPI con Envoy


def extraer_openapi(texto: str) -> dict[tuple[str, str], float]:
    """Extrae {(METODO, ruta): deadline} de un OpenAPI 2.0 sencillo sin depender de PyYAML."""
    rutas: dict[tuple[str, str], float] = {}
    en_paths = False
    ruta_actual: str | None = None
    metodo_actual: str | None = None

    for linea in texto.splitlines():
        if re.match(r"^paths:\s*$", linea):
            en_paths = True
            continue
        if en_paths and re.match(r"^[a-zA-Z0-9_-]+:", linea):
            en_paths = False
            continue
        if not en_paths:
            continue

        m_ruta = re.match(r"^  (/[^:]+):\s*$", linea)
        if m_ruta:
            # `/polizas/{poliza_id}/documento` -> `/polizas/{*}/documento`
            ruta_actual = re.sub(r"\{[^}]+\}", PARAMETRO, m_ruta.group(1).strip())
            metodo_actual = None
            continue

        m_metodo = re.match(r"^    (get|post|put|delete|patch):\s*$", linea, re.IGNORECASE)
        if m_metodo and ruta_actual:
            metodo_actual = m_metodo.group(1).upper()
            continue

        m_deadline = re.match(r"^\s+deadline:\s*([0-9.]+)\s*$", linea)
        if m_deadline and ruta_actual and metodo_actual:
            rutas[(metodo_actual, ruta_actual)] = float(m_deadline.group(1))

    return rutas


def extraer_envoy(texto: str) -> dict[tuple[str, str], float]:
    """Extrae {(METODO, ruta): timeout_segundos} de la lista `routes:` de Envoy."""
    rutas: dict[tuple[str, str], float] = {}
    # Solo la lista de rutas: después vienen las respuestas locales y los filtros (las reglas de
    # `jwt_authn` también tienen `match` y `path`).
    texto = re.split(r"\n\s*(?:local_reply_config|http_filters):", texto, maxsplit=1)[0]
    bloques = re.split(r"\n\s*-\s+match:\s*\n", texto)
    for bloque in bloques[1:]:
        m_path = re.search(r'path:\s*"([^"]+)"', bloque)
        if m_path is None:
            # Ruta con parámetro: `safe_regex` con `[^/]+` en lugar de cada parámetro.
            m_regex = re.search(r'regex:\s*"([^"]+)"', bloque)
            if m_regex:
                ruta = m_regex.group(1).replace("[^/]+", PARAMETRO).replace("[.]", ".")
                m_path = re.match(r"(.+)", ruta)
        m_metodo = re.search(r'exact:\s*"([A-Z]+)"', bloque)
        m_timeout = re.search(r"timeout:\s*([0-9.]+)s\b", bloque)
        if m_path and m_metodo and m_timeout:
            rutas[(m_metodo.group(1), m_path.group(1))] = float(m_timeout.group(1))
    return rutas


def es_de_socios(ruta: str) -> bool:
    return ruta.startswith("/v1/") and not ruta.startswith("/v1/graphql")


def rutas_del_contrato_de_socios() -> set[tuple[str, str]]:
    contrato = json.loads(CONTRATO_SOCIOS.read_text(encoding="utf-8"))
    return {
        (metodo.upper(), re.sub(r"\{[^}]+\}", PARAMETRO, camino))
        for camino, operaciones in contrato["paths"].items()
        for metodo in operaciones
    }


def validar_socios(rutas_ev_socios: dict[tuple[str, str], float]) -> list[str]:
    errores: list[str] = []
    if not OPENAPI_SOCIOS.is_file():
        return [f"No existe {OPENAPI_SOCIOS.relative_to(RAIZ)}"]
    texto = OPENAPI_SOCIOS.read_text(encoding="utf-8")
    rutas_oa = extraer_openapi(texto)
    contrato = rutas_del_contrato_de_socios()
    for origen, rutas in (("openapi-socios.yaml", rutas_oa), ("envoy.yaml", rutas_ev_socios)):
        if set(rutas) != contrato:
            sobran, faltan = sorted(set(rutas) - contrato), sorted(contrato - set(rutas))
            errores.append(
                f"{origen} no tiene las rutas de socios del contrato: sobran {sobran}, "
                f"faltan {faltan}"
            )
    for origen, rutas in (("openapi-socios.yaml", rutas_oa), ("envoy.yaml", rutas_ev_socios)):
        for clave, plazo in sorted(rutas.items()):
            if abs(plazo - PLAZO_SEGUNDOS) > 1e-6:
                errores.append(
                    f"{origen} {clave[0]} {clave[1]}: plazo={plazo}s (debe ser {PLAZO_SEGUNDOS}s)"
                )
    if "x-google-jwks_uri" not in texto or "envoy.filters.http.jwt_authn" not in ENVOY.read_text(
        encoding="utf-8"
    ):
        errores.append(
            "el token del socio debe validarse en el borde (x-google-jwks_uri y jwt_authn)"
        )
    return errores


def validar(texto_openapi: str | None = None, texto_envoy: str | None = None) -> list[str]:
    errores: list[str] = []
    if texto_openapi is None:
        if not OPENAPI.is_file():
            return [f"No existe {OPENAPI.relative_to(RAIZ)}"]
        texto_openapi = OPENAPI.read_text(encoding="utf-8")
    if texto_envoy is None:
        if not ENVOY.is_file():
            return [f"No existe {ENVOY.relative_to(RAIZ)}"]
        texto_envoy = ENVOY.read_text(encoding="utf-8")

    if 'swagger: "2.0"' not in texto_openapi and "swagger: '2.0'" not in texto_openapi:
        errores.append('openapi.yaml debe ser OpenAPI 2.0 (swagger: "2.0") para GCP API Gateway')

    rutas_oa = extraer_openapi(texto_openapi)
    todas_ev = extraer_envoy(texto_envoy)
    rutas_ev = {k: v for k, v in todas_ev.items() if not es_de_socios(k[1])}
    if texto_envoy == (ENVOY.read_text(encoding="utf-8") if ENVOY.is_file() else None):
        errores.extend(validar_socios({k: v for k, v in todas_ev.items() if es_de_socios(k[1])}))

    if set(rutas_oa.keys()) != set(rutas_ev.keys()):
        errores.append(
            f"Las rutas de openapi.yaml ({sorted(rutas_oa.keys())}) no coinciden con las de "
            f"envoy.yaml ({sorted(rutas_ev.keys())})"
        )

    faltantes = RUTAS_ESPERADAS - set(rutas_oa.keys())
    if faltantes:
        errores.append(f"Faltan rutas obligatorias en el gateway: {sorted(faltantes)}")

    for clave, deadline in sorted(rutas_oa.items()):
        esperado = PLAZO_CANAL_APP if clave in RUTAS_CANAL_APP else PLAZO_SEGUNDOS
        if abs(deadline - esperado) > 1e-6:
            errores.append(
                f"openapi.yaml {clave[0]} {clave[1]}: deadline={deadline}s (debe ser {esperado}s)"
            )
    for clave, timeout in sorted(rutas_ev.items()):
        esperado = PLAZO_CANAL_APP if clave in RUTAS_CANAL_APP else PLAZO_SEGUNDOS
        if abs(timeout - esperado) > 1e-6:
            errores.append(
                f"envoy.yaml {clave[0]} {clave[1]}: timeout={timeout}s (debe ser {esperado}s)"
            )

    if "port_value: 8080" not in texto_envoy:
        errores.append("envoy.yaml debe escuchar en port_value: 8080")
    if "address: bff" not in texto_envoy or "port_value: 8000" not in texto_envoy:
        errores.append("envoy.yaml debe apuntar al cluster bff en el puerto 8000")

    return errores


def main() -> int:
    errores = validar()
    if errores:
        print("Configuración del API Gateway inconsistente:", file=sys.stderr)
        for e in errores:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(
        f"API Gateway consistente: {len(RUTAS_ESPERADAS)} rutas sincronizadas entre "
        f"openapi.yaml y envoy.yaml, y {len(rutas_del_contrato_de_socios())} de socios entre el "
        f"contrato, openapi-socios.yaml y envoy.yaml, con corte duro de {PLAZO_SEGUNDOS}s (700 ms)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
