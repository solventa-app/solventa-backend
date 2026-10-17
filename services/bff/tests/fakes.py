"""Dobles de prueba del BFF: en memoria, sin levantar AUTH, ACL Worker, RISK ni RATING."""

import time
import uuid

import jwt

from app.clientes import Clientes, ErrorServicio, SolicitudInvalida
from app.configuracion import JWT_SECRETO_LOCAL

CONSENTIMIENTO_VIGENTE = {
    "id": "c-1",
    "finalidad": "cotizacion-vida-hipotecario",
    "version_texto": "v1",
    "otorgado_en": "2026-10-08T12:00:00+00:00",
    "revocado_en": None,
    "vigente": True,
}

# Misma forma que devuelve `POST /ofertas` de RATING
# (services/rating/app/application/servicio_oferta.py).
OFERTA_COMPLETA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "desgloseScore": [{"nombre": "historial-crediticio", "peso": 0.3}],
    "fuentes": [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "OK",
            "capturadoEn": None,
        }
    ],
    "prima": {"valor": "61200.00", "moneda": "COP"},
    "rangoPrima": None,
}

OFERTA_PRELIMINAR = {
    **OFERTA_COMPLETA,
    "tipo": "PRELIMINAR",
    "prima": None,
    "rangoPrima": {
        "minima": {"valor": "48960.00", "moneda": "COP"},
        "maxima": {"valor": "73440.00", "moneda": "COP"},
    },
    "fuentes": [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "CACHE",
            "capturadoEn": "2026-10-08T11:55:00+00:00",
        }
    ],
}


def token_de_prueba(
    rol: str,
    usuario_id: str = "u-1",
    secreto: str = JWT_SECRETO_LOCAL,
    segundos: int = 3600,
) -> str:
    """JWT firmado como lo firma AUTH (`services/auth/app/sesiones.py`)."""
    ahora = int(time.time())
    return jwt.encode(
        {"sub": usuario_id, "rol": rol, "scopes": [], "iat": ahora, "exp": ahora + segundos},
        secreto,
        algorithm="HS256",
    )


def _usuario_publico(usuario_id: str, alias: str, rol: str, estado: str = "ACTIVO") -> dict:
    return {
        "id": usuario_id,
        "usuario": alias,
        "rol": rol,
        "estado": estado,
        "creado_en": "2026-10-14T12:00:00+00:00",
    }


