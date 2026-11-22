"""Semilla de pólizas con cobertura paramétrica (KAN-259, HU-M04, Fase 4, D8).

Uso: `python -m app.sembrar_polizas_parametricas` (tarea `sembrar-polizas-parametricas` de docker
compose). Solo para local y pruebas, como `app.sembrar_demo`.

Crea 3 pólizas `EMITIDA` (propuestas y pólizas con ids fijos, mismo patrón que `sembrar_demo.py`) y
les agrega una cobertura paramétrica:

- Dos de VUELO, con `AV9340` (retraso sintético de 145 min en `stubs/eventos-parametricos`, sobre
  cualquier umbral razonable) y `AV1234` (10 min, bajo umbral), ambas con fecha de salida
  `2026-11-11` y `umbral_retraso_min=120`.
- Una de CLIMA en una celda de Bogotá (`4.62,-74.09`), cercana pero **distinta** a la celda que usa
  la sonda de recuperación del circuito del ACL Worker (que no consulta una celda fija: construye
  la sintética con cualquier punto, así que no hay colisión real; se eligió a propósito un punto
  distinto al de ejemplo del contrato, `4.61,-74.08`, para no confundir en los logs), con
  `variable=VIENTO_KMH` y `umbral=1` (km/h: el viento máximo en 24 h siempre lo supera, así la
  demo dispara siempre).

Demo en staging (`SEMILLA_CLIENTE_ID=<usuario de AUTH>` y `SEMILLA_TITULAR_ID=<titular_id>`, el
que devuelve `GET /usuarios/{id}/identidad` de AUTH): en vez de lo anterior crea 2 pólizas de ese
cliente, con el titular como `cliente_id` (así le salen en `miCoberturaParametrica`) y el usuario
como quien la aceptó (así CLAIMS le manda el push y el BFF le muestra `indemnizacion`): VUELO
(`SEMILLA_VUELO`, por defecto `AV9340`) con `umbral_retraso_min=0` y una cobertura por cada día
desde hoy durante `SEMILLA_DIAS` (90), para que el Cloud Scheduler que consulta `fecha: "hoy"`
siempre encuentre póliza; y CLIMA igual a la de arriba.

Varios clientes a la vez: `SEMILLA_CORREOS=<correo1>,<correo2>,...` (el correo con el que cada uno
entra a la app; necesita `AUTH_URL`, que resuelve el usuario y su titular) o
`SEMILLA_CLIENTES=<usuario1>,<usuario2>,...` (ids de AUTH). `SEMILLA_VUELO` vale por defecto
`RETRASADO`, el comodín de la demo: una sola cobertura que calza con cualquier vuelo y fecha,
porque el ACL toma el primer vuelo retrasado que trae AviationStack (así el push de vuelo sale
siempre).

Es idempotente: los ids de propuesta son fijos y, si la póliza ya tiene coberturas, no se
duplican (`RepositorioCoberturasParametricas.de_poliza`)."""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.application.autenticacion_servicios import autenticacion_para, modo_desde_entorno
from app.application.cifrado import cifrador_desde_entorno
from app.application.repositorio_coberturas_parametricas import (
    VUELO_COMODIN,
    RepositorioCoberturasParametricas,
)
from app.application.repositorio_propuestas import RepositorioPropuestasPostgres, construir_pool

log = logging.getLogger("sembrar_polizas_parametricas")

VIGENCIA_DESDE = datetime(2026, 1, 1, tzinfo=UTC)
VIGENCIA_HASTA = datetime(2027, 1, 1, tzinfo=UTC)

PROPUESTAS = [
    {
        "propuesta_id": "demo-m04-vuelo-prop-01",
        "oferta_id": "demo-m04-vuelo-oferta-01",
        "cliente_id": "demo-m04-cliente-vuelo-01",
        "prima": {"valor": "45000", "moneda": "COP"},
        "cobertura": {"valor": "500000", "moneda": "COP"},
    },
    {
        "propuesta_id": "demo-m04-vuelo-prop-02",
        "oferta_id": "demo-m04-vuelo-oferta-02",
        "cliente_id": "demo-m04-cliente-vuelo-02",
        "prima": {"valor": "45000", "moneda": "COP"},
        "cobertura": {"valor": "500000", "moneda": "COP"},
    },
    {
        "propuesta_id": "demo-m04-clima-prop-01",
        "oferta_id": "demo-m04-clima-oferta-01",
        "cliente_id": "demo-m04-cliente-clima-01",
        "prima": {"valor": "38000", "moneda": "COP"},
        "cobertura": {"valor": "400000", "moneda": "COP"},
    },
]


def _meses(desde: datetime, hasta: datetime) -> int:
    return (hasta.year - desde.year) * 12 + (hasta.month - desde.month)


