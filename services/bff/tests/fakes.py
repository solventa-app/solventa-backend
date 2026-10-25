"""Dobles de prueba del BFF: en memoria, sin levantar AUTH, ACL Worker, RISK ni RATING."""

import time
import uuid

import jwt

from app.clientes import (
    Clientes,
    ErrorServicio,
    FirmaRechazada,
    PropuestaRechazada,
    SolicitudInvalida,
)
from app.configuracion import JWT_SECRETO_LOCAL

CONSENTIMIENTO_VIGENTE = {
    "id": "c-1",
    "finalidad": "cotizacion-vida-hipotecario",
    "version_texto": "v1",
    "otorgado_en": "2026-10-08T12:00:00+00:00",
    "revocado_en": None,
    "vence_en": "2026-11-07T12:00:00+00:00",
    "vigente": True,
    "estado": "VIGENTE",
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
    "perfilRiesgo": {
        "score": 82,
        "nivelRiesgo": "BAJO",
        "fechaVigencia": "2026-11-17T12:00:00+00:00",
    },
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
        "idioma": "ES",
        "moneda": "COP",
        "tiene_contacto": False,
    }


def _con_nombre(usuario: dict, nombres: dict[str, str]) -> dict:
    """Como AUTH: el nombre propio solo viaja en el login y en `GET /usuarios/{id}`."""
    return {**usuario, "nombre": nombres.get(usuario["id"])}


class AuthFalso:
    def __init__(self, consentimientos: dict | None = None, falla: bool = False) -> None:
        self.consentimientos = dict(
            {"c-1": CONSENTIMIENTO_VIGENTE} if consentimientos is None else consentimientos
        )
        self.falla = falla
        self.actores: list[dict | None] = []
        self.registros_consentimiento: list[dict] = []
        # Nombres de CLIENTE por id y las consultas hechas (para probar el lote).
        self.nombres: dict[str, str] = {}
        self.consultas_de_nombres: list[list[str]] = []
        # Contacto (correo, teléfono) que AUTH recibió por usuario, para comprobar qué le llegó.
        self.contactos: dict[str, dict] = {}
        # Usuarios conocidos por id; `claves` guarda alias -> clave (solo en el doble).
        self.usuarios: dict[str, dict] = {
            "u-1": _usuario_publico("u-1", "asesor.uno", "ASESOR"),
            "u-bo": _usuario_publico("u-bo", "admin.solventa", "BACKOFFICE"),
        }
        self.claves = {"asesor.uno": "clave-segura-1", "admin.solventa": "clave-segura-1"}

    async def registrar_consentimiento(
        self,
        finalidad: str,
        version_texto: str,
        actor: dict | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
    ) -> dict:
        if self.falla:
            raise ErrorServicio("caido")
        self.actores.append(actor)
        nuevo = {
            **CONSENTIMIENTO_VIGENTE,
            "id": "c-nuevo",
            "finalidad": finalidad,
            "version_texto": version_texto,
            "titular_id": titular_id,
            "tipo_dato": tipo_dato,
            "fuente_datos": fuente_datos,
            "canal": canal,
        }
        self.registros_consentimiento.append(nuevo)
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
        revocado = {
            **actual,
            "revocado_en": "2026-10-08T13:00:00+00:00",
            "vigente": False,
            "estado": "REVOCADO",
        }
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
            "usuario": _con_nombre(datos, self.nombres),
        }

    async def obtener_usuario(self, usuario_id: str) -> dict | None:
        datos = self.usuarios.get(usuario_id)
        return _con_nombre(datos, self.nombres) if datos else None

    async def listar_usuarios(self, rol: str | None) -> list[dict]:
        return [u for u in self.usuarios.values() if rol is None or u["rol"] == rol]

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]:
        if self.falla:
            raise ErrorServicio("caido")
        self.consultas_de_nombres.append(list(ids))
        return {i: self.nombres[i] for i in ids if i in self.nombres}

    async def crear_usuario(
        self,
        usuario: str,
        clave: str,
        rol: str,
        nombre: str | None = None,
        correo: str | None = None,
        telefono: str | None = None,
    ) -> dict:
        if "@" in usuario:
            raise SolicitudInvalida("El usuario no puede ser un correo ni un número de documento.")
        if any(u["usuario"] == usuario for u in self.usuarios.values()):
            raise SolicitudInvalida("Ya existe un usuario con ese alias.")
        self._validar_contacto(rol, correo, telefono)
        nuevo = _usuario_publico(str(uuid.uuid4()), usuario, rol)
        if correo is not None or telefono is not None:
            self.contactos[nuevo["id"]] = {"correo": correo, "telefono": telefono}
            nuevo["tiene_contacto"] = True
        self.usuarios[nuevo["id"]] = nuevo
        self.claves[usuario] = clave
        if nombre is not None:
            self.nombres[nuevo["id"]] = nombre
        return nuevo

    @staticmethod
    def _validar_contacto(rol: str, correo: str | None, telefono: str | None) -> None:
        """Las mismas reglas visibles de AUTH (mensajes incluidos)."""
        if (correo is not None or telefono is not None) and rol != "CLIENTE":
            raise SolicitudInvalida("El contacto solo se guarda para clientes.")
        if correo is not None and "@" not in correo:
            raise SolicitudInvalida("El correo no es válido.")
        if telefono is not None and not telefono.lstrip("+").isdigit():
            raise SolicitudInvalida(
                "El teléfono no es válido: usa entre 7 y 15 dígitos, con un + opcional al inicio."
            )

    async def actualizar_contacto(
        self, usuario_id: str, correo: str | None, telefono: str | None
    ) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        if correo is None and telefono is None:
            raise SolicitudInvalida("Indica un correo o un teléfono.")
        if usuario_id not in self.usuarios:
            return None
        self._validar_contacto(self.usuarios[usuario_id]["rol"], correo, telefono)
        actual = self.contactos.get(usuario_id, {"correo": None, "telefono": None})
        self.contactos[usuario_id] = {
            "correo": correo if correo is not None else actual["correo"],
            "telefono": telefono if telefono is not None else actual["telefono"],
        }
        self.usuarios[usuario_id] = {**self.usuarios[usuario_id], "tiene_contacto": True}
        return self.usuarios[usuario_id]

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

    async def actualizar_preferencias(
        self, usuario_id: str, idioma: str, moneda: str
    ) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        if idioma not in ("ES", "EN"):
            raise SolicitudInvalida("Idioma no válido. Usa uno de: ES, EN.")
        if usuario_id not in self.usuarios:
            return None
        self.usuarios[usuario_id] = {
            **self.usuarios[usuario_id],
            "idioma": idioma,
            "moneda": moneda,
        }
        return self.usuarios[usuario_id]


