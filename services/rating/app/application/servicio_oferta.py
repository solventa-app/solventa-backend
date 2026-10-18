"""Cálculo de la oferta (T-W01-6 completo, HA-MOD-002, HA-LAT-001): score de
riesgo por fuente, prima de seguro de vida hipotecario y Cache-Aside del
score en Redis.

**MODELO GENÉRICO ILUSTRATIVO — no es una fórmula actuarial certificada.** Se
investigó el material del curso (PDF del proyecto, backlog de Jira,
`PlanningV1.2.xlsx`) y no hay ninguna fórmula ni ponderación numérica
definida ahí: solo la mención genérica de que debe existir un "motor de
reglas actuariales" que combine las 5 fuentes. Lo que sigue es un modelo
genérico de la industria (scoring tipo crediticio + pricing de seguro de
vida hipotecario estándar), pedido explícitamente así por el usuario y
documentado con el mismo nivel de honestidad que el hueco de cifrado de
campo en RISK (ver `services/risk/README.md`): sirve para demostrar el
mecanismo end-to-end (CA-W01-03/04/06, HA-LAT-001), no para fijar primas
reales.

## Score (0-100, por fuente, luego promedio ponderado)

| Fuente                  | Peso | Campos usados                              |
|--------------------------|------|---------------------------------------------|
| historial-crediticio      | 0.30 | `score_crediticio` (150-950)                |
| cuentas-bancarias         | 0.20 | `saldo_promedio_3m`, `productos_activos`    |
| afiliacion-pila           | 0.20 | `estado_afiliacion`, `antiguedad_meses`     |
| camara-comercio           | 0.15 | `existe_registro`                           |
| antecedentes-judiciales   | 0.15 | `antecedentes_vigentes`                     |

Si una fuente falta en el perfil, viene `degradado=True`, o su `datos` está
vacío (`{}`): se usa un valor neutro de 50/100 para esa fuente (empuja hacia
el centro, no hacia 0 — una fuente faltante no debe penalizar como si fuera
un mal dato).

`score_total = round(sum(peso_i * subscore_i))`, acotado a [0, 100].

## Prima (modelo genérico de seguro de vida hipotecario)

```
tasa_base_mensual = 0.00045   # 0.045% del monto asegurado por mes — ilustrativo
factor_riesgo = 2.2 - (score_total / 100) * 1.4   # 0.8 (score=100) .. 2.2 (score=0)
prima_mensual = cobertura * tasa_base_mensual * factor_riesgo * factor_plazo * factor_zona
```

`factor_plazo` (plazo del crédito en meses, también ilustrativo): hasta 60 → 0.95,
61-180 → 1.00, 181-240 → 1.05, más de 240 → 1.10.

`factor_zona`: ciudad detectada en el texto de la dirección del inmueble (sin
tildes ni mayúsculas, palabra completa): Bogotá 1.00, Medellín 1.00, Cali 1.03;
otra o no detectada 1.05. Ninguna de las 5 fuentes describe el inmueble, así que
esto es lo mínimo para que la dirección cuente; en producción se reemplazaría por
una fuente de geocodificación detrás del ACL Worker (regla #2). La dirección
nunca se loguea (regla #6).

Si la solicitud no trae plazo o dirección (un BFF anterior a este cambio), el
factor correspondiente es 1.00: la prima queda igual que antes.

## Datos insuficientes (CA-W01-05)

`historial-crediticio` es la única fuente obligatoria (decisión del equipo,
2026-10-17): si no llegó ni en línea ni de caché (estado `FALLO`), no hay oferta
y se lanza `DatosInsuficientes` (RATING responde 422 y el BFF lo traduce a
`ErrorOferta` con reintento). Las 5 fuentes se siguen consultando en paralelo: la
importancia se aplica aquí, después, para no pasarse del presupuesto de latencia.

## Oferta preliminar vs completa (CA-W01-04)

Si el historial crediticio llegó pero CUALQUIERA de las 5 fuentes no está fresca
(falta, `de_cache=True` o `degradado=True`): `tipo=PRELIMINAR`, se devuelve `rangoPrima` (±20%
alrededor de `prima_mensual`), sin `prima` puntual. Si las 5 están frescas:
`tipo=COMPLETA`, se devuelve `prima` puntual, sin `rangoPrima`.

## Cache-Aside del score (HA-LAT-001) — decisión de diseño

La clave es exactamente la especificada: `oferta:{cliente_id}:{perfil_version}:{version_reglas}`
(con `cliente_id` reemplazado por su hash opaco — `id_opaco`, mismo patrón de
defensa en profundidad que `acl-worker/app/application/cache.py`: la clave
nunca lleva un identificador personal en crudo, aunque viva en Redis interno).

Lo que se cachea bajo esa clave es el **score** (`score_total`, el desglose
de pesos y los metadatos de las 5 fuentes consultadas: origen/estado/fecha de
captura) — **no** la prima ni la oferta completa. Motivo: la clave
especificada no incluye `cobertura`, y `cobertura` es un dato de la
**solicitud** (puede cambiar entre dos llamadas para el mismo
`cliente_id`/`perfil_version`); cachear la prima bajo esa clave devolvería un
número incorrecto si dos solicitudes para el mismo perfil pidieran coberturas
distintas. El score y el desglose, en cambio, dependen solo del perfil (no de
la solicitud), así que cachearlos es seguro y es exactamente lo que nombra la
habilitadora ("Cache-Aside de **scores** en Redis", HA-LAT-001 en
`docs/sprint-1.md`). La prima se recalcula en cada solicitud a partir del
score (cacheado o no) y la `cobertura` recibida — es aritmética pura, sin
E/S, así que no cuesta nada repetirla.

`VERSION_REGLAS` es una constante: cuando cambien los pesos o la fórmula, se
sube (p. ej. a "v2") y la caché vieja queda huérfana — nunca se lee por
accidente con reglas distintas, y expira sola por el TTL."""