class AuthFalso:
    def __init__(self, consentimientos: dict | None = None, falla: bool = False) -> None:
        self.consentimientos = dict(
            {"c-1": CONSENTIMIENTO_VIGENTE} if consentimientos is None else consentimientos
        )
        self.falla = falla
        self.actores: list[dict | None] = []
        # Nombres de CLIENTE por id y las consultas hechas (para probar el lote).
        self.nombres: dict[str, str] = {}
        self.consultas_de_nombres: list[list[str]] = []
        # Usuarios conocidos por id; `claves` guarda alias -> clave (solo en el doble).
        self.usuarios: dict[str, dict] = {
            "u-1": _usuario_publico("u-1", "asesor.uno", "ASESOR"),
            "u-bo": _usuario_publico("u-bo", "admin.solventa", "BACKOFFICE"),
        }
        self.claves = {"asesor.uno": "clave-segura-1", "admin.solventa": "clave-segura-1"}

    async def registrar_consentimiento(
        self, finalidad: str, version_texto: str, actor: dict | None = None
    ) -> dict:
        if self.falla:
            raise ErrorServicio("caido")
        self.actores.append(actor)
        nuevo = {**CONSENTIMIENTO_VIGENTE, "id": "c-nuevo", "finalidad": finalidad}
        nuevo["version_texto"] = version_texto
        self.consentimientos[nuevo["id"]] = nuevo
        return nuevo

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        return self.consentimientos.get(consentimiento_id)

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        actual = self.consentimientos.get(consentimiento_id)
        if actual is None:
            return None
        revocado = {**actual, "revocado_en": "2026-10-08T13:00:00+00:00", "vigente": False}
        self.consentimientos[consentimiento_id] = revocado
        return revocado

    async def iniciar_sesion(self, usuario: str, clave: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        datos = next((u for u in self.usuarios.values() if u["usuario"] == usuario), None)
        if datos is None or self.claves.get(usuario) != clave or datos["estado"] != "ACTIVO":
            return None
        return {
            "token": token_de_prueba(datos["rol"], datos["id"]),
            "expira_en": "2026-10-14T13:00:00+00:00",
            "usuario": datos,
        }

    async def obtener_usuario(self, usuario_id: str) -> dict | None:
        return self.usuarios.get(usuario_id)

    async def listar_usuarios(self, rol: str | None) -> list[dict]:
        return [u for u in self.usuarios.values() if rol is None or u["rol"] == rol]

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]:
        if self.falla:
            raise ErrorServicio("caido")
        self.consultas_de_nombres.append(list(ids))
        return {i: self.nombres[i] for i in ids if i in self.nombres}

    async def crear_usuario(
        self, usuario: str, clave: str, rol: str, nombre: str | None = None
    ) -> dict:
        if "@" in usuario:
            raise SolicitudInvalida("El usuario no puede ser un correo ni un número de documento.")
        if any(u["usuario"] == usuario for u in self.usuarios.values()):
            raise SolicitudInvalida("Ya existe un usuario con ese alias.")
        nuevo = _usuario_publico(str(uuid.uuid4()), usuario, rol)
        self.usuarios[nuevo["id"]] = nuevo
        self.claves[usuario] = clave
        if nombre is not None:
            self.nombres[nuevo["id"]] = nombre
        return nuevo

    async def cambiar_rol_usuario(self, usuario_id: str, rol: str) -> dict | None:
        if usuario_id not in self.usuarios:
            return None
        self.usuarios[usuario_id] = {**self.usuarios[usuario_id], "rol": rol}
        return self.usuarios[usuario_id]

    async def suspender_usuario(self, usuario_id: str) -> dict | None:
        if usuario_id not in self.usuarios:
            return None
        self.usuarios[usuario_id] = {**self.usuarios[usuario_id], "estado": "SUSPENDIDO"}
        return self.usuarios[usuario_id]


