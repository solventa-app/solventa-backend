"""Cálculo del score y perfil de riesgo en RISK (Bloque 2, Bloque 5 y Bloque 8).

RISK calcula y persiste el score (`score_total`), el `nivel_riesgo`
(`BAJO` / `MEDIO` / `ALTO`) y la `fecha_vigencia` (30 días desde la creación
del perfil) junto con el documento del perfil en MongoDB y en la caché de Redis
(`ScoreCacheado`).

Los pesos, el origen y el carácter obligatorio de cada fuente se leen del
archivo único `contracts/fuentes.json` (Bloque 5), con fallback a las
constantes por defecto cuando el contenedor corre aislado sin el directorio
`contracts/` montado."""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

VERSION_REGLAS = "v1"
DIAS_VIGENCIA_PERFIL = 30

_ORDEN_CANONICO: tuple[str, ...] = (
    "historial-crediticio",
    "cuentas-bancarias",
    "afiliacion-pila",
    "camara-comercio",
    "antecedentes-judiciales",
)

_PESOS_POR_DEFECTO: dict[str, Decimal] = {
    "historial-crediticio": Decimal("0.30"),
    "cuentas-bancarias": Decimal("0.20"),
    "afiliacion-pila": Decimal("0.20"),
    "camara-comercio": Decimal("0.15"),
    "antecedentes-judiciales": Decimal("0.15"),
}

_ORIGEN_POR_DEFECTO: dict[str, str] = {
    "cuentas-bancarias": "OPEN_FINANCE",
    "historial-crediticio": "OPEN_FINANCE",
    "afiliacion-pila": "OPEN_DATA",
    "camara-comercio": "OPEN_DATA",
    "antecedentes-judiciales": "OPEN_DATA",
}

_OBLIGATORIAS_POR_DEFECTO: tuple[str, ...] = ("historial-crediticio",)


def cargar_configuracion_fuentes(
    ruta: Path | str | None = None,
) -> tuple[dict[str, Decimal], dict[str, str], tuple[str, ...]]:
    """Lee `contracts/fuentes.json` y devuelve `(pesos, origen_por_fuente, obligatorias)`.
    Si el archivo no existe, usa la configuración por defecto."""
    if ruta is not None:
        candidatas = [Path(ruta)]
    elif os.getenv("FUENTES_CONFIG_PATH"):
        candidatas = [Path(os.environ["FUENTES_CONFIG_PATH"])]
    else:
        actual = Path(__file__).resolve()
        candidatas = [
            p / "contracts" / "fuentes.json" for p in list(actual.parents)[:6]
        ]

    archivo = next((c for c in candidatas if c.is_file()), None)
    if archivo is None:
        return (
            dict(_PESOS_POR_DEFECTO),
            dict(_ORIGEN_POR_DEFECTO),
            _OBLIGATORIAS_POR_DEFECTO,
        )

    items: list[dict[str, Any]] = json.loads(archivo.read_text(encoding="utf-8"))
    por_nombre = {item["nombre"]: item for item in items}
    nombres_ordenados = [n for n in _ORDEN_CANONICO if n in por_nombre] + [
        item["nombre"] for item in items if item["nombre"] not in _ORDEN_CANONICO
    ]
    pesos = {nombre: Decimal(str(por_nombre[nombre]["peso"])) for nombre in nombres_ordenados}
    origenes = {nombre: str(por_nombre[nombre]["origen"]) for nombre in nombres_ordenados}
    obligatorias = tuple(
        nombre for nombre in nombres_ordenados if bool(por_nombre[nombre].get("obligatoria"))
    )
    return pesos, origenes, obligatorias


PESOS, ORIGEN_POR_FUENTE, FUENTES_OBLIGATORIAS = cargar_configuracion_fuentes()

NEUTRO = Decimal(50)
CERO = Decimal(0)
CIEN = Decimal(100)


def _acotar(valor: Decimal) -> Decimal:
    return min(CIEN, max(CERO, valor))


def _subscore_historial_crediticio(datos: dict[str, Any]) -> Decimal:
    score = datos.get("score_crediticio")
    if score is None:
        return NEUTRO
    valor = (Decimal(str(score)) - Decimal(150)) / Decimal(950 - 150) * CIEN
    return _acotar(valor)


def _subscore_cuentas_bancarias(datos: dict[str, Any]) -> Decimal:
    saldo = datos.get("saldo_promedio_3m")
    productos = datos.get("productos_activos")
    if saldo is None or productos is None:
        return NEUTRO
    valor = Decimal(str(saldo)) / Decimal(5_000_000) * Decimal(80) + Decimal(
        str(productos)
    ) * Decimal(10)
    return _acotar(valor)


def _subscore_afiliacion_pila(datos: dict[str, Any]) -> Decimal:
    estado = datos.get("estado_afiliacion")
    antiguedad = datos.get("antiguedad_meses")
    if estado is None or antiguedad is None:
        return NEUTRO
    base = Decimal(70) if estado == "activo" else Decimal(30)
    extra = min(Decimal(30), Decimal(str(antiguedad)) / Decimal(2))
    return _acotar(base + extra)