import json
import logging
import os
import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Protocol

from app.application.identificadores import id_opaco
from app.application.lector_perfiles import LectorPerfiles

logger = logging.getLogger("rating.oferta")

VERSION_REGLAS = "v1"
DIAS_VIGENCIA_PERFIL = 30

MONEDA = "COP"

_ORDEN_CANONICO: tuple[str, ...] = (
    "historial-crediticio",
    "cuentas-bancarias",
    "afiliacion-pila",
    "camara-comercio",
    "antecedentes-judiciales",
)

# Pesos exactos de la tabla de arriba (fracción 0-1, no porcentaje).
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
    """Lee `contracts/fuentes.json` (Bloque 5) con fallback a las constantes locales."""
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


# CA-W01-05: sin estas fuentes (ni en línea ni de caché) no hay oferta.
PESOS, ORIGEN_POR_FUENTE, FUENTES_OBLIGATORIAS = cargar_configuracion_fuentes()

# Factor por plazo del crédito: (plazo máximo en meses, factor). Ilustrativo.
FACTORES_PLAZO: tuple[tuple[int, Decimal], ...] = (
    (60, Decimal("0.95")),
    (180, Decimal("1.00")),
    (240, Decimal("1.05")),
)
FACTOR_PLAZO_LARGO = Decimal("1.10")

# Factor por ciudad detectada en la dirección (sin tildes, en minúscula). Ilustrativo.
FACTORES_CIUDAD: dict[str, Decimal] = {
    "bogota": Decimal("1.00"),
    "medellin": Decimal("1.00"),
    "cali": Decimal("1.03"),
}
FACTOR_OTRA_CIUDAD = Decimal("1.05")
UNO = Decimal(1)


class DatosInsuficientes(Exception):
    """Falta una fuente obligatoria: no se puede ofrecer ni una oferta preliminar."""


NEUTRO = Decimal(50)
CERO = Decimal(0)
CIEN = Decimal(100)

TASA_BASE_MENSUAL = Decimal("0.00045")
DOS_DECIMALES = Decimal("0.01")


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
    """Devuelve `(score_total, desglose)`. `desglose` son EXACTAMENTE los 5
    pesos de la tabla (sin exponer los subscores individuales — el contrato
    `FactorScore` solo pide `nombre`+`peso`)."""
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
    creado_en: datetime | str | None = None,
    dias_vigencia: int = DIAS_VIGENCIA_PERFIL,
) -> str:
    """Vigencia ISO-8601 del perfil de riesgo (`creado_en + 30 días`)."""
    if isinstance(creado_en, str):
        try:
            base = datetime.fromisoformat(creado_en)
        except ValueError:
            base = datetime.now(UTC)
    elif isinstance(creado_en, datetime):
        base = creado_en
    else:
        base = datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return (base + timedelta(days=dias_vigencia)).isoformat()