class AclFalso:
    def __init__(self, fallan: set[str] | None = None) -> None:
        self.fallan = fallan or set()
        self.llamadas: list[tuple[str, str, str]] = []

    async def consultar_fuente(self, cliente_id: str, consentimiento_id: str, fuente: str) -> dict:
        self.llamadas.append((cliente_id, consentimiento_id, fuente))
        if fuente in self.fallan:
            raise ErrorServicio("timeout")
        return {
            "fuente": fuente,
            "datos": {"dato": 1},
            "capturado_en": "2026-10-08T12:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        }


class RiskFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.llamadas: list[tuple[str, list[dict]]] = []

    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict:
        self.llamadas.append((cliente_id, fuentes))
        if self.falla:
            raise ErrorServicio("HTTP 503")
        return {"cliente_id": cliente_id, "perfil_version": 1, "operation_time": "1700000000.1"}


class RatingFalso:
    def __init__(self, oferta: dict | None = None, error: Exception | None = None) -> None:
        self.oferta = oferta or OFERTA_COMPLETA
        self.error = error
        self.llamadas: list[tuple[str, str, str, int, str]] = []

    async def generar_oferta(
        self,
        cliente_id: str,
        operation_time: str,
        cobertura: str,
        plazo_meses: int,
        direccion_inmueble: str,
    ) -> dict:
        self.llamadas.append(
            (cliente_id, operation_time, cobertura, plazo_meses, direccion_inmueble)
        )
        if self.error is not None:
            raise self.error
        return self.oferta


def cobro_de_payments(cobro_id: str, poliza_id: str, estado: str, **extra) -> dict:
    """Misma forma que devuelve PAYMENTS (`services/payments/app/main.py`, `CobroResponse`)."""
    return {
        "id": cobro_id,
        "id_intento_cobro": f"intento-{cobro_id}",
        "poliza_id": poliza_id,
        "monto": "80000.00",
        "moneda": "COP",
        "estado": estado,
        "referencia": None,
        "motivo": None,
        "intentos": 1,
        "reintentos_rechazo": 0,
        "reintentos_rechazo_restantes": 3,
        "creado_en": "2026-10-16T12:00:00+00:00",
        "actualizado_en": "2026-10-16T12:00:00+00:00",
        **extra,
    }


class PaymentsFalso:
    """Doble de PAYMENTS: reintentar un pendiente lo deja cobrado; un rechazado con
    reintentos lo cobra y descuenta uno; sin reintentos da `SolicitudInvalida` (el 409
    real); un cobrado se devuelve sin cambios."""

    def __init__(self, cobros: list[dict] | None = None, falla: bool = False) -> None:
        self.cobros = {c["id"]: dict(c) for c in (cobros or [])}
        self.falla = falla
        self.filtros: list[tuple[str | None, str | None]] = []
        self.ordenes: list[dict] = []

    async def listar_cobros(self, poliza_id: str | None, estado: str | None) -> list[dict]:
        if self.falla:
            raise ErrorServicio("caido")
        self.filtros.append((poliza_id, estado))
        return [
            c
            for c in self.cobros.values()
            if (poliza_id is None or c["poliza_id"] == poliza_id)
            and (estado is None or c["estado"] == estado)
        ]

    async def reintentar_cobro(self, cobro_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        cobro = self.cobros.get(cobro_id)
        if cobro is None:
            return None
        if cobro["estado"] == "rechazado":
            if cobro["reintentos_rechazo_restantes"] == 0:
                raise SolicitudInvalida(
                    "Se agotaron los 3 reintentos: el cobro requiere otro medio de pago."
                )
            cobro.update(
                estado="cobrado",
                referencia="pg-reintento",
                motivo=None,
                reintentos_rechazo=cobro["reintentos_rechazo"] + 1,
                reintentos_rechazo_restantes=cobro["reintentos_rechazo_restantes"] - 1,
            )
            return cobro
        if cobro["estado"] == "pendiente":
            cobro.update(estado="cobrado", referencia="pg-reintento", motivo=None)
        return cobro

    async def crear_cobro(self, orden: dict) -> dict:
        """Idempotente por `id_intento_cobro`, como PAYMENTS: con otros datos, el 409 real."""
        if self.falla:
            raise ErrorServicio("caido")
        for cobro in self.cobros.values():
            if cobro["id_intento_cobro"] == orden["id_intento_cobro"]:
                if cobro["monto"] != orden["monto"] or cobro["poliza_id"] != orden["poliza_id"]:
                    raise SolicitudInvalida("Ese id_intento_cobro ya se usó con otros datos.")
                return cobro
        cobro = cobro_de_payments(
            f"cobro-{len(self.cobros) + 1}",
            orden["poliza_id"],
            "cobrado",
            id_intento_cobro=orden["id_intento_cobro"],
            cliente_id=orden.get("cliente_id"),
            monto=orden["monto"],
            moneda=orden["moneda"],
        )
        self.cobros[cobro["id"]] = cobro
        self.ordenes.append(orden)
        return cobro


def clientes_falsos(
    auth: AuthFalso | None = None,
    acl: AclFalso | None = None,
    risk: RiskFalso | None = None,
    rating: RatingFalso | None = None,
    payments: PaymentsFalso | None = None,
) -> Clientes:
    return Clientes(
        auth=auth or AuthFalso(),
        acl=acl or AclFalso(),
        risk=risk or RiskFalso(),
        rating=rating or RatingFalso(),
        payments=payments or PaymentsFalso(),
    )
