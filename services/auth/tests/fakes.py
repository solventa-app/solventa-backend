"""Dobles de prueba de AUTH: en memoria, sin levantar PostgreSQL."""

from datetime import UTC, datetime

from argon2 import PasswordHasher

# argon2 con costo mínimo: mismas reglas, pero las pruebas no tardan segundos.
HASHER_RAPIDO = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class RepositorioUsuariosMemoria:
    """Cumple el `Protocol` `RepositorioUsuarios` de `app/usuarios.py`."""

    def __init__(self) -> None:
        self.filas: dict[str, dict] = {}

    def crear(self, usuario_id: str, alias: str, hash_clave: str, rol: str) -> dict | None:
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
        }
        self.filas[usuario_id] = fila
        return dict(fila)

    def obtener(self, usuario_id: str) -> dict | None:
        fila = self.filas.get(usuario_id)
        return dict(fila) if fila else None

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


class Reloj:
    """Reloj controlable para probar el bloqueo y el vencimiento del token."""

    def __init__(self, ahora: datetime | None = None) -> None:
        # Arranca en la hora real: pyjwt rechaza tokens con `iat` en el futuro.
        self.ahora = ahora or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.ahora