def _sembrar_poliza(repositorio: RepositorioPropuestasPostgres, propuesta: dict) -> str:
    repositorio.crear_o_obtener(
        propuesta["propuesta_id"],
        {
            "oferta_id": propuesta["oferta_id"],
            "cliente_id": propuesta["cliente_id"],
            "tipo": "COMPLETA",
            "prima": propuesta["prima"],
            "cobertura": propuesta["cobertura"],
            "plazo_meses": _meses(VIGENCIA_DESDE, VIGENCIA_HASTA),
            "nivel_riesgo": "BAJO",
            "producto": "VIDA_DEUDOR",
            # El dueño en AUTH: sin él CLAIMS no sabe a quién mandarle el push y el BFF no le
            # muestra `indemnizacion` al cliente.
            "actor_usuario_id": propuesta.get("actor_usuario_id"),
        },
    )
    poliza, _ = repositorio.emitir_poliza(
        propuesta["propuesta_id"],
        propuesta["cliente_id"],
        VIGENCIA_DESDE,
        VIGENCIA_HASTA,
        desde=("ACEPTADA",),
    )
    return poliza["id"]


def sembrar(
    repositorio: RepositorioPropuestasPostgres, coberturas: RepositorioCoberturasParametricas
) -> list[dict]:
    creadas = []

    poliza_vuelo_1 = _sembrar_poliza(repositorio, PROPUESTAS[0])
    if not coberturas.de_poliza(poliza_vuelo_1):
        coberturas.crear_vuelo(
            poliza_vuelo_1,
            "500000",
            "COP",
            VIGENCIA_DESDE,
            VIGENCIA_HASTA,
            numero_vuelo="AV9340",
            fecha_salida=datetime(2026, 11, 11).date(),
            aeropuerto_origen="BOG",
            umbral_retraso_min=120,
        )
    creadas.append({"poliza_id": poliza_vuelo_1, "tipo": "VUELO", "numero_vuelo": "AV9340"})

    poliza_vuelo_2 = _sembrar_poliza(repositorio, PROPUESTAS[1])
    if not coberturas.de_poliza(poliza_vuelo_2):
        coberturas.crear_vuelo(
            poliza_vuelo_2,
            "500000",
            "COP",
            VIGENCIA_DESDE,
            VIGENCIA_HASTA,
            numero_vuelo="AV1234",
            fecha_salida=datetime(2026, 11, 11).date(),
            aeropuerto_origen="BOG",
            umbral_retraso_min=120,
        )
    creadas.append({"poliza_id": poliza_vuelo_2, "tipo": "VUELO", "numero_vuelo": "AV1234"})

    poliza_clima = _sembrar_poliza(repositorio, PROPUESTAS[2])
    if not coberturas.de_poliza(poliza_clima):
        coberturas.crear_clima(
            poliza_clima,
            "400000",
            "COP",
            VIGENCIA_DESDE,
            VIGENCIA_HASTA,
            latitud=4.62,
            longitud=-74.09,
            variable="VIENTO_KMH",
            umbral="1",
            ventana_horas=24,
        )
    creadas.append({"poliza_id": poliza_clima, "tipo": "CLIMA", "celda": "4.62,-74.09"})

    return creadas


def sembrar_demo_staging(
    repositorio: RepositorioPropuestasPostgres,
    coberturas: RepositorioCoberturasParametricas,
    usuario_id: str,
    titular_id: str,
    numero_vuelo: str,
    dias: int,
) -> list[dict]:
    creadas = []
    sufijo = usuario_id[:12]
    poliza_vuelo = _sembrar_poliza(
        repositorio,
        {
            "propuesta_id": f"demo-m04-vuelo-{sufijo}",
            "oferta_id": f"demo-m04-vuelo-oferta-{sufijo}",
            "cliente_id": titular_id,
            "actor_usuario_id": usuario_id,
            "prima": {"valor": "45000", "moneda": "COP"},
            "cobertura": {"valor": "500000", "moneda": "COP"},
        },
    )
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    if numero_vuelo == VUELO_COMODIN:
        # El comodín calza con cualquier vuelo y fecha: basta una cobertura. Se agrega aunque la
        # póliza ya tenga las de un vuelo fijo (de una corrida anterior de la semilla).
        if not any(c["numero_vuelo"] == VUELO_COMODIN for c in coberturas.de_poliza(poliza_vuelo)):
            coberturas.crear_vuelo(
                poliza_vuelo,
                "500000",
                "COP",
                VIGENCIA_DESDE,
                VIGENCIA_HASTA,
                numero_vuelo=VUELO_COMODIN,
                fecha_salida=hoy,
                aeropuerto_origen="BOG",
                umbral_retraso_min=0,
            )
    elif not coberturas.de_poliza(poliza_vuelo):
        for i in range(dias):
            coberturas.crear_vuelo(
                poliza_vuelo,
                "500000",
                "COP",
                VIGENCIA_DESDE,
                VIGENCIA_HASTA,
                numero_vuelo=numero_vuelo,
                fecha_salida=hoy + timedelta(days=i),
                aeropuerto_origen="BOG",
                umbral_retraso_min=0,
            )
    creadas.append({"poliza_id": poliza_vuelo, "tipo": "VUELO", "numero_vuelo": numero_vuelo})

    poliza_clima = _sembrar_poliza(
        repositorio,
        {
            "propuesta_id": f"demo-m04-clima-{sufijo}",
            "oferta_id": f"demo-m04-clima-oferta-{sufijo}",
            "cliente_id": titular_id,
            "actor_usuario_id": usuario_id,
            "prima": {"valor": "38000", "moneda": "COP"},
            "cobertura": {"valor": "400000", "moneda": "COP"},
        },
    )
    if not coberturas.de_poliza(poliza_clima):
        coberturas.crear_clima(
            poliza_clima,
            "400000",
            "COP",
            VIGENCIA_DESDE,
            VIGENCIA_HASTA,
            latitud=4.62,
            longitud=-74.09,
            variable="VIENTO_KMH",
            umbral="1",
            ventana_horas=24,
        )
    creadas.append({"poliza_id": poliza_clima, "tipo": "CLIMA", "celda": "4.62,-74.09"})
    return creadas