def factor_plazo(plazo_meses: int | None) -> Decimal:
    if plazo_meses is None:
        return UNO
    for maximo, factor in FACTORES_PLAZO:
        if plazo_meses <= maximo:
            return factor
    return FACTOR_PLAZO_LARGO


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def factor_zona(direccion_inmueble: str | None) -> Decimal:
    if direccion_inmueble is None:
        return UNO
    palabras = set(re.findall(r"[a-z]+", _sin_tildes(direccion_inmueble)))
    for ciudad, factor in FACTORES_CIUDAD.items():
        if ciudad in palabras:
            return factor
    return FACTOR_OTRA_CIUDAD


def calcular_prima(
    cobertura: Decimal,
    score_total: int,
    plazo_meses: int | None = None,
    direccion_inmueble: str | None = None,
) -> Decimal:
    factor_riesgo = Decimal("2.2") - (Decimal(score_total) / CIEN) * Decimal("1.4")
    prima_mensual = (
        cobertura
        * TASA_BASE_MENSUAL
        * factor_riesgo
        * factor_plazo(plazo_meses)
        * factor_zona(direccion_inmueble)
    )
    return prima_mensual.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _faltan_obligatorias(fuentes_consultadas: list[dict[str, Any]]) -> bool:
    estados = {f["nombre"]: f["estado"] for f in fuentes_consultadas}
    return any(estados.get(nombre, "FALLO") == "FALLO" for nombre in FUENTES_OBLIGATORIAS)


def calcular_rango_prima(prima: Decimal) -> tuple[Decimal, Decimal]:
    """±20% alrededor de `prima` (CA-W01-04, oferta preliminar)."""
    minima = (prima * Decimal("0.8")).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    maxima = (prima * Decimal("1.2")).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    return minima, maxima


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
        # Contrato (`FuenteConsultada.capturadoEn`): "presente si el dato viene de caché".
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


def _calcular_resultado_score(
    fuentes: list[dict[str, Any]],
    creado_en: datetime | str | None = None,
) -> dict[str, Any]:
    score_total, desglose = calcular_score(fuentes)
    return {
        "score_total": score_total,
        "nivel_riesgo": calcular_nivel_riesgo(score_total),
        "fecha_vigencia": calcular_fecha_vigencia(creado_en),
        "version_reglas": VERSION_REGLAS,
        "desglose_score": desglose,
        "tipo": _tipo_oferta(fuentes),
        "fuentes": _fuentes_consultadas(fuentes),
    }


def _clave_cache(cliente_id: str, perfil_version: int) -> str:
    # `VERSION_REGLAS` se lee como global al momento de la llamada (no es un
    # valor por defecto fijado en la firma): subir la versión invalida el
    # hit de inmediato, sin tocar las claves ya escritas (quedan huérfanas,
    # ver docstring del módulo).
    return f"oferta:{id_opaco(cliente_id)}:{perfil_version}:{VERSION_REGLAS}"


def _clave_cache_score(cliente_id: str) -> str:
    return f"score:{id_opaco(cliente_id)}:{VERSION_REGLAS}"


class PuertoCacheScore(Protocol):
    def obtener(self, clave: str) -> dict[str, Any] | None: ...

    def guardar(self, clave: str, valor: dict[str, Any]) -> None: ...


