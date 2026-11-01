"""Usuarios de Solventa (HU-M02): alta, consulta, cambio de rol y suspensión.

AUTH es el **único escritor** de la tabla `usuarios` (regla de arquitectura #1). El
esquema lo crea la migración `0002_usuarios`, nunca la app al arrancar (ADR-07).

Capa simple, sin puertos y adaptadores formales (regla #3): el `Protocol` existe solo
para probar con dobles sin levantar PostgreSQL.

Datos personales: el alias de acceso **no puede ser un correo ni un documento**
(`validar_alias`). Los datos personales que guarda la tabla van **cifrados por campo**
(`app/cifrado.py`, SEG-002): nunca en claro en la base ni en los logs.

- El **nombre** (KAN-28), de cualquier rol. Solo sale por dos caminos: `nombre_de` (el propio
  usuario, al iniciar sesión o consultar `yo`) y `nombres` (los CLIENTE de una lista, para el
  BACKOFFICE).
- El **documento de identidad** (HU-W03, W03-27), solo de los CLIENTE y obligatorio para ellos junto
  con el correo. Se guarda cifrado y, aparte, como `titular_id` (el HMAC del consentimiento): el
  BACKOFFICE que crea al CLIENTE da por verificada su identidad (W03-36). Sale enmascarado al propio
  usuario (`documento_enmascarado_de`) y en claro solo hacia el BFF (`identidad_de`), que lo usa en
  memoria para que W01 no se lo pida (W03-34).
- El **correo y el teléfono** (HU-W03, bloque 8), solo de los CLIENTE. Solo salen por
  `contacto_de`, que lo llama POLICY (servicio a servicio) para enviar el PDF de la póliza; nunca
  vuelven al cliente por GraphQL. Cada campo se cifra con su propio contexto, así que un cifrado
  movido de una columna a otra no se descifra.

La clave se guarda solo como hash argon2id y nunca sale de este servicio."""

import hashlib
import hmac
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Literal, Protocol

from argon2 import PasswordHasher
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.cifrado import CifradorCampos, ErrorDeCifrado
from app.identificadores import cliente_id_desde_documento, id_opaco

logger = logging.getLogger("auth.usuarios")

Rol = Literal["CLIENTE", "ASESOR", "BACKOFFICE"]
ROLES: tuple[str, ...] = ("CLIENTE", "ASESOR", "BACKOFFICE")

ALIAS_VALIDO = re.compile(r"^[a-z0-9._-]{3,32}$")
# Seis o más dígitos seguidos: parece un número de documento.
PARECE_DOCUMENTO = re.compile(r"\d{6,}")
CLAVE_LONGITUD_MINIMA = 8
CLAVE_LONGITUD_MAXIMA = 128
NOMBRE_LONGITUD_MINIMA = 2
NOMBRE_LONGITUD_MAXIMA = 100
MAXIMO_IDS_POR_CONSULTA = 200
# Contacto del cliente (bloque 8 de HU-W03): correo con forma básica (no se verifica que exista) y
# teléfono con 7 a 15 dígitos (el máximo de E.164) y un "+" opcional al inicio.
CORREO_LONGITUD_MAXIMA = 254
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TELEFONO_VALIDO = re.compile(r"^\+?\d{7,15}$")
# Documento de identidad (W03-27): cédula, cédula de extranjería o pasaporte, sin separadores.
DOCUMENTO_VALIDO = re.compile(r"^[0-9A-Z]{5,20}$")
SEPARADORES_DE_TELEFONO = " -().\t"
# Preferencias de la interfaz (KAN-209): las mismas que ofrecen los selectores del header de la web.
IDIOMAS: tuple[str, ...] = ("ES", "EN")
MONEDAS: tuple[str, ...] = ("COP", "MXN", "BRL", "CLP", "PEN", "USD", "EUR")

# Columnas que pueden salir del servicio: nunca el hash ni el estado del bloqueo.
_PUBLICAS = "id, usuario, rol, estado, creado_en, idioma, moneda"
_TODAS = (
    f"{_PUBLICAS}, hash_clave, intentos_fallidos, bloqueado_hasta, nombre_cifrado, "
    "correo_cifrado, telefono_cifrado, titular_id, documento_cifrado, correo_hmac"
)
# Estados de una cuenta creada en el onboarding del móvil (HU-M01, migración `0009`).
PENDIENTE_VERIFICACION = "PENDIENTE_VERIFICACION"
RECHAZADA = "RECHAZADA"