class _Auth:
    """AUTH para la semilla: el usuario de un correo (`POST /usuarios/restablecimiento/buscar`) y
    su `titular_id` (`GET /usuarios/{id}/identidad`), para que la póliza le salga en
    `miCoberturaParametrica`. Sin `AUTH_URL` no resuelve nada: la póliza queda a nombre del
    usuario y el push y `indemnizacion` funcionan igual. Nunca loguea el correo (regla #6)."""

    def __init__(self, url: str) -> None:
        self._cliente = (
            httpx.Client(
                base_url=url, timeout=10.0, auth=autenticacion_para(modo_desde_entorno(), "auth")
            )
            if url
            else None
        )

    def usuario_de(self, correo: str) -> str | None:
        if self._cliente is None:
            return None
        try:
            respuesta = self._cliente.post(
                "/usuarios/restablecimiento/buscar", json={"correo": correo}
            )
            respuesta.raise_for_status()
            return respuesta.json().get("usuario_id")
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            log.warning("AUTH no encontró la cuenta error=%s", type(error).__name__)
            return None

    def titular_de(self, usuario_id: str) -> str | None:
        if self._cliente is None:
            return None
        try:
            respuesta = self._cliente.get(f"/usuarios/{usuario_id}/identidad")
            if respuesta.status_code == 200:
                return respuesta.json()["titular_id"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            log.warning("AUTH no dio la identidad error=%s", type(error).__name__)
        return None


def clientes_de_la_semilla(entorno, auth) -> list[str]:
    """Los usuarios de AUTH a los que se les siembra la demo: `SEMILLA_CLIENTES` (ids) o
    `SEMILLA_CLIENTE_ID`, más los de `SEMILLA_CORREOS` (AUTH resuelve cada correo a su usuario; un
    correo sin cuenta se salta y solo se cuenta en el log)."""
    ids = entorno.get("SEMILLA_CLIENTES") or entorno.get("SEMILLA_CLIENTE_ID", "")
    clientes = [c.strip() for c in ids.split(",") if c.strip()]
    correos = [c.strip() for c in entorno.get("SEMILLA_CORREOS", "").split(",") if c.strip()]
    encontrados = [u for u in (auth.usuario_de(c) for c in correos) if u]
    if correos:
        log.info("correos con cuenta: %d de %d", len(encontrados), len(correos))
    return list(dict.fromkeys(clientes + encontrados))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("Falta DATABASE_URL")
        return 1
    pool = construir_pool(database_url)
    try:
        repositorio = RepositorioPropuestasPostgres(pool, cifrador_desde_entorno())
        coberturas = RepositorioCoberturasParametricas(pool)
        auth = _Auth(os.environ.get("AUTH_URL", "").strip())
        clientes = clientes_de_la_semilla(os.environ, auth)
        if clientes or os.environ.get("SEMILLA_CORREOS"):
            creadas = []
            for usuario_id in clientes:
                titular = (
                    (os.environ.get("SEMILLA_TITULAR_ID", "").strip() if len(clientes) == 1 else "")
                    or auth.titular_de(usuario_id)
                    or usuario_id
                )
                creadas += sembrar_demo_staging(
                    repositorio,
                    coberturas,
                    usuario_id,
                    titular,
                    os.environ.get("SEMILLA_VUELO", VUELO_COMODIN).strip(),
                    int(os.environ.get("SEMILLA_DIAS", "90")),
                )
        else:
            creadas = sembrar(repositorio, coberturas)
    finally:
        pool.close()
    log.info("Pólizas paramétricas de demostración listas: %d", len(creadas))
    for c in creadas:
        log.info("poliza=%s %s", c["poliza_id"], c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
