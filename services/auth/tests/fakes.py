"""Dobles de prueba de AUTH: en memoria, sin levantar PostgreSQL."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher

from app.cifrado import CifradorAesGcm
from app.consentimientos import VIGENCIA_DIAS_POR_DEFECTO, Consentimiento

# Llave fija de pruebas (32 bytes); no es la local ni una real.
CIFRADOR_PRUEBAS = CifradorAesGcm(b"0123456789abcdef0123456789abcdef")

# Clave del HMAC del titular en las pruebas (en compose y Cloud Run, `CLIENTE_ID_CLAVE`).
CLAVE_TITULAR_PRUEBAS = "clave-titular-de-pruebas"


def documento_de(alias: str) -> str:
    """Un documento de 10 dígitos distinto por alias: un CLIENTE necesita el suyo (W03-27)."""
    return str(int(hashlib.sha256(alias.encode()).hexdigest(), 16))[:10]


def extras_cliente(alias: str) -> dict[str, str]:
    """Lo que un CLIENTE necesita además del alias, la clave y el rol (W03-27)."""
    return {"correo": f"{alias}@ejemplo.co", "documento": documento_de(alias)}


def extras_cliente_http(alias: str) -> dict[str, str]:
    """Lo mismo, con los nombres de `POST /usuarios`."""
    return {"correo": f"{alias}@ejemplo.co", "documento_identidad": documento_de(alias)}


# argon2 con costo mínimo: mismas reglas, pero las pruebas no tardan segundos.
HASHER_RAPIDO = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class RepositorioUsuariosMemoria:
    """Cumple el `Protocol` `RepositorioUsuarios` de `app/usuarios.py`."""

    def __init__(self) -> None:
        self.filas: dict[str, dict] = {}

    def crear(
        self,
        usuario_id: str,
        alias: str,
        hash_clave: str,
        rol: str,
        nombre_cifrado: str | None = None,
        correo_cifrado: str | None = None,
        telefono_cifrado: str | None = None,
        titular_id: str | None = None,
        documento_cifrado: str | None = None,
    ) -> dict | None:
        if any(f["usuario"] == alias for f in self.filas.values()):
            return None
        fila = {
            "id": usuario_id,
            "usuario": alias,
            "hash_clave": hash_clave,
            "rol": rol,
            "estado": "ACTIVO",
            "intentos_fallidos": 0,
            "bloqueado_hasta": None,
            "creado_en": datetime.now(UTC),
            "nombre_cifrado": nombre_cifrado,
            "correo_cifrado": correo_cifrado,
            "telefono_cifrado": telefono_cifrado,
            "titular_id": titular_id,
            "documento_cifrado": documento_cifrado,
            "correo_hmac": None,
            "idioma": "ES",
            "moneda": "COP",
        }
        self.filas[usuario_id] = fila
        return dict(fila)

    def nombres_cifrados_por_titular(self, titulares: list[str]) -> dict[str, tuple[str, str]]:
        return {
            f["titular_id"]: (f["id"], f["nombre_cifrado"])
            for f in self.filas.values()
            if f["titular_id"] in titulares and f["rol"] == "CLIENTE" and f["nombre_cifrado"]
        }

    def nombres_cifrados(self, ids: list[str]) -> dict[str, str]:
        return {
            i: self.filas[i]["nombre_cifrado"]
            for i in ids
            if i in self.filas
            and self.filas[i]["rol"] == "CLIENTE"
            and self.filas[i]["nombre_cifrado"] is not None
        }

    def obtener(self, usuario_id: str) -> dict | None:
        fila = self.filas.get(usuario_id)
        return dict(fila) if fila else None

    def obtener_por_titular(self, titular_id: str) -> dict | None:
        for fila in self.filas.values():
            if fila["titular_id"] == titular_id:
                return dict(fila)
        return None

    def obtener_por_alias(self, alias: str) -> dict | None:
        for fila in self.filas.values():
            if fila["usuario"] == alias:
                return dict(fila)
        return None

    def listar(self, rol: str | None) -> list[dict]:
        return [dict(f) for f in self.filas.values() if rol is None or f["rol"] == rol]

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None:
        if usuario_id not in self.filas:
            return None
        self.filas[usuario_id]["rol"] = rol
        return dict(self.filas[usuario_id])

    def suspender(self, usuario_id: str) -> dict | None:
        if usuario_id not in self.filas:
            return None
        self.filas[usuario_id]["estado"] = "SUSPENDIDO"
        return dict(self.filas[usuario_id])

    def actualizar_preferencias(self, usuario_id: str, idioma: str, moneda: str) -> dict | None:
        if usuario_id not in self.filas:
            return None
        self.filas[usuario_id] |= {"idioma": idioma, "moneda": moneda}
        return dict(self.filas[usuario_id])

    def actualizar_contacto(
        self, usuario_id: str, correo_cifrado: str | None, telefono_cifrado: str | None
    ) -> dict | None:
        if usuario_id not in self.filas:
            return None
        fila = self.filas[usuario_id]
        # Como el COALESCE de Postgres: lo que no llega se conserva.
        fila["correo_cifrado"] = (
            correo_cifrado if correo_cifrado is not None else fila["correo_cifrado"]
        )
        fila["telefono_cifrado"] = (
            telefono_cifrado if telefono_cifrado is not None else fila["telefono_cifrado"]
        )
        return dict(fila)

    def registrar_fallo(self, usuario_id: str, bloquear_hasta: datetime | None) -> int:
        fila = self.filas[usuario_id]
        if bloquear_hasta is None:
            fila["intentos_fallidos"] += 1
        else:
            fila["intentos_fallidos"] = 0
            fila["bloqueado_hasta"] = bloquear_hasta
        return fila["intentos_fallidos"]

    def registrar_exito(self, usuario_id: str) -> None:
        self.filas[usuario_id]["intentos_fallidos"] = 0
        self.filas[usuario_id]["bloqueado_hasta"] = None

    def crear_pendiente(self, usuario_id, alias, hash_clave, correo_cifrado, correo_hmac):
        if any(f.get("correo_hmac") == correo_hmac for f in self.filas.values()):
            return None
        fila = self.crear(usuario_id, alias, hash_clave, "CLIENTE", None, correo_cifrado)
        self.filas[usuario_id].update(estado="PENDIENTE_VERIFICACION", correo_hmac=correo_hmac)
        return dict(self.filas[usuario_id]) if fila else None

    def obtener_por_correo_hmac(self, correo_hmac):
        for fila in self.filas.values():
            if fila.get("correo_hmac") == correo_hmac:
                return dict(fila)
        return None

    def cerrar_verificacion(
        self, usuario_id, estado, nombre_cifrado, titular_id=None, documento_cifrado=None
    ):
        fila = self.filas.get(usuario_id)
        if fila is None or fila["estado"] not in ("PENDIENTE_VERIFICACION", estado):
            return None
        fila["estado"] = estado
        for campo, valor in (
            ("nombre_cifrado", nombre_cifrado),
            ("titular_id", titular_id),
            ("documento_cifrado", documento_cifrado),
        ):
            if valor:
                fila[campo] = valor
        return dict(fila)


class Reloj:
    """Reloj controlable para probar el bloqueo y el vencimiento del token."""

    def __init__(self, ahora: datetime | None = None) -> None:
        # Arranca en la hora real: pyjwt rechaza tokens con `iat` en el futuro.
        self.ahora = ahora or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.ahora


class AlmacenConsentimientosMemoria:
    """Cumple el `Protocol` `AlmacenConsentimientos` de `app/consentimientos.py`. Fue el mock
    de AUTH hasta T-W01-4; ahora solo es un doble de prueba (misma semántica append-only)."""

    def __init__(self) -> None:
        self._por_id: dict[str, Consentimiento] = {}

    def registrar(
        self,
        finalidad: str,
        version_texto: str,
        actor: dict[str, str] | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
        vigencia_dias: int = VIGENCIA_DIAS_POR_DEFECTO,
    ) -> Consentimiento:
        otorgado = datetime.now(UTC)
        vence = otorgado + timedelta(days=vigencia_dias)
        consentimiento = Consentimiento(
            actor=actor,
            id=str(uuid.uuid4()),
            finalidad=finalidad,
            version_texto=version_texto,
            otorgado_en=otorgado.isoformat(),
            vence_en=vence.isoformat(),
            titular_id=titular_id,
            tipo_dato=tipo_dato,
            fuente_datos=fuente_datos,
            canal=canal,
        )
        self._por_id[consentimiento.id] = consentimiento
        return consentimiento

    def obtener(self, consentimiento_id: str) -> Consentimiento | None:
        return self._por_id.get(consentimiento_id)

    def revocar(self, consentimiento_id: str) -> Consentimiento | None:
        consentimiento = self._por_id.get(consentimiento_id)
        if consentimiento is not None and consentimiento.revocado_en is None:
            consentimiento.revocado_en = datetime.now(UTC).isoformat()
        return consentimiento


class RepositorioSociosMemoria:
    """Cumple el `Protocol` `RepositorioSocios` de `app/socios.py` (HU-W07)."""

    def __init__(self) -> None:
        self.socios: dict[str, dict] = {}
        self.credenciales: list[dict] = []
        self.presupuestos: dict[tuple[str, str, str], dict] = {}
        self.consumos: list[dict] = []

    def obtener(self, socio_id):
        socio = self.socios.get(socio_id)
        return dict(socio) if socio else None

    def nombres(self, ids):
        return {i: self.socios[i]["nombre"] for i in ids if i in self.socios}

    def guardar_socio(self, socio_id, nombre, tasa):
        socio = self.socios.setdefault(socio_id, {"id": socio_id, "estado": "ACTIVO"})
        socio.update(nombre=nombre, tasa_comision=tasa)
        return dict(socio)

    def credencial_por_client_id(self, client_id):
        for c in self.credenciales:
            if c["client_id"] == client_id:
                return {**c, "estado_socio": self.socios[c["socio_id"]]["estado"]}
        return None

    def credenciales_vigentes(self, socio_id):
        return [
            dict(c)
            for c in self.credenciales
            if c["socio_id"] == socio_id and c["revocada_en"] is None
        ]

    def crear_credencial(self, socio_id, entorno, client_id, clave_cifrada):
        fila = {
            "id": str(uuid.uuid4()),
            "socio_id": socio_id,
            "entorno": entorno,
            "client_id": client_id,
            "clave_cifrada": clave_cifrada,
            "intentos_fallidos": 0,
            "bloqueado_hasta": None,
            "creada_en": datetime.now(UTC),
            "revocada_en": None,
        }
        self.credenciales.append(fila)
        return dict(fila)

    def rotar_credencial(self, socio_id, entorno, client_id, clave_cifrada):
        for c in self.credenciales:
            if c["socio_id"] == socio_id and c["entorno"] == entorno and c["revocada_en"] is None:
                c["revocada_en"] = datetime.now(UTC)
        return self.crear_credencial(socio_id, entorno, client_id, clave_cifrada)

    def _credencial(self, credencial_id):
        return next(c for c in self.credenciales if c["id"] == credencial_id)

    def registrar_fallo(self, credencial_id, bloquear_hasta):
        c = self._credencial(credencial_id)
        if bloquear_hasta is None:
            c["intentos_fallidos"] += 1
        else:
            c["intentos_fallidos"], c["bloqueado_hasta"] = 0, bloquear_hasta

    def registrar_exito(self, credencial_id):
        c = self._credencial(credencial_id)
        c["intentos_fallidos"], c["bloqueado_hasta"] = 0, None

    def fijar_presupuesto(self, socio_id, entorno, api, asignadas):
        fila = self.presupuestos.setdefault((socio_id, entorno, api), {"consumidas": 0})
        fila["asignadas"] = asignadas

    def presupuesto(self, socio_id, entorno, api):
        fila = self.presupuestos.get((socio_id, entorno, api))
        return dict(fila) if fila else None

    def reservar(self, socio_id, entorno, api):
        fila = self.presupuestos.get((socio_id, entorno, api))
        if fila is None or fila["consumidas"] >= fila["asignadas"]:
            return None
        fila["consumidas"] += 1
        return dict(fila)

    def devolver(self, socio_id, entorno, api):
        fila = self.presupuestos.get((socio_id, entorno, api))
        if fila is None or fila["consumidas"] <= 0:
            return None
        fila["consumidas"] -= 1
        return dict(fila)

    def registrar_consumo(self, socio_id, entorno, api, resultado, latencia_ms):
        self.consumos.append(
            {
                "socio_id": socio_id,
                "entorno": entorno,
                "api": api,
                "resultado": resultado,
                "latencia_ms": latencia_ms,
                "fecha": datetime.now(UTC).date(),
            }
        )

    def _en_rango(self, socio_id, entorno, desde, hasta):
        return [
            c
            for c in self.consumos
            if c["socio_id"] == socio_id
            and c["entorno"] == entorno
            and desde <= c["fecha"] <= hasta
        ]

    def consumo_por_api(self, socio_id, entorno, desde, hasta):
        por_api: dict[str, list[dict]] = {}
        for c in self._en_rango(socio_id, entorno, desde, hasta):
            por_api.setdefault(c["api"], []).append(c)
        return [
            {
                "api": api,
                "llamadas": len(filas),
                "errores": sum(1 for f in filas if f["resultado"] != "OK"),
                "latencia_media_ms": round(sum(f["latencia_ms"] for f in filas) / len(filas)),
            }
            for api, filas in sorted(por_api.items())
        ]

    def consumo_diario(self, socio_id, entorno, desde, hasta):
        por_dia: dict = {}
        for c in self._en_rango(socio_id, entorno, desde, hasta):
            por_dia[c["fecha"]] = por_dia.get(c["fecha"], 0) + 1
        return [{"fecha": d, "llamadas": n} for d, n in sorted(por_dia.items())]
