"""Socios de distribución (HU-W07, KAN-30): credenciales, presupuesto de cotizaciones y consumo.

AUTH es el **único escritor** de estas tablas (migración `0010_socios`). No hay alta por la API
(W07-21): los socios se crean con la CLI `app.crear_socio`. El BFF llama a estos endpoints internos
después de validar el token del socio.

Reglas:
- **La clave del socio se guarda cifrada** (AES-GCM, con el id del socio como contexto) porque el
  portal la muestra (W07-4). Se compara en tiempo constante y nunca se loguea.
- Tras `MAX_INTENTOS` fallos seguidos con una credencial, queda bloqueada `MINUTOS_BLOQUEO`. Siempre
  el mismo error para client_id inexistente, clave errónea, credencial bloqueada o socio suspendido.
- **Solo las cotizaciones descuentan presupuesto** (W07-9). `reservar` es un único `UPDATE` con la
  condición `consumidas < asignadas`, así que es atómico aunque lleguen varias a la vez.
- `consumos_socio` es append-only y no guarda datos personales.

Capa simple (regla 3): el `Protocol` existe solo para probar con dobles sin PostgreSQL."""

import hmac
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from app.cifrado import CifradorCampos, ErrorDeCifrado
from app.identificadores import id_opaco
from app.tokens_socio import FirmadorTokensSocio

logger = logging.getLogger("auth.socios")

ENTORNOS: tuple[str, ...] = ("PRODUCCION", "PRUEBAS")
APIS_CON_PRESUPUESTO: tuple[str, ...] = ("cotizaciones",)
RESULTADOS: tuple[str, ...] = ("OK", "ERROR_CLIENTE", "ERROR")
MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 15
MENSAJE_CREDENCIALES = "Credenciales inválidas."
MAXIMO_IDS_POR_CONSULTA = 200
PREFIJO_CLIENT_ID = {"PRODUCCION": "sol_prod_", "PRUEBAS": "sol_test_"}


class CredencialesSocioInvalidas(Exception):
    """client_id o clave incorrectos, credencial bloqueada o socio suspendido (mismo mensaje)."""


class PresupuestoAgotado(Exception):
    """El socio ya consumió todas sus cotizaciones del entorno."""


class DatoSocioInvalido(Exception):
    """La solicitud no cumple las reglas (entorno, api, tasa o fechas)."""


def validar_entorno(entorno: str) -> None:
    if entorno not in ENTORNOS:
        raise DatoSocioInvalido(f"Entorno desconocido: {entorno}.")


def validar_api_con_presupuesto(api: str) -> None:
    if api not in APIS_CON_PRESUPUESTO:
        raise DatoSocioInvalido(f"La API {api} no tiene presupuesto.")


def normalizar_tasa(tasa: str) -> Decimal:
    try:
        valor = Decimal(tasa)
    except InvalidOperation as error:
        raise DatoSocioInvalido("La tasa de comisión debe ser un decimal.") from error
    if not Decimal("0") <= valor < Decimal("1"):
        raise DatoSocioInvalido("La tasa de comisión va entre 0 y 1 (p. ej. 0.10 = 10 %).")
    return valor.quantize(Decimal("0.0001"))


def nueva_clave() -> str:
    return secrets.token_urlsafe(32)


def nuevo_client_id(entorno: str) -> str:
    return PREFIJO_CLIENT_ID[entorno] + secrets.token_hex(8)


def _publico(socio: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": socio["id"],
        "nombre": socio["nombre"],
        "estado": socio["estado"],
        "tasa_comision": format(Decimal(socio["tasa_comision"]).quantize(Decimal("0.0001")), "f"),
    }


class RepositorioSocios(Protocol):
    def obtener(self, socio_id: str) -> dict | None: ...

    def nombres(self, ids: list[str]) -> dict[str, str]: ...

    def guardar_socio(self, socio_id: str, nombre: str, tasa: Decimal) -> dict: ...

    def credencial_por_client_id(self, client_id: str) -> dict | None: ...

    def credenciales_vigentes(self, socio_id: str) -> list[dict]: ...

    def crear_credencial(
        self, socio_id: str, entorno: str, client_id: str, clave_cifrada: str
    ) -> dict: ...

    def rotar_credencial(
        self, socio_id: str, entorno: str, client_id: str, clave_cifrada: str
    ) -> dict: ...

    def registrar_fallo(self, credencial_id: str, bloquear_hasta: datetime | None) -> None: ...

    def registrar_exito(self, credencial_id: str) -> None: ...

    def fijar_presupuesto(self, socio_id: str, entorno: str, api: str, asignadas: int) -> None: ...

    def presupuesto(self, socio_id: str, entorno: str, api: str) -> dict | None: ...

    def reservar(self, socio_id: str, entorno: str, api: str) -> dict | None: ...

    def devolver(self, socio_id: str, entorno: str, api: str) -> dict | None: ...

    def registrar_consumo(
        self, socio_id: str, entorno: str, api: str, resultado: str, latencia_ms: int
    ) -> None: ...

    def consumo_por_api(
        self, socio_id: str, entorno: str, desde: date, hasta: date
    ) -> list[dict]: ...

    def consumo_diario(
        self, socio_id: str, entorno: str, desde: date, hasta: date
    ) -> list[dict]: ...


