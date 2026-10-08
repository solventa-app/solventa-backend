import re

from migrations import COLECCION, aplicar_pendientes, descubrir


class FakeColeccion:
    def __init__(self):
        self.docs = {}

    def find(self, filtro, proyeccion=None):
        return [{"_id": k} for k in self.docs]

    def update_one(self, filtro, cambio, upsert=False):
        self.docs.setdefault(filtro["_id"], cambio["$setOnInsert"])


class FakeDb:
    def __init__(self):
        self.colecciones = {}

    def __getitem__(self, nombre):
        return self.colecciones.setdefault(nombre, FakeColeccion())


def test_aplica_en_orden_y_registra():
    llamadas = []
    migraciones = [
        ("0001_a", lambda db: llamadas.append("0001_a")),
        ("0002_b", lambda db: llamadas.append("0002_b")),
    ]
    db = FakeDb()

    assert aplicar_pendientes(db, migraciones) == ["0001_a", "0002_b"]
    assert llamadas == ["0001_a", "0002_b"]
    assert set(db[COLECCION].docs) == {"0001_a", "0002_b"}


def test_segunda_ejecucion_no_reaplica():
    llamadas = []
    migraciones = [("0001_a", lambda db: llamadas.append("0001_a"))]
    db = FakeDb()

    aplicar_pendientes(db, migraciones)
    assert aplicar_pendientes(db, migraciones) == []
    assert llamadas == ["0001_a"]


def test_solo_aplica_las_pendientes():
    llamadas = []
    db = FakeDb()
    aplicar_pendientes(db, [("0001_a", lambda d: llamadas.append("0001_a"))])

    nuevas = [
        ("0001_a", lambda d: llamadas.append("0001_a")),
        ("0002_b", lambda d: llamadas.append("0002_b")),
    ]
    assert aplicar_pendientes(db, nuevas) == ["0002_b"]
    assert llamadas == ["0001_a", "0002_b"]


def test_las_migraciones_reales_tienen_prefijo_numerico_unico_y_ordenado():
    nombres = [nombre for nombre, _ in descubrir()]
    assert nombres, "debe existir al menos la migración base"
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+", n) for n in nombres)
    assert nombres == sorted(nombres)
    prefijos = [n[:4] for n in nombres]
    assert len(prefijos) == len(set(prefijos)), "dos migraciones con el mismo número"