def _subscore_camara_comercio(datos: dict[str, Any]) -> Decimal:
    existe = datos.get("existe_registro")
    if existe is None:
        return NEUTRO
    return Decimal(70) if existe else Decimal(50)


def _subscore_antecedentes_judiciales(datos: dict[str, Any]) -> Decimal:
    vigentes = datos.get("antecedentes_vigentes")
    if vigentes is None:
        return NEUTRO
    return Decimal(20) if vigentes else Decimal(100)


_SUBSCORES: dict[str, Any] = {
    "historial-crediticio": _subscore_historial_crediticio,
    "cuentas-bancarias": _subscore_cuentas_bancarias,
    "afiliacion-pila": _subscore_afiliacion_pila,
    "camara-comercio": _subscore_camara_comercio,
    "antecedentes-judiciales": _subscore_antecedentes_judiciales,
}


def _fuente_fresca(fuente_doc: dict[str, Any] | None) -> bool:
    return (
        fuente_doc is not None
        and not fuente_doc.get("de_cache")
        and not fuente_doc.get("degradado")
    )


def _subscore(nombre: str, fuente_doc: dict[str, Any] | None) -> Decimal:
    if fuente_doc is None or fuente_doc.get("degradado"):
        return NEUTRO
    datos = fuente_doc.get("datos") or {}
    if not datos:
        return NEUTRO
    funcion = _SUBSCORES.get(nombre)
    if funcion is None:
        return NEUTRO
    return funcion(datos)


def calcular_score(fuentes: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """Devuelve `(score_total, desglose)`."""
    fuentes_por_nombre = {f["fuente"]: f for f in fuentes}

    acumulado = CERO
    for nombre, peso in PESOS.items():
        subscore = _subscore(nombre, fuentes_por_nombre.get(nombre))
        acumulado += peso * subscore

    score_total = int(_acotar(acumulado).to_integral_value(rounding=ROUND_HALF_UP))
    desglose = [{"nombre": nombre, "peso": float(peso)} for nombre, peso in PESOS.items()]
    return score_total, desglose


def calcular_nivel_riesgo(score_total: int) -> str:
    """Clasifica el score en `BAJO` (>= 75), `MEDIO` (50..74) o `ALTO` (< 50)."""
    if score_total >= 75:
        return "BAJO"
    if score_total >= 50:
        return "MEDIO"
    return "ALTO"


def calcular_fecha_vigencia(
    creado_en: datetime | None = None, dias_vigencia: int = DIAS_VIGENCIA_PERFIL
) -> str:
    """Vigencia ISO-8601 del perfil de riesgo (`creado_en + 30 días`)."""
    base = creado_en if creado_en is not None else datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return (base + timedelta(days=dias_vigencia)).isoformat()


def _tipo_oferta(fuentes: list[dict[str, Any]]) -> str:
    fuentes_por_nombre = {f["fuente"]: f for f in fuentes}
    todas_frescas = all(_fuente_fresca(fuentes_por_nombre.get(nombre)) for nombre in PESOS)
    return "COMPLETA" if todas_frescas else "PRELIMINAR"


def _estado_fuente(fuente_doc: dict[str, Any] | None) -> str:
    if fuente_doc is None or fuente_doc.get("degradado"):
        return "FALLO"
    if fuente_doc.get("de_cache"):
        return "CACHE"
    return "OK"


def _fuentes_consultadas(fuentes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fuentes_por_nombre = {f["fuente"]: f for f in fuentes}
    resultado = []
    for nombre in PESOS:
        fuente_doc = fuentes_por_nombre.get(nombre)
        estado = _estado_fuente(fuente_doc)
        capturado_en = (
            fuente_doc.get("capturado_en")
            if fuente_doc is not None and fuente_doc.get("de_cache")
            else None
        )
        resultado.append(
            {
                "id": nombre,
                "nombre": nombre,
                "origen": ORIGEN_POR_FUENTE.get(nombre, "OPEN_DATA"),
                "estado": estado,
                "capturadoEn": capturado_en,
            }
        )
    return resultado


def calcular_resultado_score(
    fuentes: list[dict[str, Any]],
    creado_en: datetime | None = None,
    version_reglas: str | None = None,
) -> dict[str, Any]:
    """Calcula el resultado completo del score y metadatos de `PerfilRiesgo`."""
    score_total, desglose = calcular_score(fuentes)
    nivel_riesgo = calcular_nivel_riesgo(score_total)
    fecha_vigencia = calcular_fecha_vigencia(creado_en)
    return {
        "score_total": score_total,
        "nivel_riesgo": nivel_riesgo,
        "fecha_vigencia": fecha_vigencia,
        "version_reglas": version_reglas or VERSION_REGLAS,
        "desglose_score": desglose,
        "tipo": _tipo_oferta(fuentes),
        "fuentes": _fuentes_consultadas(fuentes),
    }