class ServicioOferta:
    def __init__(self, lector_perfiles: LectorPerfiles, cache_score: PuertoCacheScore) -> None:
        self._lector_perfiles = lector_perfiles
        self._cache_score = cache_score

    def invalidar_cache_cliente(
        self, cliente_id: str, perfil_version: int | None = None
    ) -> int:
        if hasattr(self._cache_score, "invalidar_cliente"):
            return int(
                self._cache_score.invalidar_cliente(
                    cliente_id, perfil_version, VERSION_REGLAS
                )
            )
        return 0

    def generar(
        self,
        cliente_id: str,
        operation_time: str,
        cobertura: str,
        plazo_meses: int | None = None,
        direccion_inmueble: str | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Devuelve `(oferta, score_de_cache)`. `oferta` es `None` si no hay
        perfil para `cliente_id` (el llamador decide el 404). Puede lanzar
        `ValueError`/`decimal.InvalidOperation` si `operation_time` o
        `cobertura` no tienen un formato válido — el llamador decide el 400 —, y
        `DatosInsuficientes` si falta una fuente obligatoria (CA-W01-05)."""
        perfil = self._lector_perfiles.leer(cliente_id, operation_time)
        if perfil is None:
            return None, False

        cobertura_decimal = Decimal(cobertura)

        clave = _clave_cache(cliente_id, perfil["perfil_version"])
        resultado_score = self._cache_score.obtener(clave)
        if resultado_score is None:
            candidato_risk = self._cache_score.obtener(_clave_cache_score(cliente_id))
            if candidato_risk is not None and candidato_risk.get("perfil_version") in (
                None,
                perfil["perfil_version"],
            ):
                resultado_score = candidato_risk

        score_de_cache = resultado_score is not None
        if resultado_score is None:
            if (
                perfil.get("score_total") is not None
                and perfil.get("version_reglas", VERSION_REGLAS) == VERSION_REGLAS
            ):
                score_guardado = int(perfil["score_total"])
                resultado_score = {
                    "score_total": score_guardado,
                    "nivel_riesgo": str(
                        perfil.get("nivel_riesgo") or calcular_nivel_riesgo(score_guardado)
                    ),
                    "fecha_vigencia": str(
                        perfil.get("fecha_vigencia")
                        or calcular_fecha_vigencia(perfil.get("creado_en"))
                    ),
                    "version_reglas": VERSION_REGLAS,
                    "desglose_score": [
                        {"nombre": nombre, "peso": float(peso)}
                        for nombre, peso in PESOS.items()
                    ],
                    "tipo": _tipo_oferta(perfil["fuentes"]),
                    "fuentes": _fuentes_consultadas(perfil["fuentes"]),
                }
            else:
                resultado_score = _calcular_resultado_score(
                    perfil["fuentes"], creado_en=perfil.get("creado_en")
                )
            self._cache_score.guardar(clave, resultado_score)

        nivel_riesgo = str(
            resultado_score.get("nivel_riesgo")
            or perfil.get("nivel_riesgo")
            or calcular_nivel_riesgo(int(resultado_score["score_total"]))
        )
        fecha_vigencia = str(
            resultado_score.get("fecha_vigencia")
            or perfil.get("fecha_vigencia")
            or calcular_fecha_vigencia(perfil.get("creado_en"))
        )

        logger.info(
            "oferta calculada cliente=%s perfil_version=%s score_cache=%s tipo=%s score=%s",
            id_opaco(cliente_id),
            perfil["perfil_version"],
            "hit" if score_de_cache else "miss",
            resultado_score["tipo"],
            resultado_score["score_total"],
        )

        if _faltan_obligatorias(resultado_score["fuentes"]):
            raise DatosInsuficientes("falta una fuente obligatoria")

        prima = calcular_prima(
            cobertura_decimal, resultado_score["score_total"], plazo_meses, direccion_inmueble
        )

        oferta: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "tipo": resultado_score["tipo"],
            "cobertura": {"valor": str(cobertura_decimal), "moneda": MONEDA},
            "desgloseScore": resultado_score["desglose_score"],
            "fuentes": resultado_score["fuentes"],
            "perfilRiesgo": {
                "score": int(resultado_score["score_total"]),
                "nivelRiesgo": nivel_riesgo,
                "fechaVigencia": fecha_vigencia,
            },
        }
        if resultado_score["tipo"] == "PRELIMINAR":
            minima, maxima = calcular_rango_prima(prima)
            oferta["prima"] = None
            oferta["rangoPrima"] = {
                "minima": {"valor": str(minima), "moneda": MONEDA},
                "maxima": {"valor": str(maxima), "moneda": MONEDA},
            }
        else:
            oferta["prima"] = {"valor": str(prima), "moneda": MONEDA}
            oferta["rangoPrima"] = None

        return oferta, score_de_cache

