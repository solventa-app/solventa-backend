"""Coberturas paramétricas de una póliza (KAN-259, HU-M04, Fase 4, D8).

Capa simple (regla #3: hexagonal solo en `acl-worker`). POLICY es el **único escritor** de
`coberturas_parametricas` (regla #1); CLAIMS solo lee por el endpoint interno
`GET /interno/coberturas-parametricas`.

La "celda" de clima es lat/lon redondeados a 2 decimales (ver la migración `0011`): el cruce
consulta por esa celda redondeada, no por el punto exacto.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_COLUMNAS = (
    "id, poliza_id, tipo, monto_fijo, moneda, vigencia_desde, vigencia_hasta, estado, "
    "numero_vuelo, fecha_salida, aeropuerto_origen, umbral_retraso_min, "
    "latitud, longitud, variable, umbral, ventana_horas, creado_en"
)


def _fila(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": fila["id"],
        "poliza_id": fila["poliza_id"],
        "tipo": fila["tipo"],
        "monto_fijo": {"valor": str(fila["monto_fijo"]), "moneda": fila["moneda"]},
        "vigencia_desde": fila["vigencia_desde"],
        "vigencia_hasta": fila["vigencia_hasta"],
        "estado": fila["estado"],
        "numero_vuelo": fila["numero_vuelo"],
        "fecha_salida": fila["fecha_salida"],
        "aeropuerto_origen": fila["aeropuerto_origen"],
        "umbral_retraso_min": fila["umbral_retraso_min"],
        "latitud": float(fila["latitud"]) if fila["latitud"] is not None else None,
        "longitud": float(fila["longitud"]) if fila["longitud"] is not None else None,
        "variable": fila["variable"],
        "umbral": str(fila["umbral"]) if fila["umbral"] is not None else None,
        "ventana_horas": fila["ventana_horas"],
        "creado_en": fila["creado_en"],
    }


class RepositorioCoberturasParametricas:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def crear_vuelo(
        self,
        poliza_id: str,
        monto_fijo: str,
        moneda: str,
        vigencia_desde: datetime,
        vigencia_hasta: datetime,
        numero_vuelo: str,
        fecha_salida: date,
        aeropuerto_origen: str,
        umbral_retraso_min: int,
    ) -> dict[str, Any]:
        return self._crear(
            poliza_id,
            "VUELO",
            monto_fijo,
            moneda,
            vigencia_desde,
            vigencia_hasta,
            numero_vuelo=numero_vuelo,
            fecha_salida=fecha_salida,
            aeropuerto_origen=aeropuerto_origen,
            umbral_retraso_min=umbral_retraso_min,
        )

    def crear_clima(
        self,
        poliza_id: str,
        monto_fijo: str,
        moneda: str,
        vigencia_desde: datetime,
        vigencia_hasta: datetime,
        latitud: float,
        longitud: float,
        variable: str,
        umbral: str,
        ventana_horas: int,
    ) -> dict[str, Any]:
        return self._crear(
            poliza_id,
            "CLIMA",
            monto_fijo,
            moneda,
            vigencia_desde,
            vigencia_hasta,
            latitud=latitud,
            longitud=longitud,
            variable=variable,
            umbral=umbral,
            ventana_horas=ventana_horas,
        )

    def _crear(
        self,
        poliza_id: str,
        tipo: str,
        monto_fijo: str,
        moneda: str,
        vigencia_desde: datetime,
        vigencia_hasta: datetime,
        **campos: Any,
    ) -> dict[str, Any]:
        cobertura_id = str(uuid.uuid4())
        with self._pool.connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                INSERT INTO coberturas_parametricas
                    (id, poliza_id, tipo, monto_fijo, moneda, vigencia_desde, vigencia_hasta,
                     numero_vuelo, fecha_salida, aeropuerto_origen, umbral_retraso_min,
                     latitud, longitud, variable, umbral, ventana_horas)
                VALUES (%(id)s, %(poliza_id)s, %(tipo)s, %(monto_fijo)s, %(moneda)s,
                        %(vigencia_desde)s, %(vigencia_hasta)s,
                        %(numero_vuelo)s, %(fecha_salida)s, %(aeropuerto_origen)s,
                        %(umbral_retraso_min)s,
                        %(latitud)s, %(longitud)s, %(variable)s, %(umbral)s, %(ventana_horas)s)
                RETURNING {_COLUMNAS}
                """,
                {
                    "id": cobertura_id,
                    "poliza_id": poliza_id,
                    "tipo": tipo,
                    "monto_fijo": Decimal(monto_fijo),
                    "moneda": moneda,
                    "vigencia_desde": vigencia_desde,
                    "vigencia_hasta": vigencia_hasta,
                    "numero_vuelo": campos.get("numero_vuelo"),
                    "fecha_salida": campos.get("fecha_salida"),
                    "aeropuerto_origen": campos.get("aeropuerto_origen"),
                    "umbral_retraso_min": campos.get("umbral_retraso_min"),
                    "latitud": campos.get("latitud"),
                    "longitud": campos.get("longitud"),
                    "variable": campos.get("variable"),
                    "umbral": Decimal(campos["umbral"]) if campos.get("umbral") else None,
                    "ventana_horas": campos.get("ventana_horas"),
                },
            )
            fila = cursor.fetchone()
        return _fila(fila)

    def vigentes_de_vuelo(self, numero_vuelo: str, fecha: date) -> list[dict[str, Any]]:
        """Las coberturas VUELO vigentes que calzan exacto con el vuelo y su fecha de salida."""
        with self._pool.connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT {_COLUMNAS} FROM coberturas_parametricas
                WHERE tipo = 'VUELO' AND estado = 'VIGENTE'
                  AND numero_vuelo = %(numero_vuelo)s AND fecha_salida = %(fecha)s
                """,
                {"numero_vuelo": numero_vuelo, "fecha": fecha},
            )
            return [_fila(f) for f in cursor.fetchall()]

    def vigentes_de_celda(
        self, latitud: float, longitud: float, variable: str | None = None
    ) -> list[dict[str, Any]]:
        """Las coberturas CLIMA vigentes de la celda (lat/lon redondeados a 2 decimales, ver
        `0011`); `variable` filtra opcionalmente cuál midió el evento."""
        with self._pool.connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT {_COLUMNAS} FROM coberturas_parametricas
                WHERE tipo = 'CLIMA' AND estado = 'VIGENTE'
                  AND round(latitud, 2) = round(%(latitud)s::numeric, 2)
                  AND round(longitud, 2) = round(%(longitud)s::numeric, 2)
                  AND (%(variable)s::text IS NULL OR variable = %(variable)s::text)
                """,
                {"latitud": latitud, "longitud": longitud, "variable": variable},
            )
            return [_fila(f) for f in cursor.fetchall()]

    def de_poliza(self, poliza_id: str) -> list[dict[str, Any]]:
        """Las coberturas de una póliza, para que la semilla sea idempotente (no duplica)."""
        with self._pool.connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT {_COLUMNAS} FROM coberturas_parametricas WHERE poliza_id = %(poliza_id)s",
                {"poliza_id": poliza_id},
            )
            return [_fila(f) for f in cursor.fetchall()]
