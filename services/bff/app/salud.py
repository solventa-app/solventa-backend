"""Salud del sistema (HU-W07, bloque 6 del plan): la barra del portal de socios (`GET /v1/estado`) y
el detalle del BACKOFFICE (`estadoSistema`). Pesos, objetivos y reglas en `app/salud.json`, copia de
`contracts/salud.json` (`scripts/sincronizar_socios.py`).

**Fórmulas:**
- Un micro: 0 si `/health` no responde; si responde, `peso_disponibilidad` % de su disponibilidad
  (peticiones sin 5xx) más `peso_latencia` % del puntaje de latencia (100 en el objetivo, 0 al
  doble).
  Sin tráfico en la ventana, o sin fuente de métricas: 100.
- La barra general: promedio de los micros ponderado por su peso.
- Una API (Cotización o Emisión): promedio de la salud media de sus micros y la de sus APIs externas
  (circuito del ACL cerrado 100, semiabierto 50, abierto o desconocido 0). **Queda en ROJO si uno de
  sus micros no responde**, porque los necesita todos.
- **La barra general no queda en VERDE si alguna API está en ROJO** (como mucho, AMARILLO).
- Disponibilidad y latencia consolidadas: las del borde (el gateway), no un promedio de micros.

**Fuentes de métricas** (`METRICAS_FUENTE`): `ninguna` (CI y pruebas: solo `/health`),
`prometheus` (local, perfil `observabilidad`) o `monitoring` (Cloud Monitoring en staging). Son
clientes pequeños, sin puertos y adaptadores (regla 3). El resultado se guarda `CACHE_SEGUNDOS` por
ventana. El socio nunca ve nombres de micros (W07-14)."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

logger = logging.getLogger("bff.salud")

CONFIG = Path(__file__).resolve().parent / "salud.json"
CACHE_SEGUNDOS = 60
SEGUNDOS_POR_VENTANA = {"15m": 900, "1h": 3600, "24h": 86400, "7d": 604800}
ESTADO_CIRCUITO = {"closed": "CERRADO", "half-opened": "SEMIABIERTO", "opened": "ABIERTO"}
PUNTAJE_CIRCUITO = {"CERRADO": 100, "SEMIABIERTO": 50, "ABIERTO": 0, "DESCONOCIDO": 0}


def cargar_config(ruta: Path = CONFIG) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Metricas:
    peticiones: float
    disponibilidad: float | None = None  # % sin 5xx
    latencia_media_ms: int | None = None
    latencia_p95_ms: int | None = None


class FuenteMetricas(Protocol):
    async def micro(self, nombre: str, ventana: str) -> Metricas | None: ...

    async def borde(self, ventana: str) -> Metricas | None: ...


class SinMetricas:
    async def micro(self, nombre: str, ventana: str) -> Metricas | None:
        return None

    async def borde(self, ventana: str) -> Metricas | None:
        return None


class MetricasPrometheus:
    """Las métricas de `prometheus-fastapi-instrumentator` de cada micro (job `servicios`,
    `instance=<micro>:<puerto>`) y las de Envoy (job `gateway`)."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def _escalar(self, consulta: str) -> float | None:
        try:
            respuesta = await self._cliente.get("/api/v1/query", params={"query": consulta})
            resultado = respuesta.json()["data"]["result"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("Prometheus no respondió error=%s", type(error).__name__)
            return None
        if not resultado:
            return None
        valor = float(resultado[0]["value"][1])
        return None if valor != valor else valor  # NaN = sin datos

    async def micro(self, nombre: str, ventana: str) -> Metricas | None:
        filtro = f'job="servicios",instance=~"{nombre}:.*",handler!="/metrics"'
        total, buenas, suma, cuenta, p95 = await asyncio.gather(
            self._escalar(f"sum(increase(http_requests_total{{{filtro}}}[{ventana}]))"),
            self._escalar(
                f'sum(increase(http_requests_total{{{filtro},status!="5xx"}}[{ventana}]))'
            ),
            self._escalar(
                f"sum(increase(http_request_duration_seconds_sum{{{filtro}}}[{ventana}]))"
            ),
            self._escalar(
                f"sum(increase(http_request_duration_seconds_count{{{filtro}}}[{ventana}]))"
            ),
            self._escalar(
                "histogram_quantile(0.95, sum by (le) "
                f"(increase(http_request_duration_seconds_bucket{{{filtro}}}[{ventana}])))"
            ),
        )
        return _metricas(total, buenas, suma, cuenta, p95)

    async def borde(self, ventana: str) -> Metricas | None:
        filtro = 'job="gateway",envoy_http_conn_manager_prefix="ingress_http"'
        total, malas, suma, cuenta = await asyncio.gather(
            self._escalar(f"sum(increase(envoy_http_downstream_rq_total{{{filtro}}}[{ventana}]))"),
            self._escalar(
                f"sum(increase(envoy_http_downstream_rq_xx{{{filtro},"
                f'envoy_response_code_class="5"}}[{ventana}]))'
            ),
            self._escalar(
                f"sum(increase(envoy_http_downstream_rq_time_sum{{{filtro}}}[{ventana}]))"
            ),
            self._escalar(
                f"sum(increase(envoy_http_downstream_rq_time_count{{{filtro}}}[{ventana}]))"
            ),
        )
        buenas = None if total is None else total - (malas or 0)
        # Envoy mide en milisegundos; `_metricas` espera segundos.
        return _metricas(total, buenas, None if suma is None else suma / 1000, cuenta, None)


class MetricasMonitoring:
    """Cloud Monitoring (staging): las métricas nativas de Cloud Run por servicio y las del API
    Gateway para el borde. Necesita `roles/monitoring.viewer` en la cuenta del BFF."""

    API = "https://monitoring.googleapis.com/v3"

    def __init__(self, proyecto: str, cliente: httpx.AsyncClient | None = None) -> None:
        self._proyecto = proyecto
        self._cliente = cliente or httpx.AsyncClient(timeout=5)
        self._credenciales: Any = None

    async def _token(self) -> str:
        import google.auth
        import google.auth.transport.requests

        if self._credenciales is None:
            self._credenciales, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/monitoring.read"]
            )
        if not self._credenciales.valid:
            await asyncio.to_thread(
                self._credenciales.refresh, google.auth.transport.requests.Request()
            )
        return self._credenciales.token

    async def _series(self, filtro: str, ventana: str, alineador: str, agrupar: list[str]):
        fin = datetime.now(UTC)
        segundos = SEGUNDOS_POR_VENTANA[ventana]
        params = {
            "filter": filtro,
            "interval.endTime": fin.isoformat(),
            "interval.startTime": datetime.fromtimestamp(
                fin.timestamp() - segundos, UTC
            ).isoformat(),
            "aggregation.alignmentPeriod": f"{segundos}s",
            "aggregation.perSeriesAligner": alineador,
            "aggregation.crossSeriesReducer": "REDUCE_SUM"
            if alineador == "ALIGN_DELTA"
            else "REDUCE_MEAN",
            "aggregation.groupByFields": agrupar,
        }
        try:
            respuesta = await self._cliente.get(
                f"{self.API}/projects/{self._proyecto}/timeSeries",
                params=params,
                headers={"Authorization": f"Bearer {await self._token()}"},
            )
            respuesta.raise_for_status()
            return respuesta.json().get("timeSeries", [])
        except Exception as error:  # noqa: BLE001 - sin métricas la salud sale de /health
            logger.warning("Cloud Monitoring no respondió error=%s", type(error).__name__)
            return None

    @staticmethod
    def _valor(serie: dict) -> float:
        valor = serie["points"][0]["value"]
        if "distributionValue" in valor:
            return float(valor["distributionValue"].get("mean", 0))
        return float(valor.get("int64Value") or valor.get("doubleValue") or 0)

    async def micro(self, nombre: str, ventana: str) -> Metricas | None:
        base = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{nombre}"'
        conteos, media, p95 = await asyncio.gather(
            self._series(
                f'metric.type="run.googleapis.com/request_count" AND {base}',
                ventana,
                "ALIGN_DELTA",
                ["metric.label.response_code_class"],
            ),
            self._series(
                f'metric.type="run.googleapis.com/request_latencies" AND {base}',
                ventana,
                "ALIGN_DELTA",
                [],
            ),
            self._series(
                f'metric.type="run.googleapis.com/request_latencies" AND {base}',
                ventana,
                "ALIGN_PERCENTILE_95",
                [],
            ),
        )
        if conteos is None:
            return None
        total = sum(self._valor(s) for s in conteos)
        malas = sum(
            self._valor(s)
            for s in conteos
            if s.get("metric", {}).get("labels", {}).get("response_code_class") == "5xx"
        )
        return Metricas(
            peticiones=total,
            disponibilidad=100 * (total - malas) / total if total else None,
            latencia_media_ms=round(self._valor(media[0])) if media else None,
            latencia_p95_ms=round(self._valor(p95[0])) if p95 else None,
        )

    async def borde(self, ventana: str) -> Metricas | None:
        base = 'resource.type="apigee.googleapis.com/Proxy" OR resource.type="api"'
        conteos = await self._series(
            f'metric.type="serviceruntime.googleapis.com/api/request_count" AND ({base})',
            ventana,
            "ALIGN_DELTA",
            ["metric.label.response_code_class"],
        )
        if not conteos:
            return None
        total = sum(self._valor(s) for s in conteos)
        malas = sum(
            self._valor(s)
            for s in conteos
            if s.get("metric", {}).get("labels", {}).get("response_code_class") == "5xx"
        )
        media = await self._series(
            f'metric.type="serviceruntime.googleapis.com/api/request_latencies" AND ({base})',
            ventana,
            "ALIGN_DELTA",
            [],
        )
        return Metricas(
            peticiones=total,
            disponibilidad=100 * (total - malas) / total if total else None,
            # La latencia del gateway llega en segundos.
            latencia_media_ms=round(1000 * self._valor(media[0])) if media else None,
        )


