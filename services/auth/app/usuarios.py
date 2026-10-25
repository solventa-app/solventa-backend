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
- El **correo y el teléfono** (HU-W03, bloque 8), solo de los CLIENTE y opcionales. Solo salen por
  `contacto_de`, que lo llama POLICY (servicio a servicio) para enviar el PDF de la póliza; nunca
  vuelven al cliente por GraphQL. Cada campo se cifra con su propio contexto, así que un cifrado
  movido de una columna a otra no se descifra.

La clave se guarda solo como hash argon2id y nunca sale de este servicio."""

import logging
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Literal, Protocol

from argon2 import PasswordHasher
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.cifrado import CifradorCampos, ErrorDeCifrado
from app.identificadores import id_opaco

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
SEPARADORES_DE_TELEFONO = " -().\t"
# Preferencias de la interfaz (KAN-209): las mismas que ofrecen los selectores del header de la web.
IDIOMAS: tuple[str, ...] = ("ES", "EN")
MONEDAS: tuple[str, ...] = ("COP", "MXN", "BRL", "CLP", "PEN", "USD", "EUR")

# Columnas que pueden salir del servicio: nunca el hash ni el estado del bloqueo.
_PUBLICAS = "id, usuario, rol, estado, creado_en, idioma, moneda"
_TODAS = (
    f"{_PUBLICAS}, hash_clave, intentos_fallidos, bloqueado_hasta, nombre_cifrado, "
    "correo_cifrado, telefono_cifrado"
)


class DatoInvalido(Exception):
    """La solicitud no cumple las reglas (alias, clave o rol). Mensaje apto para el cliente."""


class UsuarioDuplicado(Exception):
    """Ya existe un usuario con ese alias."""


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


def validar_clave(clave: str) -> None:
    if not CLAVE_LONGITUD_MINIMA <= len(clave) <= CLAVE_LONGITUD_MAXIMA:
        raise DatoInvalido(
            f"La clave debe tener entre {CLAVE_LONGITUD_MINIMA} y {CLAVE_LONGITUD_MAXIMA} "
            "caracteres."
        )


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
    ) -> dict | None: ...

    def nombres_cifrados(self, ids: list[str]) -> dict[str, str]: ...

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
    ) -> dict | None:
        """`None` si el alias ya existe (índice único: correcto también bajo concurrencia)."""
        return self._una(
            f"""
            INSERT INTO usuarios (id, usuario, hash_clave, rol, nombre_cifrado,
                                  correo_cifrado, telefono_cifrado)
            VALUES (%(id)s, %(usuario)s, %(hash_clave)s, %(rol)s, %(nombre_cifrado)s,
                    %(correo_cifrado)s, %(telefono_cifrado)s)
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
            },
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
    ) -> None:
        self._repositorio = repositorio
        self._hasher = hasher
        self._cifrador = cifrador

    def crear(
        self,
        alias: str,
        clave: str,
        rol: str,
        nombre: str | None = None,
        correo: str | None = None,
        telefono: str | None = None,
    ) -> dict:
        validar_alias(alias)
        validar_clave(clave)
        validar_rol(rol)
        usuario_id = str(uuid.uuid4())
        nombre_cifrado = None
        if nombre is not None:
            limpio = normalizar_nombre(nombre)
            if self._cifrador is None:
                raise DatoInvalido("Este servicio no está configurado para guardar nombres.")
            nombre_cifrado = self._cifrador.cifrar(limpio, usuario_id)
        correo_cifrado, telefono_cifrado = self._cifrar_contacto(usuario_id, rol, correo, telefono)
        creado = self._repositorio.crear(
            usuario_id,
            alias,
            self._hasher.hash(clave),
            rol,
            nombre_cifrado,
            correo_cifrado,
            telefono_cifrado,
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
        """Nombres en claro de los CLIENTE pedidos. Un nombre que no se pueda descifrar se
        omite (el cobro sale sin nombre) en vez de fallar toda la consulta."""
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
        return resultado

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

    def actualizar_preferencias(self, usuario_id: str, idioma: str, moneda: str) -> dict | None:
        """Idioma y moneda por defecto del propio usuario (el BFF solo lo llama con el id del
        token). `None` si el usuario no existe."""
        validar_preferencias(idioma, moneda)
        fila = self._repositorio.actualizar_preferencias(usuario_id, idioma, moneda)
        return publico(fila) if fila else None
