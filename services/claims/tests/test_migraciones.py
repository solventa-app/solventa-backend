import importlib

from migrations import COLECCION, aplicar_pendientes, descubrir

colecciones = importlib.import_module("migrations.versiones.0002_colecciones")


class FakeColeccion:
    def __init__(self):
        self.docs = {}
        self.indices = {}

    def find(self, filtro, proyeccion=None):
        return [{"_id": k} for k in self.docs]

    def update_one(self, filtro, cambio, upsert=False):
        self.docs.setdefault(filtro["_id"], cambio["$setOnInsert"])

    def create_index(self, llaves, name, unique):
        self.indices[name] = (tuple(llaves), unique)


class FakeDb:
    def __init__(self):
        self.colecciones = {}
        self.creadas = {}
        self.comandos = []

    def __getitem__(self, nombre):
        return self.colecciones.setdefault(nombre, FakeColeccion())

    def list_collection_names(self):
        return list(self.creadas)

    def create_collection(self, nombre, validator, validationLevel):
        self.creadas[nombre] = validator

    def command(self, nombre, coleccion, validator, validationLevel):
        self.comandos.append((nombre, coleccion))


def test_descubre_las_migraciones_en_orden():
    assert [n for n, _ in descubrir()] == ["0001_base", "0002_colecciones"]


def test_segunda_ejecucion_no_reaplica():
    db = FakeDb()
    assert len(aplicar_pendientes(db)) == 2
    assert aplicar_pendientes(db) == []
    assert set(db[COLECCION].docs) == {"0001_base", "0002_colecciones"}


def test_crea_las_seis_colecciones_con_validacion_e_indices_unicos():
    db = FakeDb()
    colecciones.aplicar(db)
    assert set(db.creadas) == {
        "siniestros",
        "peritos",
        "evidencias_siniestro",
        "verificaciones_siniestro",
        "asignaciones_perito",
        "resoluciones_siniestro",
    }
    assert all("$jsonSchema" in v for v in db.creadas.values())
    # Una sola resolución y una sola asignación por caso; radicado único.
    assert db["resoluciones_siniestro"].indices["ux_resoluciones_siniestro_siniestro"][1] is True
    assert db["asignaciones_perito"].indices["ux_asignaciones_perito_siniestro"][1] is True
    assert db["siniestros"].indices["ux_siniestros_radicado"][1] is True


def test_si_la_coleccion_existe_actualiza_la_validacion_sin_recrearla():
    db = FakeDb()
    colecciones.aplicar(db)
    colecciones.aplicar(db)
    assert ("collMod", "siniestros") in db.comandos


def test_los_datos_personales_solo_se_guardan_cifrados():
    propiedades = colecciones.COLECCIONES["siniestros"]["$jsonSchema"]["properties"]
    assert "asegurado_nombre_cifrado" in propiedades
    assert not {"asegurado", "nombre", "documento", "descripcion", "ubicacion"} & set(propiedades)