class DatoInvalido(Exception):
    """La solicitud no cumple las reglas (alias, clave o rol). Mensaje apto para el cliente."""


class UsuarioDuplicado(Exception):
    """Ya existe un usuario con ese alias, o un CLIENTE con ese documento."""


MENSAJE_DOCUMENTO_DUPLICADO = "Ya existe un cliente con ese documento."


def validar_alias(alias: str) -> None:
    # Primero la regla de datos personales: quien escribe un correo debe leer que no
    # puede usar un correo, no un mensaje genérico de formato.
    if "@" in alias or PARECE_DOCUMENTO.search(alias):
        raise DatoInvalido("El usuario no puede ser un correo ni un número de documento.")
    if not ALIAS_VALIDO.match(alias):
        raise DatoInvalido(
            "El usuario debe tener entre 3 y 32 caracteres: minúsculas, números, punto, "
            "guion o guion bajo."
        )


def faltantes_de_clave(clave: str) -> list[str]:
    """Requisitos de composición que no cumple la clave (T17). Global y con Unicode: mayúscula =
    categoría `Lu` (Ñ, Á, Ç, Ł...), minúscula = `Ll` (ñ, é, ß...), especial = cualquier carácter
    que no sea letra ni número (`¿`, `€`, `_`, espacio, emoji...). Las letras sin caso (chino,
    árabe, hindi) cuentan como letras pero no como mayúscula ni minúscula."""
    categorias = [unicodedata.category(c) for c in clave]
    faltan = []
    if "Lu" not in categorias:
        faltan.append("una mayúscula")
    if "Ll" not in categorias:
        faltan.append("una minúscula")
    if not any(c[0] not in "LN" for c in categorias):
        faltan.append("un carácter especial")
    return faltan


def mensaje_clave_debil(faltan: list[str]) -> str:
    lista = faltan[0] if len(faltan) == 1 else ", ".join(faltan[:-1]) + " y " + faltan[-1]
    return f"La clave no es segura: falta {lista}."


def validar_clave(clave: str) -> None:
    """Solo en altas (onboarding y `crearUsuario`); el login no la exige."""
    if not CLAVE_LONGITUD_MINIMA <= len(clave) <= CLAVE_LONGITUD_MAXIMA:
        raise DatoInvalido(
            f"La clave debe tener entre {CLAVE_LONGITUD_MINIMA} y {CLAVE_LONGITUD_MAXIMA} "
            "caracteres."
        )
    faltan = faltantes_de_clave(clave)
    if faltan:
        raise DatoInvalido(mensaje_clave_debil(faltan))


def validar_rol(rol: str) -> None:
    if rol not in ROLES:
        raise DatoInvalido(f"Rol desconocido: {rol}.")


def normalizar_nombre(nombre: str) -> str:
    """Devuelve el nombre sin espacios sobrantes. Cualquier rol puede tener nombre."""
    limpio = " ".join(nombre.split())
    if not NOMBRE_LONGITUD_MINIMA <= len(limpio) <= NOMBRE_LONGITUD_MAXIMA:
        raise DatoInvalido(
            f"El nombre debe tener entre {NOMBRE_LONGITUD_MINIMA} y {NOMBRE_LONGITUD_MAXIMA} "
            "caracteres."
        )
    if any(unicodedata.category(c).startswith("C") for c in limpio):
        raise DatoInvalido("El nombre tiene caracteres no permitidos.")
    return limpio


def normalizar_correo(correo: str) -> str:
    """El correo sin espacios y en minúsculas. Solo revisa la forma: no verifica que exista."""
    limpio = correo.strip().lower()
    if (
        len(limpio) > CORREO_LONGITUD_MAXIMA
        or not CORREO_VALIDO.match(limpio)
        or any(unicodedata.category(c).startswith("C") for c in limpio)
    ):
        raise DatoInvalido("El correo no es válido.")
    return limpio


def normalizar_telefono(telefono: str) -> str:
    """El teléfono solo con dígitos y un "+" opcional al inicio (sin espacios, guiones ni
    paréntesis)."""
    limpio = "".join(c for c in telefono.strip() if c not in SEPARADORES_DE_TELEFONO)
    if not TELEFONO_VALIDO.match(limpio):
        raise DatoInvalido(
            "El teléfono no es válido: usa entre 7 y 15 dígitos, con un + opcional al inicio."
        )
    return limpio


