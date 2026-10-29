"""Alta de un socio de distribución desde la línea de comandos (HU-W07, W07-21).

En este MVP no hay pantalla ni endpoint de alta: los socios se crean aquí. Corre como tarea de un
solo uso (`crear-socios-demo` en docker-compose; en staging, un Cloud Run Job con la misma imagen),
igual que `crear_usuario`.

Uso:
    python -m app.crear_socio --id socio-demo-1 --nombre "Banco Demo" --tasa-comision 0.10 \\
        --presupuesto-cotizaciones 1000 --presupuesto-cotizaciones-pruebas 5000

Es idempotente: crea o actualiza el nombre y la tasa, **fija** el presupuesto de cotizaciones de
cada entorno y crea la credencial de cada entorno solo si no hay una vigente.

Las credenciales se pueden fijar con `SOCIO_CLIENT_ID_PRODUCCION` y `SOCIO_CLAVE_PRODUCCION` (y
lo mismo con `_PRUEBAS`): así lo hace el compose con los socios demo, cuyas claves son **solo
locales**.
Sin ellas se generan al azar y se imprimen **una sola vez** por la salida estándar, nunca en el log.
Las claves llegan por variables de entorno y nunca como argumentos (quedarían en el historial)."""

import argparse
import logging
import os
import sys

from app.cifrado import construir_cifrador
from app.configuracion import Configuracion
from app.socios import ENTORNOS, DatoSocioInvalido, RepositorioSociosPostgres, ServicioSocios
from app.tokens_socio import FirmadorTokensSocio
from app.usuarios import construir_pool

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("auth.crear_socio")


def credenciales_del_entorno() -> dict[str, tuple[str, str]]:
    """Las credenciales fijadas por variables de entorno (se necesitan el client_id y la clave)."""
    fijas = {}
    for entorno in ENTORNOS:
        client_id = os.getenv(f"SOCIO_CLIENT_ID_{entorno}")
        clave = os.getenv(f"SOCIO_CLAVE_{entorno}")
        if client_id and clave:
            fijas[entorno] = (client_id, clave)
    return fijas


def main(argumentos: list[str] | None = None, servicio: ServicioSocios | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alta de un socio de distribución (AUTH).")
    parser.add_argument("--id", required=True, help="Id estable del socio, p. ej. socio-demo-1")
    parser.add_argument("--nombre", required=True, help="Nombre comercial")
    parser.add_argument("--tasa-comision", required=True, help="Decimal entre 0 y 1, p. ej. 0.10")
    parser.add_argument("--presupuesto-cotizaciones", type=int, required=True)
    parser.add_argument("--presupuesto-cotizaciones-pruebas", type=int, required=True)
    opciones = parser.parse_args(argumentos)

    pool = None
    if servicio is None:
        config = Configuracion.desde_entorno()
        pool = construir_pool(config.database_url)
        servicio = ServicioSocios(
            RepositorioSociosPostgres(pool),
            construir_cifrador(config),
            FirmadorTokensSocio(config.socios_clave_firma),
        )
    fijas = credenciales_del_entorno()
    try:
        resultado = servicio.guardar(
            opciones.id,
            opciones.nombre,
            opciones.tasa_comision,
            {
                "PRODUCCION": opciones.presupuesto_cotizaciones,
                "PRUEBAS": opciones.presupuesto_cotizaciones_pruebas,
            },
            fijas,
        )
    except DatoSocioInvalido as error:
        logger.error("datos inválidos: %s", error)
        return 2
    finally:
        if pool is not None:
            pool.close()

    logger.info(
        "socio listo id=%s credenciales nuevas=%d",
        opciones.id,
        len(resultado["credenciales_creadas"]),
    )
    for credencial in resultado["credenciales_creadas"]:
        if credencial["entorno"] in fijas:
            continue  # las fijas ya las conoce quien las puso; no se repiten en la salida
        # Una sola vez y por la salida estándar: el log se recoge y se guarda, la salida no.
        entorno, client_id = credencial["entorno"], credencial["client_id"]
        print(f"{entorno}: client_id={client_id} clave={credencial['clave']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