class AclFalso:
    def __init__(self, fallan: set[str] | None = None) -> None:
        self.fallan = fallan or set()
        self.llamadas: list[tuple[str, str, str]] = []
        self.invalidaciones: list[str] = []

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

    async def invalidar_cache_cliente(self, cliente_id: str) -> None:
        self.invalidaciones.append(cliente_id)

    async def tasas_cambio(self) -> dict[str, str]:
        if "tasas" in self.fallan:
            raise ErrorServicio("timeout")
        return {"COP": "1", "USD": "4000", "EUR": "4400", "CLP": "4"}


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
    def __init__(
        self,
        oferta: dict | None = None,
        error: Exception | None = None,
        guardadas: dict[str, dict] | None = None,
        error_lectura: Exception | None = None,
    ) -> None:
        self.oferta = oferta or OFERTA_COMPLETA
        self.error = error
        self.llamadas: list[tuple[str, str, str, int, str]] = []
        self.consentimientos_enviados: list[str | None] = []
        # Lo que RATING guardó para aceptar (`GET /ofertas/{id}`), por id de oferta.
        self.guardadas = guardadas or {}
        self.error_lectura = error_lectura

    async def generar_oferta(
        self,
        cliente_id: str,
        operation_time: str,
        cobertura: str,
        plazo_meses: int,
        direccion_inmueble: str,
        consentimiento_id: str | None = None,
    ) -> dict:
        self.llamadas.append(
            (cliente_id, operation_time, cobertura, plazo_meses, direccion_inmueble)
        )
        self.consentimientos_enviados.append(consentimiento_id)
        if self.error is not None:
            raise self.error
        return self.oferta

    async def obtener_oferta(self, oferta_id: str) -> dict | None:
        if self.error_lectura is not None:
            raise self.error_lectura
        return self.guardadas.get(oferta_id)


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

    async def listar_cobros(
        self,
        poliza_id: str | None,
        estado: str | None,
        pagina: int | None = None,
        tamano: int | None = None,
    ) -> list[dict]:
        if self.falla:
            raise ErrorServicio("caido")
        self.filtros.append((poliza_id, estado))
        resultado = [
            c
            for c in self.cobros.values()
            if (poliza_id is None or c["poliza_id"] == poliza_id)
            and (estado is None or c["estado"] == estado)
        ]
        if pagina is not None and tamano is not None and pagina > 0 and tamano > 0:
            inicio = (pagina - 1) * tamano
            return resultado[inicio : inicio + tamano]
        return resultado

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