def normalizar_documento(documento: str) -> str:
    """El documento sin espacios a los lados y en mayúsculas: así el HMAC coincide con el que
    calcula el BFF para el consentimiento (`strip().upper()`). Sin puntos, guiones ni espacios."""
    limpio = documento.strip().upper()
    if not DOCUMENTO_VALIDO.match(limpio):
        raise DatoInvalido(
            "El documento de identidad no es válido: usa de 5 a 20 letras o números, sin puntos "
            "ni espacios."
        )
    return limpio


def enmascarar_documento(documento: str) -> str:
    """Conserva la longitud (T24): 2 iniciales y 4 finales visibles, el resto `•` (`10••••3040`).
    Con menos de 7 caracteres solo se ven los últimos 4, para no revelar el documento entero."""
    n = len(documento)
    if n < 7:
        return "•" * (n - 4) + documento[-4:] if n > 4 else documento
    return documento[:2] + "•" * (n - 6) + documento[-4:]


def contexto_documento(usuario_id: str) -> str:
    return f"documento:{usuario_id}"


def correo_hmac(correo_normalizado: str, clave: str) -> str:
    """HMAC-SHA256 del correo ya normalizado: busca la cuenta al iniciar sesión sin guardar el
    correo en claro. El prefijo separa este uso de la clave de otros HMAC (p. ej. `titular_id`)."""
    return hmac.new(
        clave.encode("utf-8"), f"correo:{correo_normalizado}".encode(), hashlib.sha256
    ).hexdigest()


def contexto_correo(usuario_id: str) -> str:
    return f"correo:{usuario_id}"


def contexto_telefono(usuario_id: str) -> str:
    return f"telefono:{usuario_id}"


def validar_preferencias(idioma: str, moneda: str) -> None:
    if idioma not in IDIOMAS:
        raise DatoInvalido(f"Idioma no válido. Usa uno de: {', '.join(IDIOMAS)}.")
    if moneda not in MONEDAS:
        raise DatoInvalido(f"Moneda no válida. Usa una de: {', '.join(MONEDAS)}.")


def publico(fila: dict[str, Any]) -> dict[str, Any]:
    """Lo único que se devuelve hacia afuera de un usuario (sin hash ni intentos)."""
    campos = ("id", "usuario", "rol", "estado", "creado_en", "idioma", "moneda")
    datos = {campo: fila[campo] for campo in campos}
    # Solo dice si hay contacto (para que la web sepa cuándo pedirlo), nunca cuál es.
    datos["tiene_contacto"] = bool(fila.get("correo_cifrado") or fila.get("telefono_cifrado"))
    return datos


class RepositorioUsuarios(Protocol):
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
    ) -> dict | None: ...

    def obtener_por_titular(self, titular_id: str) -> dict | None: ...

    def nombres_cifrados(self, ids: list[str]) -> dict[str, str]: ...

    def nombres_cifrados_por_titular(self, titulares: list[str]) -> dict[str, tuple[str, str]]: ...

    def obtener(self, usuario_id: str) -> dict | None: ...

    def obtener_por_alias(self, alias: str) -> dict | None: ...

    def listar(self, rol: str | None) -> list[dict]: ...

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None: ...

    def suspender(self, usuario_id: str) -> dict | None: ...

    def actualizar_preferencias(self, usuario_id: str, idioma: str, moneda: str) -> dict | None: ...

    def actualizar_contacto(
        self, usuario_id: str, correo_cifrado: str | None, telefono_cifrado: str | None
    ) -> dict | None: ...

    def registrar_fallo(self, usuario_id: str, bloquear_hasta: datetime | None) -> int: ...

    def registrar_exito(self, usuario_id: str) -> None: ...

    def crear_pendiente(
        self, usuario_id: str, alias: str, hash_clave: str, correo_cifrado: str, correo_hmac: str
    ) -> dict | None: ...

    def obtener_por_correo_hmac(self, correo_hmac: str) -> dict | None: ...

    def cerrar_verificacion(
        self,
        usuario_id: str,
        estado: str,
        nombre_cifrado: str | None,
        titular_id: str | None = None,
        documento_cifrado: str | None = None,
    ) -> dict | None: ...