class RepositorioSociosPostgres:
    """Cada sentencia es una transacción (`autocommit`), salvo `rotar_credencial`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return dict(fila) if fila is not None else None

    def _varias(self, sql: str, parametros: dict) -> list[dict]:
        with self._pool.connection() as conexion:
            return [dict(f) for f in conexion.execute(sql, parametros).fetchall()]

    def obtener(self, socio_id: str) -> dict | None:
        return self._una(
            "SELECT id, nombre, estado, tasa_comision FROM socios WHERE id = %(id)s",
            {"id": socio_id},
        )

    def nombres(self, ids: list[str]) -> dict[str, str]:
        filas = self._varias("SELECT id, nombre FROM socios WHERE id = ANY(%(ids)s)", {"ids": ids})
        return {f["id"]: f["nombre"] for f in filas}

    def guardar_socio(self, socio_id: str, nombre: str, tasa: Decimal) -> dict:
        """Crea o actualiza el nombre y la tasa (la CLI es idempotente). La tasa nueva solo aplica
        a lo que se acepte después: POLICY congela la de cada propuesta (W07-7)."""
        fila = self._una(
            """
            INSERT INTO socios (id, nombre, tasa_comision) VALUES (%(id)s, %(nombre)s, %(tasa)s)
            ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre,
                tasa_comision = EXCLUDED.tasa_comision, actualizado_en = now()
            RETURNING id, nombre, estado, tasa_comision
            """,
            {"id": socio_id, "nombre": nombre, "tasa": tasa},
        )
        assert fila is not None
        return fila

    def credencial_por_client_id(self, client_id: str) -> dict | None:
        return self._una(
            """
            SELECT c.id, c.socio_id, c.entorno, c.client_id, c.clave_cifrada, c.intentos_fallidos,
                   c.bloqueado_hasta, c.creada_en, c.revocada_en, s.estado AS estado_socio
            FROM credenciales_socio c JOIN socios s ON s.id = c.socio_id
            WHERE c.client_id = %(client_id)s
            """,
            {"client_id": client_id},
        )

    def credenciales_vigentes(self, socio_id: str) -> list[dict]:
        return self._varias(
            """
            SELECT id, socio_id, entorno, client_id, clave_cifrada, creada_en
            FROM credenciales_socio WHERE socio_id = %(socio_id)s AND revocada_en IS NULL
            ORDER BY entorno
            """,
            {"socio_id": socio_id},
        )

    def crear_credencial(
        self, socio_id: str, entorno: str, client_id: str, clave_cifrada: str
    ) -> dict:
        fila = self._una(
            """
            INSERT INTO credenciales_socio (id, socio_id, entorno, client_id, clave_cifrada)
            VALUES (%(id)s, %(socio_id)s, %(entorno)s, %(client_id)s, %(clave)s)
            RETURNING id, socio_id, entorno, client_id, clave_cifrada, creada_en
            """,
            {
                "id": str(uuid.uuid4()),
                "socio_id": socio_id,
                "entorno": entorno,
                "client_id": client_id,
                "clave": clave_cifrada,
            },
        )
        assert fila is not None
        return fila

    def rotar_credencial(
        self, socio_id: str, entorno: str, client_id: str, clave_cifrada: str
    ) -> dict:
        """Revoca la vigente y crea otra en una sola transacción: nunca quedan dos ni ninguna."""
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                """
                UPDATE credenciales_socio SET revocada_en = now()
                WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s AND revocada_en IS NULL
                """,
                {"socio_id": socio_id, "entorno": entorno},
            )
            fila = conexion.execute(
                """
                INSERT INTO credenciales_socio (id, socio_id, entorno, client_id, clave_cifrada)
                VALUES (%(id)s, %(socio_id)s, %(entorno)s, %(client_id)s, %(clave)s)
                RETURNING id, socio_id, entorno, client_id, clave_cifrada, creada_en
                """,
                {
                    "id": str(uuid.uuid4()),
                    "socio_id": socio_id,
                    "entorno": entorno,
                    "client_id": client_id,
                    "clave": clave_cifrada,
                },
            ).fetchone()
        assert fila is not None
        return dict(fila)

    def registrar_fallo(self, credencial_id: str, bloquear_hasta: datetime | None) -> None:
        self._una(
            """
            UPDATE credenciales_socio SET
                intentos_fallidos = CASE WHEN %(bloquear)s::timestamptz IS NULL
                                         THEN intentos_fallidos + 1 ELSE 0 END,
                bloqueado_hasta = COALESCE(%(bloquear)s::timestamptz, bloqueado_hasta)
            WHERE id = %(id)s RETURNING id
            """,
            {"id": credencial_id, "bloquear": bloquear_hasta},
        )

    def registrar_exito(self, credencial_id: str) -> None:
        self._una(
            """
            UPDATE credenciales_socio SET intentos_fallidos = 0, bloqueado_hasta = NULL
            WHERE id = %(id)s AND (intentos_fallidos > 0 OR bloqueado_hasta IS NOT NULL)
            RETURNING id
            """,
            {"id": credencial_id},
        )

    def fijar_presupuesto(self, socio_id: str, entorno: str, api: str, asignadas: int) -> None:
        self._una(
            """
            INSERT INTO presupuestos_socio (socio_id, entorno, api, asignadas)
            VALUES (%(socio_id)s, %(entorno)s, %(api)s, %(asignadas)s)
            ON CONFLICT (socio_id, entorno, api) DO UPDATE SET asignadas = EXCLUDED.asignadas
            RETURNING socio_id
            """,
            {"socio_id": socio_id, "entorno": entorno, "api": api, "asignadas": asignadas},
        )

    def presupuesto(self, socio_id: str, entorno: str, api: str) -> dict | None:
        return self._una(
            """
            SELECT asignadas, consumidas FROM presupuestos_socio
            WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s AND api = %(api)s
            """,
            {"socio_id": socio_id, "entorno": entorno, "api": api},
        )

    def reservar(self, socio_id: str, entorno: str, api: str) -> dict | None:
        """`None` si no queda presupuesto (o no hay fila): atómico en un solo UPDATE."""
        return self._una(
            """
            UPDATE presupuestos_socio SET consumidas = consumidas + 1
            WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s AND api = %(api)s
              AND consumidas < asignadas
            RETURNING asignadas, consumidas
            """,
            {"socio_id": socio_id, "entorno": entorno, "api": api},
        )

    def devolver(self, socio_id: str, entorno: str, api: str) -> dict | None:
        return self._una(
            """
            UPDATE presupuestos_socio SET consumidas = consumidas - 1
            WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s AND api = %(api)s
              AND consumidas > 0
            RETURNING asignadas, consumidas
            """,
            {"socio_id": socio_id, "entorno": entorno, "api": api},
        )

    def registrar_consumo(
        self, socio_id: str, entorno: str, api: str, resultado: str, latencia_ms: int
    ) -> None:
        self._una(
            """
            INSERT INTO consumos_socio (socio_id, entorno, api, resultado, latencia_ms)
            VALUES (%(socio_id)s, %(entorno)s, %(api)s, %(resultado)s, %(latencia_ms)s)
            RETURNING id
            """,
            {
                "socio_id": socio_id,
                "entorno": entorno,
                "api": api,
                "resultado": resultado,
                "latencia_ms": latencia_ms,
            },
        )

    def consumo_por_api(self, socio_id: str, entorno: str, desde: date, hasta: date) -> list[dict]:
        return self._varias(
            """
            SELECT api, count(*) AS llamadas,
                   count(*) FILTER (WHERE resultado <> 'OK') AS errores,
                   round(avg(latencia_ms))::int AS latencia_media_ms
            FROM consumos_socio
            WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s
              AND ocurrido_en >= %(desde)s AND ocurrido_en < %(hasta)s::date + 1
            GROUP BY api ORDER BY api
            """,
            {"socio_id": socio_id, "entorno": entorno, "desde": desde, "hasta": hasta},
        )

    def consumo_diario(self, socio_id: str, entorno: str, desde: date, hasta: date) -> list[dict]:
        return self._varias(
            """
            SELECT (ocurrido_en AT TIME ZONE 'UTC')::date AS fecha, count(*) AS llamadas
            FROM consumos_socio
            WHERE socio_id = %(socio_id)s AND entorno = %(entorno)s
              AND ocurrido_en >= %(desde)s AND ocurrido_en < %(hasta)s::date + 1
            GROUP BY 1 ORDER BY 1
            """,
            {"socio_id": socio_id, "entorno": entorno, "desde": desde, "hasta": hasta},
        )


class ServicioSocios:
    def __init__(
        self,
        repositorio: RepositorioSocios,
        cifrador: CifradorCampos,
        firmador: FirmadorTokensSocio,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._cifrador = cifrador
        self._firmador = firmador
        self._reloj = reloj

    # ---------- Acceso ----------

    def jwks(self) -> dict[str, Any]:
        return self._firmador.jwks()

    def emitir_token(self, client_id: str, clave: str) -> dict[str, Any]:
        """Lanza `CredencialesSocioInvalidas` con el mismo mensaje en todos los casos."""
        credencial = self._repo.credencial_por_client_id(client_id)
        if credencial is None or credencial["revocada_en"] is not None:
            # Igual se compara, para no revelar por el tiempo qué client_id existen.
            hmac.compare_digest(clave.encode(), nueva_clave().encode())
            raise CredencialesSocioInvalidas(MENSAJE_CREDENCIALES)
        ahora = self._reloj()
        bloqueada = (
            credencial["bloqueado_hasta"] is not None and ahora < credencial["bloqueado_hasta"]
        )
        correcta = hmac.compare_digest(clave.encode(), self._clave_en_claro(credencial).encode())
        if bloqueada:
            logger.info("token de socio rechazado: credencial bloqueada")
            raise CredencialesSocioInvalidas(MENSAJE_CREDENCIALES)
        if not correcta:
            intentos = credencial["intentos_fallidos"] + 1
            bloquear = (
                ahora + timedelta(minutes=MINUTOS_BLOQUEO) if intentos >= MAX_INTENTOS else None
            )
            self._repo.registrar_fallo(credencial["id"], bloquear)
            if bloquear:
                logger.warning(
                    "credencial de socio bloqueada %d min socio=%s",
                    MINUTOS_BLOQUEO,
                    id_opaco(credencial["socio_id"]),
                )
            raise CredencialesSocioInvalidas(MENSAJE_CREDENCIALES)
        if credencial["estado_socio"] != "ACTIVO":
            raise CredencialesSocioInvalidas(MENSAJE_CREDENCIALES)
        self._repo.registrar_exito(credencial["id"])
        logger.info(
            "token de socio emitido socio=%s entorno=%s",
            id_opaco(credencial["socio_id"]),
            credencial["entorno"],
        )
        return self._firmador.emitir(credencial["socio_id"], credencial["entorno"])

    def _clave_en_claro(self, credencial: dict) -> str:
        try:
            return self._cifrador.descifrar(credencial["clave_cifrada"], credencial["socio_id"])
        except ErrorDeCifrado:
            logger.error(
                "no se pudo descifrar una clave de socio socio=%s", id_opaco(credencial["socio_id"])
            )
            return ""

    # ---------- Socio y credenciales ----------

    def obtener(self, socio_id: str) -> dict | None:
        socio = self._repo.obtener(socio_id)
        return _publico(socio) if socio else None

    def nombres(self, ids: list[str]) -> dict[str, str]:
        if len(ids) > MAXIMO_IDS_POR_CONSULTA:
            raise DatoSocioInvalido(f"Máximo {MAXIMO_IDS_POR_CONSULTA} ids por consulta.")
        return self._repo.nombres(ids) if ids else {}

    def credenciales(self, socio_id: str) -> list[dict] | None:
        """Las vigentes con la clave en claro (la muestra el portal). `None` si no existe."""
        if self._repo.obtener(socio_id) is None:
            return None
        return [self._credencial_publica(c) for c in self._repo.credenciales_vigentes(socio_id)]

    def rotar(self, socio_id: str, entorno: str) -> dict | None:
        validar_entorno(entorno)
        if self._repo.obtener(socio_id) is None:
            return None
        clave = nueva_clave()
        fila = self._repo.rotar_credencial(
            socio_id, entorno, nuevo_client_id(entorno), self._cifrador.cifrar(clave, socio_id)
        )
        logger.info("credencial de socio rotada socio=%s entorno=%s", id_opaco(socio_id), entorno)
        return {**self._credencial_publica(fila), "clave": clave}

    def _credencial_publica(self, fila: dict) -> dict:
        return {
            "entorno": fila["entorno"],
            "client_id": fila["client_id"],
            "clave": self._clave_en_claro(fila),
            "creada_en": fila["creada_en"],
        }

    def guardar(
        self,
        socio_id: str,
        nombre: str,
        tasa_comision: str,
        presupuestos: dict[str, int],
        credenciales: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Alta idempotente (CLI, W07-21): crea o actualiza el socio, fija el presupuesto de
        cotizaciones por entorno y crea la credencial de cada entorno **solo si no hay una
        vigente**. `credenciales` permite fijar `(client_id, clave)` (las del socio demo local); sin
        ellas se generan al azar. Devuelve las claves creadas en esta corrida, para mostrarlas una
        sola vez."""
        nombre = " ".join(nombre.split())
        if not 2 <= len(nombre) <= 100:
            raise DatoSocioInvalido("El nombre del socio va entre 2 y 100 caracteres.")
        socio = self._repo.guardar_socio(socio_id, nombre, normalizar_tasa(tasa_comision))
        for entorno, asignadas in presupuestos.items():
            validar_entorno(entorno)
            if asignadas < 0:
                raise DatoSocioInvalido("El presupuesto no puede ser negativo.")
            self._repo.fijar_presupuesto(socio_id, entorno, "cotizaciones", asignadas)
        vigentes = {c["entorno"] for c in self._repo.credenciales_vigentes(socio_id)}
        creadas = []
        for entorno in ENTORNOS:
            if entorno in vigentes:
                continue
            client_id, clave = (credenciales or {}).get(entorno) or (
                nuevo_client_id(entorno),
                nueva_clave(),
            )
            try:
                fila = self._repo.crear_credencial(
                    socio_id, entorno, client_id, self._cifrador.cifrar(clave, socio_id)
                )
            except UniqueViolation as error:
                raise DatoSocioInvalido(f"El client_id de {entorno} ya existe.") from error
            creadas.append({**self._credencial_publica(fila), "clave": clave})
        return {"socio": _publico(socio), "credenciales_creadas": creadas}

    # ---------- Presupuesto y consumo ----------

    def reservar(self, socio_id: str, entorno: str, api: str) -> int:
        """Las cotizaciones que quedan tras reservar una. Lanza `PresupuestoAgotado`."""
        validar_entorno(entorno)
        validar_api_con_presupuesto(api)
        fila = self._repo.reservar(socio_id, entorno, api)
        if fila is None:
            raise PresupuestoAgotado()
        return fila["asignadas"] - fila["consumidas"]

    def devolver(self, socio_id: str, entorno: str, api: str) -> int:
        """Devuelve la unidad de una cotización que falló por un error nuestro (W07-9)."""
        validar_entorno(entorno)
        validar_api_con_presupuesto(api)
        fila = self._repo.devolver(socio_id, entorno, api) or self._repo.presupuesto(
            socio_id, entorno, api
        )
        return (fila["asignadas"] - fila["consumidas"]) if fila else 0

    def registrar_consumo(
        self, socio_id: str, entorno: str, api: str, resultado: str, latencia_ms: int
    ) -> None:
        validar_entorno(entorno)
        if resultado not in RESULTADOS:
            raise DatoSocioInvalido(f"Resultado desconocido: {resultado}.")
        if not api or len(api) > 40:
            raise DatoSocioInvalido("API inválida.")
        self._repo.registrar_consumo(socio_id, entorno, api, resultado, max(0, latencia_ms))

    def consumo(
        self, socio_id: str, entorno: str, desde: date | None, hasta: date | None
    ) -> dict[str, Any]:
        """Por defecto, los últimos 30 días."""
        validar_entorno(entorno)
        hasta = hasta or self._reloj().date()
        desde = desde or hasta - timedelta(days=29)
        if desde > hasta:
            raise DatoSocioInvalido("`desde` no puede ser posterior a `hasta`.")
        presupuesto = self._repo.presupuesto(socio_id, entorno, "cotizaciones") or {
            "asignadas": 0,
            "consumidas": 0,
        }
        return {
            "entorno": entorno,
            "desde": desde,
            "hasta": hasta,
            "presupuesto": {
                "asignadas": presupuesto["asignadas"],
                "consumidas": presupuesto["consumidas"],
                "restantes": max(0, presupuesto["asignadas"] - presupuesto["consumidas"]),
            },
            "por_api": self._repo.consumo_por_api(socio_id, entorno, desde, hasta),
            "diario": self._repo.consumo_diario(socio_id, entorno, desde, hasta),
        }