class PolicyFalso:
    """Doble de POLICY: acepta con el precio que RATING guardó, como el real."""

    def __init__(
        self,
        rechazo: str | None = None,
        error: Exception | None = None,
        rechazo_firma: str | None = None,
        error_firma: Exception | None = None,
    ) -> None:
        self.rechazo = rechazo
        self.error = error
        self.rechazo_firma = rechazo_firma
        self.error_firma = error_firma
        self.propuestas: dict[str, dict] = {}
        self.llamadas: list[tuple[str, str | None]] = []
        self.firmas: dict[str, dict] = {}
        self.firmas_pedidas: list[tuple[str, bool, str, str]] = []
        self.polizas: dict[str, dict] = {}
        self.documentos_pedidos: list[tuple[str, str | None]] = []
        self.error_documento: Exception | None = None
        self.reconciliaciones: list[str] = []
        self.error_reconciliar: Exception | None = None
        self.propuestas_conocidas: set[str] | None = None  # None = conoce cualquier id

    async def reconciliar_cobro(self, propuesta_id: str) -> str | None:
        self.reconciliaciones.append(propuesta_id)
        if self.error_reconciliar is not None:
            raise self.error_reconciliar
        if self.propuestas_conocidas is not None and propuesta_id not in self.propuestas_conocidas:
            return None  # un cobro que no es de W03
        return "EMITIDA"

    async def crear_propuesta(self, oferta_id: str, actor_usuario_id: str | None) -> dict:
        self.llamadas.append((oferta_id, actor_usuario_id))
        if self.error is not None:
            raise self.error
        if self.rechazo is not None:
            raise PropuestaRechazada(self.rechazo)
        propuesta = self.propuestas.setdefault(
            oferta_id,
            {
                "id": f"propuesta-{oferta_id}",
                "oferta_id": oferta_id,
                "cliente_id": "cliente-opaco",
                "actor_usuario_id": actor_usuario_id,
                "estado": "ACEPTADA",
                "prima": {"valor": "185000", "moneda": "COP"},
                "cobertura": {"valor": "200000000", "moneda": "COP"},
                "plazo_meses": 60,
                "poliza_id": None,
                "actualizado_en": "2026-10-22T12:00:00+00:00",
            },
        )
        return propuesta

    async def obtener_propuesta(self, propuesta_id: str) -> dict | None:
        if self.error is not None:
            raise self.error
        return next((p for p in self.propuestas.values() if p["id"] == propuesta_id), None)

    async def obtener_poliza(self, poliza_id: str) -> dict | None:
        if self.error is not None:
            raise self.error
        return self.polizas.get(poliza_id)

    async def generar_documento(self, poliza_id: str, asegurado: str | None) -> bytes | None:
        self.documentos_pedidos.append((poliza_id, asegurado))
        if self.error_documento is not None:
            raise self.error_documento
        return b"%PDF-1.3 documento falso" if poliza_id in self.polizas else None

    async def documento_firma(self, propuesta_id: str) -> dict | None:
        if self.error is not None:
            raise self.error
        if await self.obtener_propuesta(propuesta_id) is None:
            return None
        return {
            "propuesta_id": propuesta_id,
            "version": "v1",
            "resumen": "Seguro de vida: prima de 185000 COP al mes durante 60 meses.",
            "monto_mensual": {"valor": "185000", "moneda": "COP"},
            "meses": 60,
            "forma_cobro": "con la cuota de tu crédito",
        }

    async def firmar_propuesta(
        self, propuesta_id: str, autoriza_cobro: bool, version_documento: str, firmante_id: str
    ) -> dict:
        self.firmas_pedidas.append((propuesta_id, autoriza_cobro, version_documento, firmante_id))
        if self.error_firma is not None:
            raise self.error_firma
        if self.rechazo_firma is not None:
            raise FirmaRechazada(self.rechazo_firma)
        return self.firmas.setdefault(
            propuesta_id,
            {
                "propuesta_id": propuesta_id,
                "firmada_en": "2026-10-22T12:00:00+00:00",
                "referencia": f"fe-{propuesta_id}",
                "estado": "EN_EMISION",
            },
        )


def clientes_falsos(
    auth: AuthFalso | None = None,
    acl: AclFalso | None = None,
    risk: RiskFalso | None = None,
    rating: RatingFalso | None = None,
    payments: PaymentsFalso | None = None,
    policy: PolicyFalso | None = None,
) -> Clientes:
    return Clientes(
        auth=auth or AuthFalso(),
        acl=acl or AclFalso(),
        risk=risk or RiskFalso(),
        rating=rating or RatingFalso(),
        payments=payments or PaymentsFalso(),
        policy=policy or PolicyFalso(),
    )
