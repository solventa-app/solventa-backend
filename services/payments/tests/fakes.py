"""Dobles de prueba compartidos por las pruebas de PAYMENTS — en memoria, sin
levantar PostgreSQL ni el ACL Worker reales (mismo patrón de las pruebas de
`acl-worker`/`risk`: fakes en vez de mocks)."""

import threading
from datetime import UTC, datetime
from typing import Any

from app.application.cliente_acl import OrdenCobro, ResultadoCobro, ResultadoConsulta


class RepositorioCobrosFalso:
    """Fake de `RepositorioCobros`. `crear_o_obtener` es thread-safe con un
    `Lock`, replicando la garantía real de la restricción UNIQUE de Postgres
    (bajo concurrencia real, una sola de las llamadas concurrentes con la
    mismo `id_intento_cobro` "gana" la inserción) — así la prueba de
    idempotencia concurrente no depende de un PostgreSQL real."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._por_clave: dict[str, dict[str, Any]] = {}
        self._por_id: dict[str, dict[str, Any]] = {}

    def crear_o_obtener(self, cobro_id: str, orden: dict[str, Any]) -> tuple[dict, bool]:
        with self._lock:
            existente = self._por_clave.get(orden["id_intento_cobro"])
            if existente is not None:
                return dict(existente), False

            cobro = {
                "id": cobro_id,
                "id_intento_cobro": orden["id_intento_cobro"],
                "poliza_id": orden["poliza_id"],
                "cliente_id": orden.get("cliente_id"),
                "monto": orden["monto"],
                "moneda": orden["moneda"],
                "token_medio_pago": orden["token_medio_pago"],
                "estado": "cobrando",
                "referencia": None,
                "motivo": None,
                "intentos": 1,
                "creado_en": datetime.now(UTC),
                "actualizado_en": datetime.now(UTC),
            }
            self._por_clave[orden["id_intento_cobro"]] = cobro
            self._por_id[cobro_id] = cobro
            return dict(cobro), True

    def actualizar_estado(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict:
        with self._lock:
            cobro = self._por_id[cobro_id]
            cobro["estado"] = estado
            cobro["referencia"] = referencia
            cobro["motivo"] = motivo
            return dict(cobro)

    def actualizar_estado_y_contar_intento(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict:
        with self._lock:
            cobro = self._por_id[cobro_id]
            cobro["estado"] = estado
            cobro["referencia"] = referencia
            cobro["motivo"] = motivo
            cobro["intentos"] += 1
            return dict(cobro)

    def listar_pendientes(self, max_intentos: int) -> list[dict]:
        with self._lock:
            return [
                dict(cobro)
                for cobro in self._por_id.values()
                if cobro["estado"] == "pendiente" and cobro["intentos"] < max_intentos
            ]

    def obtener(self, cobro_id: str) -> dict | None:
        with self._lock:
            cobro = self._por_id.get(cobro_id)
            return dict(cobro) if cobro else None

    def listar(self, poliza_id: str | None, estado: str | None) -> list[dict]:
        with self._lock:
            return [
                dict(c)
                for c in self._por_id.values()
                if (poliza_id is None or c["poliza_id"] == poliza_id)
                and (estado is None or c["estado"] == estado)
            ]

    def balance_poliza(self, poliza_id: str) -> str:
        with self._lock:
            total = sum(
                float(cobro["monto"])
                for cobro in self._por_id.values()
                if cobro["poliza_id"] == poliza_id and cobro["estado"] == "cobrado"
            )
            return f"{total:.2f}"


class ClienteAclFalso:
    """Fake de `PuertoClienteAcl`. Por defecto responde `cobrado`; se puede
    configurar una lista de resultados a devolver en orden (uno por llamada,
    repitiendo el último si se agotan) para simular, p. ej., primero
    `pendiente` y luego `cobrado` (como en el reintento real)."""

    def __init__(
        self,
        resultados: list[ResultadoCobro] | None = None,
        en_pasarela: dict[str, ResultadoConsulta] | None = None,
        consulta_desconocida: bool = False,
    ) -> None:
        self._resultados = resultados or [ResultadoCobro(estado="cobrado", referencia="ref-1")]
        self._lock = threading.Lock()
        self.ordenes: list[OrdenCobro] = []
        # Conciliación: lo que "la pasarela" ya tiene registrado, por clave.
        self.en_pasarela = en_pasarela or {}
        self.consulta_desconocida = consulta_desconocida
        self.consultas: list[str] = []

    def consultar(self, id_intento_cobro: str) -> ResultadoConsulta:
        with self._lock:
            self.consultas.append(id_intento_cobro)
            if self.consulta_desconocida:
                return ResultadoConsulta(resultado="desconocido")
            return self.en_pasarela.get(
                id_intento_cobro, ResultadoConsulta(resultado="no_encontrado")
            )

    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro:
        with self._lock:
            self.ordenes.append(orden)
            indice = min(len(self.ordenes) - 1, len(self._resultados) - 1)
            return self._resultados[indice]

    @property
    def llamadas(self) -> int:
        return len(self.ordenes)


class BusEventosFalso:
    """Fake de `PuertoBusEventos`: guarda los eventos publicados para que la
    prueba los inspeccione, en vez de loguearlos de verdad."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        with self._lock:
            self.eventos.append(evento)