class RepositorioUsuariosPostgres:
    """Cada sentencia es una transacción (`autocommit`), igual que en PAYMENTS."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return dict(fila) if fila is not None else None

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
        """`None` si el alias ya existe (índice único: correcto también bajo concurrencia). Un
        documento repetido choca con `usuarios_titular_unico` y lanza `UsuarioDuplicado`."""
        try:
            return self._una(
                f"""
                INSERT INTO usuarios (id, usuario, hash_clave, rol, nombre_cifrado,
                                      correo_cifrado, telefono_cifrado, titular_id,
                                      documento_cifrado)
                VALUES (%(id)s, %(usuario)s, %(hash_clave)s, %(rol)s, %(nombre_cifrado)s,
                        %(correo_cifrado)s, %(telefono_cifrado)s, %(titular_id)s,
                        %(documento_cifrado)s)
                ON CONFLICT (usuario) DO NOTHING
                RETURNING {_TODAS}
                """,
                {
                    "id": usuario_id,
                    "usuario": alias,
                    "hash_clave": hash_clave,
                    "rol": rol,
                    "nombre_cifrado": nombre_cifrado,
                    "correo_cifrado": correo_cifrado,
                    "telefono_cifrado": telefono_cifrado,
                    "titular_id": titular_id,
                    "documento_cifrado": documento_cifrado,
                },
            )
        except UniqueViolation as error:
            raise UsuarioDuplicado(MENSAJE_DOCUMENTO_DUPLICADO) from error

    def obtener_por_titular(self, titular_id: str) -> dict | None:
        return self._una(
            f"SELECT {_TODAS} FROM usuarios WHERE titular_id = %(t)s", {"t": titular_id}
        )

    def nombres_cifrados(self, ids: list[str]) -> dict[str, str]:
        """Solo de los CLIENTE que tienen nombre; los demás ids no aparecen."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                """
                SELECT id, nombre_cifrado FROM usuarios
                WHERE id = ANY(%(ids)s) AND rol = 'CLIENTE' AND nombre_cifrado IS NOT NULL
                """,
                {"ids": ids},
            ).fetchall()
            return {fila["id"]: fila["nombre_cifrado"] for fila in filas}

    def nombres_cifrados_por_titular(self, titulares: list[str]) -> dict[str, tuple[str, str]]:
        """`{titular_id: (usuario_id, nombre_cifrado)}`. El id es el contexto del cifrado."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                """
                SELECT titular_id, id, nombre_cifrado FROM usuarios
                WHERE titular_id = ANY(%(titulares)s) AND rol = 'CLIENTE'
                  AND nombre_cifrado IS NOT NULL
                """,
                {"titulares": titulares},
            ).fetchall()
            return {fila["titular_id"]: (fila["id"], fila["nombre_cifrado"]) for fila in filas}

    def obtener(self, usuario_id: str) -> dict | None:
        return self._una(f"SELECT {_TODAS} FROM usuarios WHERE id = %(id)s", {"id": usuario_id})

    def obtener_por_alias(self, alias: str) -> dict | None:
        return self._una(
            f"SELECT {_TODAS} FROM usuarios WHERE usuario = %(usuario)s", {"usuario": alias}
        )

    def listar(self, rol: str | None) -> list[dict]:
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_TODAS} FROM usuarios
                WHERE %(rol)s::text IS NULL OR rol = %(rol)s
                ORDER BY creado_en
                """,
                {"rol": rol},
            ).fetchall()
            return [dict(fila) for fila in filas]

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None:
        return self._una(
            f"""
            UPDATE usuarios SET rol = %(rol)s, actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id, "rol": rol},
        )

    def suspender(self, usuario_id: str) -> dict | None:
        return self._una(
            f"""
            UPDATE usuarios SET estado = 'SUSPENDIDO', actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id},
        )

    def actualizar_preferencias(self, usuario_id: str, idioma: str, moneda: str) -> dict | None:
        return self._una(
            f"""
            UPDATE usuarios SET idioma = %(idioma)s, moneda = %(moneda)s, actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id, "idioma": idioma, "moneda": moneda},
        )

    def actualizar_contacto(
        self, usuario_id: str, correo_cifrado: str | None, telefono_cifrado: str | None
    ) -> dict | None:
        """Solo cambia lo que llega: un campo en `None` conserva el valor que ya tenía."""
        return self._una(
            f"""
            UPDATE usuarios SET
                correo_cifrado = COALESCE(%(correo)s, correo_cifrado),
                telefono_cifrado = COALESCE(%(telefono)s, telefono_cifrado),
                actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id, "correo": correo_cifrado, "telefono": telefono_cifrado},
        )

    def registrar_fallo(self, usuario_id: str, bloquear_hasta: datetime | None) -> int:
        """Suma un intento fallido y, si `bloquear_hasta` llega, bloquea y reinicia el contador."""
        fila = self._una(
            """
            UPDATE usuarios SET
                intentos_fallidos = CASE WHEN %(bloquear)s::timestamptz IS NULL
                                         THEN intentos_fallidos + 1 ELSE 0 END,
                bloqueado_hasta = COALESCE(%(bloquear)s::timestamptz, bloqueado_hasta),
                actualizado_en = now()
            WHERE id = %(id)s RETURNING intentos_fallidos
            """,
            {"id": usuario_id, "bloquear": bloquear_hasta},
        )
        return fila["intentos_fallidos"] if fila else 0

    def registrar_exito(self, usuario_id: str) -> None:
        self._una(
            """
            UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL
            WHERE id = %(id)s RETURNING id
            """,
            {"id": usuario_id},
        )

    def crear_pendiente(
        self, usuario_id: str, alias: str, hash_clave: str, correo_cifrado: str, correo_hmac: str
    ) -> dict | None:
        """CLIENTE en PENDIENTE_VERIFICACION. `None` si el correo ya tiene cuenta."""
        return self._una(
            f"""
            INSERT INTO usuarios (id, usuario, hash_clave, rol, estado, correo_cifrado,
                                  correo_hmac)
            VALUES (%(id)s, %(usuario)s, %(hash_clave)s, 'CLIENTE', 'PENDIENTE_VERIFICACION',
                    %(correo_cifrado)s, %(correo_hmac)s)
            ON CONFLICT DO NOTHING
            RETURNING {_TODAS}
            """,
            {
                "id": usuario_id,
                "usuario": alias,
                "hash_clave": hash_clave,
                "correo_cifrado": correo_cifrado,
                "correo_hmac": correo_hmac,
            },
        )

    def obtener_por_correo_hmac(self, correo_hmac: str) -> dict | None:
        return self._una(
            f"SELECT {_TODAS} FROM usuarios WHERE correo_hmac = %(h)s", {"h": correo_hmac}
        )

    def cerrar_verificacion(
        self,
        usuario_id: str,
        estado: str,
        nombre_cifrado: str | None,
        titular_id: str | None = None,
        documento_cifrado: str | None = None,
    ) -> dict | None:
        """Solo desde PENDIENTE_VERIFICACION; idempotente si ya está en `estado`. `None` si no
        aplica (no existe, o está en otro estado). `UsuarioDuplicado` si el titular ya es de otra
        cuenta (índice único de la `0008`)."""
        try:
            return self._una(
                f"""
                UPDATE usuarios SET estado = %(estado)s,
                    nombre_cifrado = COALESCE(%(nombre)s, nombre_cifrado),
                    titular_id = COALESCE(%(titular)s, titular_id),
                    documento_cifrado = COALESCE(%(documento)s, documento_cifrado),
                    actualizado_en = now()
                WHERE id = %(id)s AND estado IN ('PENDIENTE_VERIFICACION', %(estado)s)
                RETURNING {_TODAS}
                """,
                {
                    "id": usuario_id,
                    "estado": estado,
                    "nombre": nombre_cifrado,
                    "titular": titular_id,
                    "documento": documento_cifrado,
                },
            )
        except UniqueViolation as error:
            raise UsuarioDuplicado(MENSAJE_DOCUMENTO_DUPLICADO) from error


