"""Adaptador del clima (HU-M04, KAN-259, Fase 3): traducción pura a HTTP contra Open-Meteo
(`https://api.open-meteo.com/v1/forecast`, sin llave) o, en local y en el CI, contra
`stubs/eventos-parametricos`, que imita la misma forma de respuesta en `GET /v1/forecast`.

Mismo rol que `AdaptadorVehiculos`: sin Circuit Breaker ni caché aquí; eso lo orquesta
`ServicioEventosParametricos`. La celda (`ConsultaClima.clave`) es lo único que ven la caché y los
logs; la latitud y la longitud solo viajan al proveedor (datos de clima, no personales).
"""

from datetime import UTC, datetime

import httpx

from app.domain.puertos import ConsultaClima, MedicionClima

# Qué parámetro horario de Open-Meteo corresponde a cada variable de la cobertura paramétrica
# (D8, `docs/` del plan de push; la ventana y el umbral los evalúa POLICY/CLAIMS, no aquí).
VARIABLE_A_PARAMETRO = {
    "PRECIPITACION_MM": "precipitation",
    "VIENTO_KMH": "wind_speed_10m",
    "TEMP_MAX_C": "temperature_2m",
}
# Cómo se agrega la serie horaria del día en un único valor: la precipitación se acumula, el
# viento y la temperatura se toman en su pico.
AGREGACION = {
    "PRECIPITACION_MM": sum,
    "VIENTO_KMH": max,
    "TEMP_MAX_C": max,
}

# Celda sintética (Bogotá) con la que la sonda de recuperación prueba al proveedor (regla 5):
# nunca la celda de una póliza real.
CELDA_SONDA = ConsultaClima(
    clave="sonda", latitud=4.61, longitud=-74.08, variable="PRECIPITACION_MM"
)


class VariableClimaDesconocida(ValueError):
    pass


class SerieClimaVacia(ValueError):
    pass


class AdaptadorClima:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def consultar(self, consulta: ConsultaClima, timeout_s: float | None = None) -> MedicionClima:
        parametro = VARIABLE_A_PARAMETRO.get(consulta.variable)
        if parametro is None:
            raise VariableClimaDesconocida(consulta.variable)
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(
            "/v1/forecast",
            params={
                "latitude": consulta.latitud,
                "longitude": consulta.longitud,
                "hourly": parametro,
                "forecast_days": 1,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        serie = (respuesta.json().get("hourly") or {}).get(parametro) or []
        if not serie:
            raise SerieClimaVacia(consulta.variable)
        valor = AGREGACION[consulta.variable](float(v) for v in serie)
        return MedicionClima(
            valor=float(valor), medido_en=datetime.now(UTC).isoformat(), fuente="open-meteo"
        )

    def sondear(self) -> None:
        """Llamada sintética solo para la sonda de recuperación del circuito."""
        self.consultar(CELDA_SONDA)

    def cerrar(self) -> None:
        self._cliente.close()
