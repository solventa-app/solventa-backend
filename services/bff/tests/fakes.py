"""Dobles de prueba del BFF: en memoria, sin levantar AUTH, ACL Worker, RISK ni RATING."""

import time
import uuid
from datetime import UTC, datetime

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.clientes import (
    Acceso,
    ArchivoRechazado,
    Clientes,
    CredencialesSocioInvalidas,
    ErrorOnboardingServicio,
    ErrorServicio,
    FirmaRechazada,
    FlujoRechazado,
    PresupuestoAgotado,
    PropuestaRechazada,
    ResolucionRechazada,
    SolicitudInvalida,
    VerificacionPendiente,
)
from app.configuracion import CLAVE_CLIENTE_ID_LOCAL, JWT_SECRETO_LOCAL
from app.identificadores import cliente_id_desde_documento

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
    scopes: list[str] | None = None,
) -> str:
    """JWT firmado como lo firma AUTH (`services/auth/app/sesiones.py`)."""
    ahora = int(time.time())
    return jwt.encode(
        {
            "sub": usuario_id,
            "rol": rol,
            "scopes": scopes or [],
            "iat": ahora,
            "exp": ahora + segundos,
        },
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


def _enmascarar(documento: str) -> str:
    return documento[:2] + "•" * (len(documento) - 6) + documento[-4:]


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
        self.documentos: dict[str, str] = {}  # W03-27: el documento que recibió AUTH
        self.consultas_de_nombres: list[list[str]] = []
        # Contacto (correo, teléfono) que AUTH recibió por usuario, para comprobar qué le llegó.
        self.contactos: dict[str, dict] = {}
        # Usuarios conocidos por id; `claves` guarda alias -> clave (solo en el doble).
        self.usuarios: dict[str, dict] = {
            "u-1": _usuario_publico("u-1", "asesor.uno", "ASESOR"),
            "u-bo": _usuario_publico("u-bo", "admin.solventa", "BACKOFFICE"),
        }
        self.claves = {"asesor.uno": "Clave-segura-1", "admin.solventa": "Clave-segura-1"}
        self.canales: list[str | None] = []
        self.renovaciones: list[tuple[str, str]] = []

    async def correo_registrado(self, correo: str) -> bool:
        if self.falla:
            raise ErrorServicio("caido")
        return correo in self.claves

    async def crear_cuenta_onboarding(self, correo: str, clave: str) -> dict:
        """Como AUTH (HU-M01): el doble usa el correo como alias para poder iniciar sesión."""
        if self.falla:
            raise ErrorServicio("caido")
        if correo in self.claves:
            raise ErrorOnboardingServicio("CORREO_YA_REGISTRADO")
        usuario_id = f"u-onb-{len(self.usuarios)}"
        self.usuarios[usuario_id] = _usuario_publico(
            usuario_id, correo, "CLIENTE", "PENDIENTE_VERIFICACION"
        )
        self.claves[correo] = clave
        return self.usuarios[usuario_id]

    async def renovar_sesion(self, usuario_id: str, expira_en: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        self.renovaciones.append((usuario_id, expira_en))
        datos = self.usuarios.get(usuario_id)
        if datos is None or datos["estado"] == "SUSPENDIDO":
            return None
        return {**self._sesion(datos), "expira_en": expira_en}

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
            "actor": actor,
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

    async def iniciar_sesion(
        self, usuario: str, clave: str, canal: str | None = None, rol: str | None = None
    ) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        self.canales.append(canal)
        datos = next((u for u in self.usuarios.values() if u["usuario"] == usuario), None)
        if datos is None or self.claves.get(usuario) != clave:
            return None
        if rol is not None and rol != datos["rol"]:
            return None
        if datos["estado"] in ("PENDIENTE_VERIFICACION", "RECHAZADA"):
            if canal != "app":
                raise VerificacionPendiente()
        elif datos["estado"] != "ACTIVO":
            return None
        return self._sesion(datos)

    def _sesion(self, datos: dict) -> dict:
        pendiente = datos["estado"] != "ACTIVO"
        return {
            "token": token_de_prueba(
                datos["rol"],
                datos["id"],
                scopes=["verificacion-pendiente"] if pendiente else ["sesion"],
            ),
            "expira_en": "2026-10-14T13:00:00+00:00",
            "usuario": {**_con_nombre(datos, self.nombres), "correo": self._correo(datos["id"])},
        }

    def _correo(self, usuario_id: str) -> str | None:
        """Como AUTH (v0.24): el correo propio, solo en el login y en `GET /usuarios/{id}`."""
        return self.contactos.get(usuario_id, {}).get("correo")

    async def obtener_usuario(self, usuario_id: str) -> dict | None:
        datos = self.usuarios.get(usuario_id)
        if not datos:
            return None
        documento = self.documentos.get(usuario_id)
        return {
            **_con_nombre(datos, self.nombres),
            "correo": self._correo(usuario_id),
            "documento_enmascarado": _enmascarar(documento) if documento else None,
        }

    async def obtener_identidad(self, usuario_id: str) -> dict | None:
        """Como AUTH: el titular y el documento en claro de un CLIENTE verificado."""
        if self.falla:
            raise ErrorServicio("caido")
        documento = self.documentos.get(usuario_id)
        if documento is None or usuario_id not in self.usuarios:
            return None
        return {
            "titular_id": cliente_id_desde_documento(documento, CLAVE_CLIENTE_ID_LOCAL),
            "documento": documento,
        }

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
        documento_identidad: str | None = None,
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
        if documento_identidad is not None:
            self.documentos[nuevo["id"]] = documento_identidad
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
        self.circuitos_estado: dict[str, str] = {
            n: "closed"
            for n in ("open-finance", "open-data", "pasarela", "firma", "kyc", "notificaciones")
        }
        self.fallan = fallan or set()
        self.llamadas: list[tuple[str, str, str]] = []
        self.invalidaciones: list[str] = []
        self.notificaciones: list[dict] = []

    async def circuitos(self) -> dict[str, str]:
        return dict(self.circuitos_estado)

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

    async def enviar_notificacion(
        self,
        id_notificacion: str,
        plantilla: str,
        destinatario_id: str,
        datos: dict[str, str],
        correo: str,
    ) -> dict:
        if "notificaciones" in self.fallan:
            raise ErrorServicio("timeout")
        envio = {
            "id_notificacion": id_notificacion,
            "plantilla": plantilla,
            "destinatario_id": destinatario_id,
            "datos": dict(datos),
            "correo": correo,
        }
        self.notificaciones.append(envio)
        return {"estado": "enviada", "referencia": "ref-1", "motivo": None}


class RiskFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.llamadas: list[tuple[str, list[dict]]] = []
        self.consentimientos: list[tuple[str | None, str | None]] = []
        self.revocados: list[tuple[str, str]] = []

    async def registrar_perfil(
        self,
        cliente_id: str,
        fuentes: list[dict],
        consentimiento_id: str | None = None,
        consentimiento_vence_en: str | None = None,
    ) -> dict:
        self.llamadas.append((cliente_id, fuentes))
        self.consentimientos.append((consentimiento_id, consentimiento_vence_en))
        if self.falla:
            raise ErrorServicio("HTTP 503")
        return {"cliente_id": cliente_id, "perfil_version": 1, "operation_time": "1700000000.1"}

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None:
        if self.falla:
            raise ErrorServicio("HTTP 503")
        self.revocados.append((titular_id, consentimiento_id))


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
        self.contactos_recibidos: list[dict | None] = []
        self.tasas_recibidas: list[str | None] = []
        self.nombres: dict[str, str] = {}
        self.falla_nombres = False
        self.error_reconciliar: Exception | None = None
        self.propuestas_conocidas: set[str] | None = None  # None = conoce cualquier id
        # v0.18: lo que el BFF le pidió (operación, argumentos) y lo que responderá.
        self.pedidos: list[tuple] = []
        self.rechazos: dict[str, FlujoRechazado] = {}  # operación -> rechazo de negocio
        self.caidas: set[str] = set()  # operaciones que dan ErrorServicio (5xx)
        self.respuestas: dict[str, dict] = {}  # operación -> respuesta a medida
        self.enlaces: dict[str, str] = {}  # token -> propuesta_id
        self.revocaciones: list[tuple[str, str]] = []

    # ---------- v0.18 (HU-W03, F-B6) ----------

    def _flujo(self, operacion: str, *argumentos) -> dict | None:
        self.pedidos.append((operacion, *argumentos))
        if operacion in self.caidas:
            raise ErrorServicio("HTTP 503")
        if operacion in self.rechazos:
            raise self.rechazos[operacion]
        return self.respuestas.get(operacion)

    async def listar_propuestas(
        self, cliente_id, actor_usuario_id, estado_visible, pagina, tamano, actor_rol=None
    ):
        canned = self._flujo("listar", cliente_id, actor_usuario_id, estado_visible, pagina, tamano)
        return canned or {"propuestas": [], "total": 0, "pagina": pagina, "tamano": tamano}

    async def ventas_socio(self, socio_id: str, desde: str | None, hasta: str | None) -> dict:
        canned = self._flujo("ventas_socio", socio_id, desde, hasta)
        return canned or {
            "desde": desde or "2025-11-01",
            "hasta": hasta or "2026-10-29",
            "meses": [],
            "totales": {
                "propuestas": 0,
                "emitidas": 0,
                "primas": {"valor": "0", "moneda": "COP"},
                "comision": {"valor": "0", "moneda": "COP"},
            },
        }

    async def enviar_codigo(self, propuesta_id: str, correo: str | None) -> dict:
        canned = self._flujo("enviar_codigo", propuesta_id, correo)
        return canned or {
            "propuesta_id": propuesta_id,
            "correo_enmascarado": "a•••@correo.com",
            "vence_en": "2026-10-28T12:10:00+00:00",
        }

    async def verificar_codigo(self, propuesta_id: str, codigo: str) -> dict:
        canned = self._flujo("verificar_codigo", propuesta_id, codigo)
        return canned or self._propuesta_en(propuesta_id, "IDENTIDAD_VERIFICADA")

    async def emitir(self, propuesta_id: str, rol: str, titular_id: str | None) -> dict:
        canned = self._flujo("emitir", propuesta_id, rol, titular_id)
        if canned:
            return canned
        if rol in ("ASESOR", "SOCIO"):
            return {
                "propuesta": self._propuesta_en(propuesta_id, "EN_FIRMA"),
                "enlace": {
                    "correo_enmascarado": "a•••@correo.com",
                    "vence_en": "2026-10-28T12:30:00+00:00",
                },
            }
        return {"propuesta": self._propuesta_en(propuesta_id, "EN_FIRMA"), "enlace": None}

    async def reenviar_enlace(self, propuesta_id: str) -> dict:
        canned = self._flujo("reenviar_enlace", propuesta_id)
        return canned or {
            "propuesta": self._propuesta_en(propuesta_id, "EN_FIRMA"),
            "enlace": {
                "correo_enmascarado": "a•••@correo.com",
                "vence_en": "2026-10-28T12:30:00+00:00",
            },
        }

    async def resolver_enlace(self, token: str) -> dict | None:
        self._flujo("resolver_enlace", token)
        propuesta_id = self.enlaces.get(token)
        if propuesta_id is None:
            return None
        return {
            "propuesta_id": propuesta_id,
            "vigente": True,
            "vence_en": "2026-10-28T12:30:00+00:00",
        }

    async def estado_enlace(self, propuesta_id: str, acceso: Acceso) -> dict:
        canned = self._flujo("estado_enlace", propuesta_id, acceso)
        return canned or {
            "propuesta": self._propuesta_en(propuesta_id, "EN_FIRMA"),
            "vigente": True,
            "vence_en": "2026-10-28T12:30:00+00:00",
            "firma": None,
        }

    async def contrato(self, propuesta_id: str, acceso: Acceso) -> dict:
        canned = self._flujo("contrato", propuesta_id, acceso)
        return canned or {
            "propuesta_id": propuesta_id,
            "version": "v1",
            "resumen": "Seguro de vida: prima de 185000 COP al mes durante 60 meses.",
            "monto_mensual": {"valor": "185000", "moneda": "COP"},
            "meses": 60,
            "forma_cobro": "con la cuota de tu crédito",
        }

    async def contrato_pdf(self, propuesta_id: str, acceso: Acceso) -> bytes | None:
        self._flujo("contrato_pdf", propuesta_id, acceso)
        return b"%PDF-1.3 contrato falso"

    async def subida_firma(
        self, propuesta_id: str, acceso: Acceso, tipo_contenido: str, tamano_bytes: int
    ) -> dict:
        canned = self._flujo("subida_firma", propuesta_id, acceso, tipo_contenido, tamano_bytes)
        return canned or {
            "archivo": {
                "id": "arch-1",
                "tipo": "firma-imagen",
                "estado": "PENDIENTE",
                "tipo_contenido": tipo_contenido,
                "tamano_bytes": tamano_bytes,
                "creado_en": "2026-10-28T12:00:00+00:00",
            },
            "url": "https://almacen.example/subir/arch-1",
            "metodo": "PUT",
            "encabezados": {"Content-Type": tipo_contenido},
            "vence_en": "2026-10-28T12:15:00+00:00",
        }

    async def firmar_enlace(self, propuesta_id: str, acceso: Acceso, entrada: dict) -> dict:
        canned = self._flujo("firmar_enlace", propuesta_id, acceso, entrada)
        return canned or {
            "propuesta_id": propuesta_id,
            "firmada_en": "2026-10-28T12:05:00+00:00",
            "referencia": "ev-1",
            "estado": "FIRMADA",
        }

    async def iniciar_pago(
        self, propuesta_id: str, acceso: Acceso, url_retorno: str, idioma: str
    ) -> dict:
        canned = self._flujo("iniciar_pago", propuesta_id, acceso, url_retorno, idioma)
        return canned or {
            "sesion": {
                "sesion_id": "s-1",
                "estado": "abierta",
                "url_pago": "https://pasarela.example/checkout/s-1",
                "vence_en": "2026-10-28T12:15:00+00:00",
                "motivo": None,
                "fallos_seguidos": 0,
            },
            "poliza": None,
        }

    async def confirmar_pago(self, propuesta_id: str, acceso: Acceso, sesion_id: str) -> dict:
        canned = self._flujo("confirmar_pago", propuesta_id, acceso, sesion_id)
        return canned or {
            "sesion": {"sesion_id": sesion_id, "estado": "cobrada", "fallos_seguidos": 0},
            "poliza": {
                "id": "pol-1",
                "numero": "SOL-2026-0001",
                "vigencia_desde": "2026-10-28T12:06:00+00:00",
                "vigencia_hasta": "2031-10-28T12:06:00+00:00",
            },
        }

    async def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]:
        if self.falla_nombres:
            raise ErrorServicio("caido")
        return {i: self.nombres[i] for i in propuesta_ids if i in self.nombres}

    async def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> None:
        self._flujo("consentimiento_revocado", titular_id, consentimiento_id)
        self.revocaciones.append((titular_id, consentimiento_id))

    @staticmethod
    def _propuesta_en(propuesta_id: str, estado: str) -> dict:
        return {
            "id": propuesta_id,
            "oferta_id": "o-1",
            "estado": estado,
            "prima": {"valor": "185000", "moneda": "COP"},
            "cobertura": {"valor": "200000000", "moneda": "COP"},
            "plazo_meses": 60,
            "poliza_id": None,
            "actualizado_en": "2026-10-28T12:00:00+00:00",
        }

    async def reconciliar_cobro(self, propuesta_id: str) -> str | None:
        self.reconciliaciones.append(propuesta_id)
        if self.error_reconciliar is not None:
            raise self.error_reconciliar
        if self.propuestas_conocidas is not None and propuesta_id not in self.propuestas_conocidas:
            return None  # un cobro que no es de W03
        return "EMITIDA"

    async def crear_propuesta(
        self,
        oferta_id: str,
        actor_usuario_id: str | None,
        actor_rol: str | None = None,
        contacto: dict | None = None,
        tasa_comision: str | None = None,
    ) -> dict:
        self.llamadas.append((oferta_id, actor_usuario_id))
        self.contactos_recibidos.append(contacto)
        self.tasas_recibidas.append(tasa_comision)
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
                "actor_rol": actor_rol,
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


class AlmacenamientoFalso:
    """ALMACENAMIENTO en memoria: un CLIENTE solo ve lo suyo."""

    def __init__(self) -> None:
        self.archivos: dict[str, dict] = {}
        self.llamadas: list[tuple] = []
        self.rechazo: str | None = None
        self.error: Exception | None = None

    def _revisar(self, *llamada) -> None:
        self.llamadas.append(llamada)
        if self.error is not None:
            raise self.error
        if self.rechazo is not None:
            raise ArchivoRechazado(self.rechazo)

    def _propio(self, rol: str, actor_id: str, archivo_id: str) -> dict:
        archivo = self.archivos.get(archivo_id)
        if archivo is None or (rol == "CLIENTE" and archivo["propietario_id"] != actor_id):
            raise ArchivoRechazado("ARCHIVO_NO_ENCONTRADO")
        return archivo

    async def solicitar_subida(self, rol: str, actor_id: str, solicitud: dict) -> dict:
        self._revisar("subida", rol, actor_id, solicitud)
        archivo_id = f"arc-{len(self.archivos) + 1}"
        archivo = {
            "id": archivo_id,
            "tipo": solicitud["tipo"],
            "estado": "PENDIENTE",
            "propietario_tipo": "usuario",
            "propietario_id": solicitud["propietario_id"],
            "tipo_contenido": solicitud["tipo_contenido"],
            "tamano_bytes": None,
            "motivo": None,
            "creado_en": "2026-10-27T15:00:00+00:00",
        }
        self.archivos[archivo_id] = archivo
        return {
            "archivo": archivo,
            "url": f"https://almacen.prueba/{archivo_id}?firma=subida",
            "metodo": "PUT",
            "encabezados": {"Content-Type": solicitud["tipo_contenido"]},
            "vence_en": "2026-10-27T15:10:00+00:00",
        }

    async def confirmar(self, rol: str, actor_id: str, archivo_id: str) -> dict:
        self._revisar("confirmar", rol, actor_id, archivo_id)
        archivo = self._propio(rol, actor_id, archivo_id)
        archivo.update(estado="DISPONIBLE", tamano_bytes=64)
        return archivo

    async def obtener(self, rol: str, actor_id: str, archivo_id: str) -> dict | None:
        self._revisar("obtener", rol, actor_id, archivo_id)
        try:
            return self._propio(rol, actor_id, archivo_id)
        except ArchivoRechazado:
            return None

    async def descarga(self, rol: str, actor_id: str, archivo_id: str) -> dict:
        self._revisar("descarga", rol, actor_id, archivo_id)
        archivo = self._propio(rol, actor_id, archivo_id)
        if archivo["estado"] != "DISPONIBLE":
            raise ArchivoRechazado("ARCHIVO_NO_DISPONIBLE")
        return {
            "url": f"https://almacen.prueba/{archivo_id}?firma=descarga",
            "vence_en": "2026-10-27T15:05:00+00:00",
        }


class TicketsFalso:
    """Doble del ticket de avisos: guarda lo que se emitió (rol, usuario, titular)."""

    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.emitidos: list[tuple[str, str, str | None]] = []

    async def emitir(self, rol: str, usuario_id: str, titular_id: str | None):
        if self.falla:
            raise ConnectionError("redis caído")
        self.emitidos.append((rol, usuario_id, titular_id))
        return f"ticket-{len(self.emitidos)}", datetime(2026, 10, 28, 12, 1, tzinfo=UTC)


class OnboardingFalso:
    """El servicio ONBOARDING en memoria (HU-M01). Un solo token válido por recorrido."""

    def __init__(self) -> None:
        self.sesiones: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}
        self.falla = False

    def _revisar(self, onboarding_id: str, token: str | None) -> dict:
        if self.falla:
            raise ErrorServicio("caido")
        if self.tokens.get(onboarding_id) != token:
            raise ErrorOnboardingServicio("TOKEN_INVALIDO")
        return self.sesiones[onboarding_id]

    def _vista(self, sesion: dict) -> dict:
        vista = {k: sesion[k] for k in ("id", "estado", "motivo", "intento", "intentos_restantes")}
        return {**vista, "tiene_cuenta": sesion.get("usuario_id") is not None}

    async def iniciar(self, jurisdiccion: str, tipo_documento: str) -> dict:
        if self.falla:
            raise ErrorServicio("caido")
        if jurisdiccion != "CO":
            raise ErrorOnboardingServicio("JURISDICCION_NO_SOPORTADA")
        onboarding_id = f"onb-{len(self.sesiones) + 1}"
        self.sesiones[onboarding_id] = {
            "id": onboarding_id,
            "estado": "INICIADO",
            "motivo": None,
            "intento": 1,
            "intentos_restantes": 2,
            "consentimiento_id": None,
            "usuario_id": None,
            "piezas": {},
        }
        self.tokens[onboarding_id] = f"tok-{onboarding_id}"
        return {
            "onboarding_id": onboarding_id,
            "token": self.tokens[onboarding_id],
            "token_vence_en": "2026-10-28T12:30:00+00:00",
        }

    async def consultar(self, onboarding_id: str, token: str) -> dict:
        return self._vista(self._revisar(onboarding_id, token))

    async def registrar_consentimiento(self, onboarding_id, token, consentimiento_id) -> dict:
        sesion = self._revisar(onboarding_id, token)
        sesion["consentimiento_id"] = consentimiento_id
        return self._vista(sesion)

    async def solicitar_subida(self, onboarding_id, token, paso, tipo_contenido, tamano) -> dict:
        sesion = self._revisar(onboarding_id, token)
        if sesion["consentimiento_id"] is None:
            raise ErrorOnboardingServicio("SIN_CONSENTIMIENTO")
        archivo_id = f"a-{paso.lower()}"
        sesion["piezas"][archivo_id] = "PENDIENTE"
        return {
            "archivo_id": archivo_id,
            "url": f"http://gcs/{archivo_id}",
            "metodo": "PUT",
            "encabezados": {"Content-Type": tipo_contenido},
            "vence_en": "2026-10-28T12:15:00+00:00",
        }

    async def confirmar_subida(self, onboarding_id, token, archivo_id) -> dict:
        sesion = self._revisar(onboarding_id, token)
        if archivo_id not in sesion["piezas"]:
            raise ErrorOnboardingServicio("ARCHIVO_NO_ENCONTRADO")
        sesion["piezas"][archivo_id] = "DISPONIBLE"
        return {"archivo_id": archivo_id, "estado": "DISPONIBLE"}

    async def vincular_cuenta(self, onboarding_id, token, usuario_id) -> dict:
        sesion = self._revisar(onboarding_id, token)
        if sesion["usuario_id"] not in (None, usuario_id):
            raise ErrorOnboardingServicio("SESION_CON_CUENTA")
        sesion["usuario_id"] = usuario_id
        return self._vista(sesion)

    async def enviar(self, onboarding_id, token) -> dict:
        sesion = self._revisar(onboarding_id, token)
        if len([e for e in sesion["piezas"].values() if e == "DISPONIBLE"]) < 4:
            raise ErrorOnboardingServicio("PIEZAS_INCOMPLETAS")
        sesion["estado"] = "EN_VERIFICACION"
        return self._vista(sesion)

    async def reintentar(self, onboarding_id, token) -> dict:
        sesion = self._revisar(onboarding_id, token)
        if sesion["estado"] != "RECHAZADA" or sesion["intentos_restantes"] == 0:
            raise ErrorOnboardingServicio("SIN_INTENTOS")
        sesion.update(estado="INICIADO", motivo=None, piezas={}, intento=sesion["intento"] + 1)
        sesion["intentos_restantes"] -= 1
        return self._vista(sesion)

    async def de_usuario(self, usuario_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        filas = [s for s in self.sesiones.values() if s["usuario_id"] == usuario_id]
        return self._vista(filas[-1]) if filas else None


class ClaimsFalso:
    """CLAIMS en memoria (HU-W04): devuelve expedientes con la forma real y registra las
    llamadas."""

    def __init__(self) -> None:
        self.siniestros: dict[str, dict] = {"sin-01": expediente_de_prueba()}
        self.llamadas: list[tuple] = []
        self.error: Exception | None = None
        self.rechazo: str | None = None

    def _revisar(self, *llamada) -> None:
        self.llamadas.append(llamada)
        if self.error is not None:
            raise self.error
        if self.rechazo is not None:
            raise ResolucionRechazada(self.rechazo)

    async def listar(self, estado: str | None) -> list[dict]:
        self._revisar("listar", estado)
        return [s for s in self.siniestros.values() if estado in (None, s["estado"])]

    async def obtener(self, siniestro_id: str) -> dict | None:
        self._revisar("obtener", siniestro_id)
        return self.siniestros.get(siniestro_id)

    async def peritos(self, siniestro_id: str) -> list[dict]:
        self._revisar("peritos", siniestro_id)
        return [{**PERITO_DE_PRUEBA, "vencimiento_informe": "2026-11-03T17:00:00-05:00"}]

    async def aprobar(self, siniestro_id: str, actor_usuario_id: str) -> dict:
        self._revisar("aprobar", siniestro_id, actor_usuario_id)
        s = self.siniestros[siniestro_id]
        s["estado"] = "APROBADO"
        s["resolucion"] = {
            "tipo": "APROBACION",
            "resuelta_en": "2026-10-28T15:00:00+00:00",
            "notificacion": "Enviada al asegurado y a la entidad acreedora",
            "indemnizacion": {"valor": "8650000", "moneda": "COP"},
            "metodo_transferencia": "ACH Colombia Interbancario",
            "plazo_dispersion": "1 a 3 días hábiles",
            "perito": None,
        }
        return s

    async def rechazar(self, siniestro_id, motivo, comentario, actor_usuario_id) -> dict:
        self._revisar("rechazar", siniestro_id, motivo, comentario, actor_usuario_id)
        s = self.siniestros[siniestro_id]
        s["estado"] = "RECHAZADO"
        s["resolucion"] = {
            "tipo": "RECHAZO",
            "resuelta_en": "2026-10-28T15:00:00+00:00",
            "notificacion": "Enviada vía correo certificado y Open Finance",
            "motivo": motivo,
            "comentario": comentario,
            "via_recurso": "Disponible en los próximos 10 días hábiles",
        }
        return s

    async def derivar(self, siniestro_id, perito_id, instrucciones, actor_usuario_id) -> dict:
        self._revisar("derivar", siniestro_id, perito_id, instrucciones, actor_usuario_id)
        s = self.siniestros[siniestro_id]
        s["estado"] = "EN_REVISION"
        s["resolucion"] = {
            "tipo": "DERIVACION",
            "resuelta_en": "2026-10-28T15:00:00+00:00",
            "notificacion": "Correo certificado y expediente digital de siniestros",
            "perito": PERITO_DE_PRUEBA,
            "instrucciones": instrucciones,
            "vencimiento_informe": "2026-11-03T17:00:00-05:00",
        }
        return s


PERITO_DE_PRUEBA = {
    "id": "per-01",
    "nombre": "Peritajes Andes Ltda.",
    "ciudad": "Bogotá",
    "red": "Red Preferente Centro",
    "registro": "RNPL-SFC 2024-0117",
    "tiempo_respuesta_dias": 4,
}


def expediente_de_prueba(sid: str = "sin-01", poliza_id: str = "pol-01") -> dict:
    return {
        "id": sid,
        "radicado": "SIN-2026-0141",
        "poliza_id": poliza_id,
        "estado": "PENDIENTE",
        "asegurado": {"nombre": "Camilo Rojas", "documento": "C.C. 79.845.210"},
        "cobertura_aplicada": "Vida grupo deudor",
        "entidad_acreedora": "Banco Aliado S.A.",
        "ocurrido_en": "2026-10-19T13:40:00+00:00",
        "notificado_en": "2026-10-19T16:15:00+00:00",
        "ubicacion": {
            "descripcion": "Calle 80",
            "ciudad": "Bogotá",
            "latitud": 4.72,
            "longitud": -74.12,
        },
        "descripcion": "Choque leve",
        "suma_reclamada": {"valor": "8650000", "moneda": "COP"},
        "vence_dictamen_en": "2026-10-29T15:00:00+00:00",
        "evidencias": [
            {
                "id": "ev-01-1",
                "archivo_id": "arch-1",
                "tipo": "FOTO",
                "titulo": "Daño frontal",
                "archivo": "sin-01-foto-1.jpg",
                "registrada_en": "2026-10-19T14:05:00+00:00",
                "geoetiquetada": True,
                "latitud": 4.72,
                "longitud": -74.12,
                "nota": "4.72, -74.12 · tomada en sitio",
            }
        ],
        "verificaciones": [{"descripcion": "Póliza vigente", "cumple": True}],
        "resolucion": None,
    }


class SociosFalso:
    """Doble de los endpoints de socios de AUTH (HU-W07). Firma tokens ES256 **reales** con una
    clave generada para la prueba, para que el BFF los verifique igual que en producción."""

    EMISOR = "solventa-auth"
    AUDIENCIA = "solventa-socios"

    def __init__(self, presupuesto: int = 2, falla: bool = False) -> None:
        self._clave = ec.generate_private_key(ec.SECP256R1())
        publica = jwt.algorithms.ECAlgorithm.to_jwk(self._clave.public_key(), as_dict=True)
        self._jwk = {**publica, "kid": "kid-prueba", "alg": "ES256", "use": "sig"}
        self.falla = falla
        self.socios = {
            "socio-1": {"id": "socio-1", "nombre": "Banco Demo", "estado": "ACTIVO",
                        "tasa_comision": "0.1000"},
            "socio-2": {"id": "socio-2", "nombre": "Fintech Demo", "estado": "ACTIVO",
                        "tasa_comision": "0.0800"},
        }  # fmt: skip
        self.claves = {
            "sol_prod_1": ("socio-1", "PRODUCCION", "Clave-prod-1"),
            "sol_test_1": ("socio-1", "PRUEBAS", "clave-test-1"),
            "sol_prod_2": ("socio-2", "PRODUCCION", "Clave-prod-2"),
        }
        self.presupuesto = {
            ("socio-1", "PRODUCCION"): presupuesto,
            ("socio-1", "PRUEBAS"): 50,
            ("socio-2", "PRODUCCION"): 1,
        }
        self.consumidas: dict[tuple[str, str], int] = {}
        self.devoluciones: list[tuple[str, str]] = []
        self.consumos: list[tuple[str, str, str, str]] = []
        self.pedidos_jwks = 0

    def _caer(self) -> None:
        if self.falla:
            raise ErrorServicio("caido")

    def token(self, socio_id: str = "socio-1", entorno: str = "PRODUCCION", **cambios) -> str:
        ahora = int(datetime.now(UTC).timestamp())
        claims = {
            "iss": self.EMISOR, "aud": self.AUDIENCIA, "sub": socio_id, "rol": "SOCIO",
            "entorno": entorno, "iat": ahora, "exp": ahora + 900, **cambios,
        }  # fmt: skip
        return jwt.encode(claims, self._clave, algorithm="ES256", headers={"kid": "kid-prueba"})

    async def emitir_token(self, client_id: str, clave: str) -> dict:
        self._caer()
        credencial = self.claves.get(client_id)
        if credencial is None or credencial[2] != clave:
            raise CredencialesSocioInvalidas()
        socio_id, entorno, _ = credencial
        return {
            "token": self.token(socio_id, entorno),
            "expira_en": "2026-10-29T12:15:00+00:00",
            "entorno": entorno,
            "socio_id": socio_id,
        }

    async def jwks(self) -> dict:
        self._caer()
        self.pedidos_jwks += 1
        return {"keys": [self._jwk]}

    async def obtener(self, socio_id: str) -> dict | None:
        self._caer()
        return self.socios.get(socio_id)

    async def nombres(self, ids: list[str]) -> dict[str, str]:
        self._caer()
        return {i: self.socios[i]["nombre"] for i in ids if i in self.socios}

    async def credenciales(self, socio_id: str) -> list[dict]:
        self._caer()
        return [
            {"entorno": e, "client_id": c, "clave": k, "creada_en": "2026-10-29T00:00:00+00:00"}
            for c, (s, e, k) in self.claves.items()
            if s == socio_id
        ]

    async def rotar(self, socio_id: str, entorno: str) -> dict:
        self._caer()
        for client_id, (s, e, _) in list(self.claves.items()):
            if s == socio_id and e == entorno:
                del self.claves[client_id]
        nuevo = f"sol_nuevo_{len(self.claves)}"
        self.claves[nuevo] = (socio_id, entorno, "Clave-nueva")
        return {
            "entorno": entorno,
            "client_id": nuevo,
            "clave": "Clave-nueva",
            "creada_en": "2026-10-29T00:00:00+00:00",
        }

    async def reservar(self, socio_id: str, entorno: str) -> int:
        self._caer()
        clave = (socio_id, entorno)
        usadas = self.consumidas.get(clave, 0)
        if usadas >= self.presupuesto.get(clave, 0):
            raise PresupuestoAgotado()
        self.consumidas[clave] = usadas + 1
        return self.presupuesto[clave] - usadas - 1

    async def devolver(self, socio_id: str, entorno: str) -> int:
        self._caer()
        clave = (socio_id, entorno)
        self.devoluciones.append(clave)
        self.consumidas[clave] = max(0, self.consumidas.get(clave, 0) - 1)
        return self.presupuesto.get(clave, 0) - self.consumidas[clave]

    async def registrar_consumo(self, socio_id, entorno, api, resultado, latencia_ms) -> None:
        self._caer()
        self.consumos.append((socio_id, entorno, api, resultado))

    async def consumo(self, socio_id, entorno, desde, hasta) -> dict:
        self._caer()
        asignadas = self.presupuesto.get((socio_id, entorno), 0)
        consumidas = self.consumidas.get((socio_id, entorno), 0)
        return {
            "entorno": entorno,
            "desde": desde or "2026-09-30",
            "hasta": hasta or "2026-10-29",
            "presupuesto": {
                "asignadas": asignadas,
                "consumidas": consumidas,
                "restantes": asignadas - consumidas,
            },
            "por_api": [
                {"api": "cotizaciones", "llamadas": 2, "errores": 0, "latencia_media_ms": 120}
            ],
            "diario": [{"fecha": "2026-10-29", "llamadas": 2}],
        }


def clientes_falsos(
    auth: AuthFalso | None = None,
    acl: AclFalso | None = None,
    risk: RiskFalso | None = None,
    rating: RatingFalso | None = None,
    payments: PaymentsFalso | None = None,
    policy: PolicyFalso | None = None,
    almacenamiento: AlmacenamientoFalso | None = None,
    tickets: TicketsFalso | None = None,
    onboarding: OnboardingFalso | None = None,
    claims: ClaimsFalso | None = None,
    socios: SociosFalso | None = None,
) -> Clientes:
    return Clientes(
        auth=auth or AuthFalso(),
        acl=acl or AclFalso(),
        risk=risk or RiskFalso(),
        rating=rating or RatingFalso(),
        payments=payments or PaymentsFalso(),
        policy=policy or PolicyFalso(),
        almacenamiento=almacenamiento or AlmacenamientoFalso(),
        tickets=tickets or TicketsFalso(),
        onboarding=onboarding or OnboardingFalso(),
        claims=claims or ClaimsFalso(),
        socios=socios or SociosFalso(),
    )