def construir_pool(database_url: str) -> ConnectionPool:
    """Copiado de `services/payments/app/application/repositorio_cobros.py`
    (sin código compartido entre servicios)."""
    if not database_url:
        raise ValueError("Falta DATABASE_URL")
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )


class ServicioUsuarios:
    """Alta y administración de usuarios. AUTH no decide quién puede llamar a esto:
    el BFF exige rol BACKOFFICE antes de llegar aquí, y AUTH es privado en Cloud Run."""

    def __init__(
        self,
        repositorio: RepositorioUsuarios,
        hasher: PasswordHasher,
        cifrador: CifradorCampos | None = None,
        clave_titular: str | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._hasher = hasher
        self._cifrador = cifrador
        # `CLIENTE_ID_CLAVE`: la misma del BFF, para que el titular coincida con el consentimiento.
        self._clave_titular = clave_titular

    def crear(
        self,
        alias: str,
        clave: str,
        rol: str,
        nombre: str | None = None,
        correo: str | None = None,
        telefono: str | None = None,
        documento: str | None = None,
    ) -> dict:
        validar_alias(alias)
        validar_clave(clave)
        validar_rol(rol)
        if rol == "CLIENTE" and (documento is None or correo is None):
            raise DatoInvalido("Un cliente necesita su documento de identidad y su correo.")
        usuario_id = str(uuid.uuid4())
        nombre_cifrado = None
        if nombre is not None:
            limpio = normalizar_nombre(nombre)
            if self._cifrador is None:
                raise DatoInvalido("Este servicio no está configurado para guardar nombres.")
            nombre_cifrado = self._cifrador.cifrar(limpio, usuario_id)
        correo_cifrado, telefono_cifrado = self._cifrar_contacto(usuario_id, rol, correo, telefono)
        titular_id, documento_cifrado = self._titular(usuario_id, rol, documento)
        if titular_id is not None:
            # El alias primero: repetir el alta de un usuario que ya existe dice eso, no que el
            # documento está tomado (los scripts de prueba son idempotentes por alias).
            if self._repositorio.obtener_por_alias(alias):
                raise UsuarioDuplicado("Ya existe un usuario con ese alias.")
            if self._repositorio.obtener_por_titular(titular_id):
                raise UsuarioDuplicado(MENSAJE_DOCUMENTO_DUPLICADO)
        creado = self._repositorio.crear(
            usuario_id,
            alias,
            self._hasher.hash(clave),
            rol,
            nombre_cifrado,
            correo_cifrado,
            telefono_cifrado,
            titular_id,
            documento_cifrado,
        )
        if creado is None:
            raise UsuarioDuplicado("Ya existe un usuario con ese alias.")
        return publico(creado)

    def obtener(self, usuario_id: str) -> dict | None:
        fila = self._repositorio.obtener(usuario_id)
        return publico(fila) if fila else None

    def nombre_de(self, usuario_id: str) -> str | None:
        """El nombre del propio usuario, para mostrárselo a él al iniciar sesión (`yo`). Nunca
        se usa para otro usuario: el BFF solo lo pide con el id del token. `None` si no tiene
        nombre o no se puede descifrar."""
        fila = self._repositorio.obtener(usuario_id)
        if self._cifrador is None or not fila or not fila.get("nombre_cifrado"):
            return None
        try:
            return self._cifrador.descifrar(fila["nombre_cifrado"], usuario_id)
        except ErrorDeCifrado:
            return None

    def nombres(self, ids: list[str]) -> dict[str, str]:
        """Nombres en claro de los CLIENTE pedidos, por id de usuario o por `titular_id`: los cobros
        guardan uno u otro según su origen. Un nombre que no se pueda descifrar se omite (el cobro
        sale sin nombre) en vez de fallar toda la consulta."""
        if len(ids) > MAXIMO_IDS_POR_CONSULTA:
            raise DatoInvalido(f"Se pueden pedir hasta {MAXIMO_IDS_POR_CONSULTA} nombres a la vez.")
        if self._cifrador is None or not ids:
            return {}
        resultado: dict[str, str] = {}
        for usuario_id, cifrado in self._repositorio.nombres_cifrados(list(set(ids))).items():
            try:
                resultado[usuario_id] = self._cifrador.descifrar(cifrado, usuario_id)
            except ErrorDeCifrado:
                continue
        por_titular = self._repositorio.nombres_cifrados_por_titular(list(set(ids)))
        for titular_id, (usuario_id, cifrado) in por_titular.items():
            try:
                resultado[titular_id] = self._cifrador.descifrar(cifrado, usuario_id)
            except ErrorDeCifrado:
                continue
        return resultado

    def _titular(
        self, usuario_id: str, rol: str, documento: str | None
    ) -> tuple[str | None, str | None]:
        """El `titular_id` (HMAC) y el documento cifrado de un CLIENTE. Quien crea el usuario es un
        BACKOFFICE, que verifica la identidad: por eso aquí sí se fija el titular (W03-36)."""
        if documento is None:
            return None, None
        if rol != "CLIENTE":
            raise DatoInvalido("El documento solo se guarda para clientes.")
        limpio = normalizar_documento(documento)
        if self._cifrador is None or not self._clave_titular:
            raise DatoInvalido("Este servicio no está configurado para guardar el documento.")
        return (
            cliente_id_desde_documento(limpio, self._clave_titular),
            self._cifrador.cifrar(limpio, contexto_documento(usuario_id)),
        )

    def _documento(self, fila: dict) -> str | None:
        if self._cifrador is None or not fila.get("documento_cifrado"):
            return None
        try:
            return self._cifrador.descifrar(
                fila["documento_cifrado"], contexto_documento(fila["id"])
            )
        except ErrorDeCifrado:
            logger.warning("documento no descifrable id=%s", id_opaco(fila["id"]))
            return None

    def documento_enmascarado_de(self, usuario_id: str) -> str | None:
        """El documento del propio usuario enmascarado (`yo`, W03-34). `None` si no tiene."""
        fila = self._repositorio.obtener(usuario_id)
        documento = self._documento(fila) if fila else None
        return enmascarar_documento(documento) if documento else None

    def identidad_de(self, usuario_id: str) -> dict[str, str] | None:
        """El titular y el documento en claro de un CLIENTE con identidad verificada, o `None`. Dato
        personal: solo lo pide el BFF con el id del token, para cotizar sin pedirle la cédula
        (W03-34) y para autorizar por titular (W03-33). Nunca se loguea."""
        fila = self._repositorio.obtener(usuario_id)
        if fila is None or fila["rol"] != "CLIENTE" or not fila.get("titular_id"):
            return None
        documento = self._documento(fila)
        if documento is None:
            return None
        return {"titular_id": fila["titular_id"], "documento": documento}

    def _cifrar_contacto(
        self, usuario_id: str, rol: str, correo: str | None, telefono: str | None
    ) -> tuple[str | None, str | None]:
        """Valida y cifra lo que llegue; `(None, None)` si no llegó nada. Solo los CLIENTE tienen
        contacto: es a quienes se les envía el PDF, y así no se guardan datos de más."""
        if correo is None and telefono is None:
            return None, None
        if rol != "CLIENTE":
            raise DatoInvalido("El contacto solo se guarda para clientes.")
        correo_limpio = normalizar_correo(correo) if correo is not None else None
        telefono_limpio = normalizar_telefono(telefono) if telefono is not None else None
        if self._cifrador is None:
            raise DatoInvalido("Este servicio no está configurado para guardar el contacto.")
        correo_cifrado = (
            self._cifrador.cifrar(correo_limpio, contexto_correo(usuario_id))
            if correo_limpio is not None
            else None
        )
        telefono_cifrado = (
            self._cifrador.cifrar(telefono_limpio, contexto_telefono(usuario_id))
            if telefono_limpio is not None
            else None
        )
        return correo_cifrado, telefono_cifrado

    def actualizar_contacto(
        self, usuario_id: str, correo: str | None, telefono: str | None
    ) -> dict | None:
        """Correo y teléfono del propio cliente (el BFF solo lo llama con el id del token). Lo
        que no llega se conserva. `None` si el usuario no existe."""
        if correo is None and telefono is None:
            raise DatoInvalido("Indica un correo o un teléfono.")
        fila = self._repositorio.obtener(usuario_id)
        if fila is None:
            return None
        correo_cifrado, telefono_cifrado = self._cifrar_contacto(
            usuario_id, fila["rol"], correo, telefono
        )
        actualizado = self._repositorio.actualizar_contacto(
            usuario_id, correo_cifrado, telefono_cifrado
        )
        return publico(actualizado) if actualizado else None

    def contacto_de(self, usuario_id: str) -> dict[str, str | None] | None:
        """El correo y el teléfono en claro de un CLIENTE, o `None` si no existe o no es CLIENTE.
        Dato personal: solo lo pide POLICY para enviarle al cliente el PDF de su póliza, y nunca se
        loguea. Un campo que no se pueda descifrar sale como `None` en vez de fallar todo."""
        fila = self._repositorio.obtener(usuario_id)
        if fila is None or fila["rol"] != "CLIENTE":
            return None
        return {
            "correo": self._descifrar_contacto(fila, "correo_cifrado", contexto_correo(usuario_id)),
            "telefono": self._descifrar_contacto(
                fila, "telefono_cifrado", contexto_telefono(usuario_id)
            ),
        }

    def correo_de(self, usuario_id: str) -> str | None:
        """El correo del propio usuario, para mostrárselo a él (`yo` e `iniciarSesion`, contrato
        v0.24). Solo los CLIENTE tienen correo; el BFF lo pide únicamente con el id del token."""
        contacto = self.contacto_de(usuario_id)
        return contacto["correo"] if contacto else None

    def _descifrar_contacto(self, fila: dict, campo: str, contexto: str) -> str | None:
        if self._cifrador is None or not fila.get(campo):
            return None
        try:
            return self._cifrador.descifrar(fila[campo], contexto)
        except ErrorDeCifrado:
            logger.warning("contacto no descifrable id=%s campo=%s", id_opaco(fila["id"]), campo)
            return None

    def listar(self, rol: str | None) -> list[dict]:
        if rol is not None:
            validar_rol(rol)
        return [publico(fila) for fila in self._repositorio.listar(rol)]

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None:
        validar_rol(rol)
        fila = self._repositorio.cambiar_rol(usuario_id, rol)
        return publico(fila) if fila else None

    def suspender(self, usuario_id: str) -> dict | None:
        """Baja lógica: el usuario se conserva, pero ya no puede iniciar sesión."""
        fila = self._repositorio.suspender(usuario_id)
        return publico(fila) if fila else None

    def crear_cuenta_onboarding(self, correo: str, clave: str) -> dict:
        """Cuenta CLIENTE del onboarding del móvil (M01-05b): correo y clave, en
        PENDIENTE_VERIFICACION hasta que el servicio `onboarding` verifique la identidad. El alias
        es opaco (el cliente entra con su correo); el nombre llega después, del documento."""
        if self._cifrador is None or not self._clave_titular:
            raise DatoInvalido("Este servicio no está configurado para cuentas del onboarding.")
        limpio = normalizar_correo(correo)
        validar_clave(clave)
        usuario_id = str(uuid.uuid4())
        creado = self._repositorio.crear_pendiente(
            usuario_id,
            f"c-{uuid.uuid4().hex[:24]}",
            self._hasher.hash(clave),
            self._cifrador.cifrar(limpio, contexto_correo(usuario_id)),
            correo_hmac(limpio, self._clave_titular),
        )
        if creado is None:
            raise UsuarioDuplicado("Ya existe una cuenta con ese correo.")
        return publico(creado)

    def correo_registrado(self, correo: str) -> bool:
        """¿Ya hay una cuenta con ese correo? Lo consulta el BFF antes de enviar el código OTP del
        registro (HU-M01). Busca por el HMAC del correo: el correo no se guarda ni se loguea."""
        if not self._clave_titular:
            raise DatoInvalido("Este servicio no está configurado para cuentas del onboarding.")
        limpio = normalizar_correo(correo)
        return (
            self._repositorio.obtener_por_correo_hmac(correo_hmac(limpio, self._clave_titular))
            is not None
        )

    def cerrar_verificacion(
        self,
        usuario_id: str,
        aprobada: bool,
        nombre: str | None = None,
        documento: str | None = None,
    ) -> dict | None:
        """Lo llama el servicio `onboarding` al cerrar: ACTIVO o RECHAZADA. Con la identidad
        verificada se fijan el nombre (cifrado) y el titular y documento del CLIENTE (W03-36): así
        «Mis pólizas» liga las que le vendieron antes. Idempotente. `None` si la cuenta no existe o
        no está pendiente; `UsuarioDuplicado` si el documento ya es de otra cuenta."""
        nombre_cifrado = titular_id = documento_cifrado = None
        if aprobada and nombre:
            if self._cifrador is None:
                raise DatoInvalido("Este servicio no está configurado para guardar nombres.")
            nombre_cifrado = self._cifrador.cifrar(normalizar_nombre(nombre), usuario_id)
        if aprobada and documento:
            titular_id, documento_cifrado = self._titular(usuario_id, "CLIENTE", documento)
            otro = self._repositorio.obtener_por_titular(titular_id)
            if otro is not None and otro["id"] != usuario_id:
                raise UsuarioDuplicado(MENSAJE_DOCUMENTO_DUPLICADO)
        fila = self._repositorio.cerrar_verificacion(
            usuario_id,
            "ACTIVO" if aprobada else RECHAZADA,
            nombre_cifrado,
            titular_id,
            documento_cifrado,
        )
        return publico(fila) if fila else None

    def actualizar_preferencias(self, usuario_id: str, idioma: str, moneda: str) -> dict | None:
        """Idioma y moneda por defecto del propio usuario (el BFF solo lo llama con el id del
        token). `None` si el usuario no existe."""
        validar_preferencias(idioma, moneda)
        fila = self._repositorio.actualizar_preferencias(usuario_id, idioma, moneda)
        return publico(fila) if fila else None