def _metricas(total, buenas, suma_s, cuenta, p95_s) -> Metricas | None:
    if total is None:
        return None
    return Metricas(
        peticiones=total,
        disponibilidad=100 * buenas / total if total and buenas is not None else None,
        latencia_media_ms=round(1000 * suma_s / cuenta) if suma_s is not None and cuenta else None,
        latencia_p95_ms=round(1000 * p95_s) if p95_s is not None else None,
    )


# ---------- Cálculo ----------


def puntaje_latencia(media_ms: int | None, objetivo_ms: int) -> float:
    if media_ms is None or media_ms <= objetivo_ms:
        return 100.0
    if media_ms >= 2 * objetivo_ms:
        return 0.0
    return 100.0 * (2 * objetivo_ms - media_ms) / objetivo_ms


def color(porcentaje: float, umbrales: dict) -> str:
    if porcentaje >= umbrales["verde"]:
        return "VERDE"
    if porcentaje >= umbrales["amarillo"]:
        return "AMARILLO"
    return "ROJO"


Sonda = Callable[[], Awaitable[bool]]


class ServicioSalud:
    def __init__(
        self,
        sondas: dict[str, Sonda],
        circuitos: Callable[[], Awaitable[dict[str, str]]],
        metricas: FuenteMetricas | None = None,
        config: dict | None = None,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or cargar_config()
        self._sondas = sondas
        self._circuitos = circuitos
        self._metricas = metricas or SinMetricas()
        self._reloj = reloj
        self._cache: dict[str, tuple[float, dict]] = {}
        self.ventanas = tuple(self._config["ventanas"])

    async def _responde(self, nombre: str) -> bool:
        sonda = self._sondas.get(nombre)
        if sonda is None:
            return False
        try:
            return await sonda()
        except Exception:  # noqa: BLE001 - un /health que falla es un micro caído
            return False

    async def _externas(self) -> dict[str, str]:
        try:
            estados = await self._circuitos()
        except Exception:  # noqa: BLE001 - el ACL caído deja todo DESCONOCIDO
            return {}
        return {
            nombre: ESTADO_CIRCUITO.get(estado, "DESCONOCIDO") for nombre, estado in estados.items()
        }

    async def _micro(self, micro: dict, ventana: str) -> dict:
        nombre, objetivo = micro["nombre"], micro["objetivo_latencia_ms"]
        responde, metricas = await asyncio.gather(
            self._responde(nombre), self._metricas.micro(nombre, ventana)
        )
        sin_trafico = metricas is None or metricas.peticiones == 0
        if not responde:
            porcentaje = 0.0
        elif sin_trafico or metricas.disponibilidad is None:
            porcentaje = 100.0
        else:
            formula = self._config["formula"]
            porcentaje = (
                formula["peso_disponibilidad"] * metricas.disponibilidad
                + formula["peso_latencia"] * puntaje_latencia(metricas.latencia_media_ms, objetivo)
            ) / 100
        return {
            "nombre": nombre,
            "peso": micro["peso"],
            "porcentaje": round(porcentaje),
            "color": color(porcentaje, self._config["colores"]),
            "responde": responde,
            "sin_trafico": sin_trafico,
            "disponibilidad": None if sin_trafico else _redondear(metricas.disponibilidad),
            "latencia_media_ms": None if sin_trafico else metricas.latencia_media_ms,
            "latencia_p95_ms": None if sin_trafico else metricas.latencia_p95_ms,
        }

    async def calcular(self, ventana: str) -> dict:
        guardado = self._cache.get(ventana)
        if guardado and self._reloj() - guardado[0] < CACHE_SEGUNDOS:
            return guardado[1]
        umbrales, reglas = self._config["colores"], self._config["reglas"]
        micros_list, externas, borde = await asyncio.gather(
            asyncio.gather(*(self._micro(m, ventana) for m in self._config["micros"])),
            self._externas(),
            self._metricas.borde(ventana),
        )
        micros = {m["nombre"]: m for m in micros_list}
        peso_total = sum(m["peso"] for m in micros_list)
        general = sum(m["peso"] * m["porcentaje"] for m in micros_list) / peso_total
        apis = []
        for api in self._config["apis"]:
            suyos = [micros[n] for n in api["micros"]]
            de_micros = sum(m["porcentaje"] for m in suyos) / len(suyos)
            estados = [
                {"nombre": n, "estado": externas.get(n, "DESCONOCIDO")} for n in api["externas"]
            ]
            de_externas = sum(PUNTAJE_CIRCUITO[e["estado"]] for e in estados) / len(estados)
            porcentaje = (de_micros + de_externas) / 2
            api_color = color(porcentaje, umbrales)
            if reglas["api_en_rojo_si_un_micro_no_responde"] and not all(
                m["responde"] for m in suyos
            ):
                api_color = "ROJO"
            apis.append(
                {
                    "nombre": api["nombre"],
                    "porcentaje": round(porcentaje),
                    "color": api_color,
                    "micros": list(api["micros"]),
                    "externas": estados,
                }
            )
        general_color = color(general, umbrales)
        hay_roja = any(a["color"] == "ROJO" for a in apis)
        if reglas["general_no_verde_si_una_api_en_rojo"] and hay_roja and general_color == "VERDE":
            general_color = "AMARILLO"
        resultado = {
            "porcentaje": round(general),
            "color": general_color,
            "disponibilidad": None if borde is None else _redondear(borde.disponibilidad),
            "latencia_media_ms": None if borde is None else borde.latencia_media_ms,
            "ventana": ventana,
            "calculado_en": datetime.now(UTC),
            "apis": apis,
            "micros": micros_list,
        }
        self._cache[ventana] = (self._reloj(), resultado)
        return resultado

    async def para_socio(self, ventana: str) -> dict:
        """`GET /v1/estado`: sin nombres de micros ni de proveedores (W07-14)."""
        datos = await self.calcular(ventana)
        return {
            "porcentaje": datos["porcentaje"],
            "color": datos["color"],
            "disponibilidad": datos["disponibilidad"],
            "latenciaMediaMs": datos["latencia_media_ms"],
            "ventana": ventana,
            "calculadoEn": datos["calculado_en"].isoformat(),
            "apis": [
                {"nombre": a["nombre"], "porcentaje": a["porcentaje"], "color": a["color"]}
                for a in datos["apis"]
            ],
        }


def _redondear(valor: float | None) -> float | None:
    return None if valor is None else round(valor, 2)


def sonda_http(cliente: httpx.AsyncClient) -> Sonda:
    async def responde() -> bool:
        try:
            respuesta = await cliente.get("/health", timeout=1.0)
        except httpx.HTTPError:
            return False
        return respuesta.status_code == 200

    return responde
