"""Dobles de prueba de CLAIMS: un repositorio en memoria con la interfaz de
`RepositorioSiniestrosMongo`.

Las pruebas del servicio y de la API no necesitan un replica set real; el repositorio de MongoDB se
verifica en vivo con `docker compose` (ver README)."""

import copy
from datetime import UTC, datetime

AHORA = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
ABIERTOS = ("PENDIENTE", "EN_REVISION")


class RepositorioFalso:
    def __init__(self) -> None:
        self.siniestros: dict[str, dict] = {}
        self.evidencias_por: dict[str, list[dict]] = {}
        self.verificaciones_por: dict[str, list[dict]] = {}
        self.asignaciones_por: dict[str, dict] = {}
        self.resoluciones_por: dict[str, dict] = {}
        self.peritos_por: dict[str, dict] = {}
        self.lecturas_primaria = 0

    # ---------- Lectura (misma firma que el repositorio real) ----------

    def listar(self, estado=None):
        todos = [copy.deepcopy(s) for s in self.siniestros.values()]
        if estado:
            todos = [s for s in todos if s["estado"] == estado]
        return sorted(todos, key=lambda s: s["notificado_en"], reverse=True)

    def pendientes_vencidos(self, ahora):
        return [
            s["id"]
            for s in self.siniestros.values()
            if s["estado"] == "PENDIENTE" and s["vence_dictamen_en"] <= ahora
        ]

    def obtener(self, siniestro_id, primaria=False):
        self.lecturas_primaria += int(primaria)
        s = self.siniestros.get(siniestro_id)
        return copy.deepcopy(s) if s else None

    def evidencias(self, ids, primaria=False):
        return {i: copy.deepcopy(self.evidencias_por.get(i, [])) for i in ids}

    def verificaciones(self, ids, primaria=False):
        return {i: copy.deepcopy(self.verificaciones_por.get(i, [])) for i in ids}

    def asignaciones(self, ids, primaria=False):
        return {
            i: copy.deepcopy(self.asignaciones_por[i]) for i in ids if i in self.asignaciones_por
        }

    def resoluciones(self, ids, primaria=False):
        return {
            i: copy.deepcopy(self.resoluciones_por[i]) for i in ids if i in self.resoluciones_por
        }

    def peritos(self, primaria=False):
        return [copy.deepcopy(p) for p in self.peritos_por.values() if p["activo"]]

    def perito(self, perito_id, primaria=False):
        p = self.peritos_por.get(perito_id)
        return copy.deepcopy(p) if p else None

    # ---------- Resolución (misma regla que la transacción real) ----------

    def registrar_derivacion(self, siniestro_id, asignacion, ahora):
        s = self.siniestros.get(siniestro_id)
        if siniestro_id in self.asignaciones_por or s is None or s["estado"] not in ABIERTOS:
            return False
        self.asignaciones_por[siniestro_id] = copy.deepcopy(asignacion)
        s["estado"] = "EN_REVISION"
        return True

    def registrar_resolucion(self, siniestro_id, resolucion, estado, ahora):
        s = self.siniestros.get(siniestro_id)
        if siniestro_id in self.resoluciones_por or s is None or s["estado"] not in ABIERTOS:
            return False
        self.resoluciones_por[siniestro_id] = copy.deepcopy(resolucion)
        s["estado"] = estado
        return True

    # ---------- Escritura de la semilla ----------

    def guardar_perito(self, perito):
        self.peritos_por[perito["id"]] = copy.deepcopy(perito)

    def guardar_siniestro(self, siniestro, ahora):
        campos = (
            "id",
            "radicado",
            "poliza_id",
            "estado",
            "asegurado",
            "cobertura_aplicada",
            "entidad_acreedora",
            "ocurrido_en",
            "notificado_en",
            "ubicacion",
            "descripcion",
            "suma_reclamada",
            "vence_dictamen_en",
        )
        self.siniestros[siniestro["id"]] = {k: copy.deepcopy(siniestro[k]) for k in campos}

    def evidencia(self, evidencia_id):
        for lista in self.evidencias_por.values():
            for e in lista:
                if e["id"] == evidencia_id:
                    return copy.deepcopy(e)
        return None

    def guardar_evidencia(self, evidencia):
        lista = self.evidencias_por.setdefault(evidencia["siniestro_id"], [])
        lista[:] = [e for e in lista if e["id"] != evidencia["id"]] + [copy.deepcopy(evidencia)]

    def guardar_verificacion(self, verificacion):
        lista = self.verificaciones_por.setdefault(verificacion["siniestro_id"], [])
        lista[:] = [v for v in lista if v["id"] != verificacion["id"]] + [
            copy.deepcopy(verificacion)
        ]

    def reiniciar_resolucion(self, siniestro_id, asignacion, resolucion):
        self.asignaciones_por.pop(siniestro_id, None)
        self.resoluciones_por.pop(siniestro_id, None)
        if asignacion is not None:
            self.asignaciones_por[siniestro_id] = copy.deepcopy(asignacion)
        if resolucion is not None:
            self.resoluciones_por[siniestro_id] = copy.deepcopy(resolucion)


class AlmacenamientoFalso:
    def __init__(self):
        self.subidas = []

    def subir(self, tipo, tipo_contenido, contenido, propietario_id):
        self.subidas.append((tipo, tipo_contenido, len(contenido), propietario_id))
        return f"arch-{len(self.subidas)}"


class PolicyFalso:
    def __init__(self):
        self.consultas = []

    def poliza_de_propuesta(self, propuesta_id):
        self.consultas.append(propuesta_id)
        return "poliza-de-" + propuesta_id


def siniestro(sid="sin-01", estado="PENDIENTE", notificado_en=AHORA, **extra) -> dict:
    return {
        "id": sid,
        "radicado": f"SIN-2026-{sid[-2:]}",
        "poliza_id": "pol-01",
        "estado": estado,
        "asegurado": {"nombre": "Camilo Rojas", "documento": "C.C. 79.845.210"},
        "cobertura_aplicada": "Vida grupo deudor",
        "entidad_acreedora": "Banco Aliado S.A.",
        "ocurrido_en": notificado_en,
        "notificado_en": notificado_en,
        "ubicacion": {
            "descripcion": "Calle 80",
            "ciudad": "Bogotá",
            "latitud": 4.72,
            "longitud": -74.12,
        },
        "descripcion": "Choque leve",
        "suma_reclamada": {"valor": "8650000", "moneda": "COP"},
        "vence_dictamen_en": notificado_en,
        **extra,
    }


def perito(pid="per-01", dias=4, activo=True) -> dict:
    return {
        "id": pid,
        "nombre": f"Perito {pid}",
        "ciudad": "Bogotá",
        "red": "Red Centro",
        "registro": f"RNPL-{pid}",
        "tiempo_respuesta_dias": dias,
        "activo": activo,
        "creado_en": AHORA,
    }


def repositorio_con_datos() -> RepositorioFalso:
    repo = RepositorioFalso()
    repo.siniestros["sin-01"] = siniestro(
        "sin-01", notificado_en=datetime(2026, 10, 19, tzinfo=UTC)
    )
    repo.siniestros["sin-02"] = siniestro(
        "sin-02", "EN_REVISION", datetime(2026, 10, 20, tzinfo=UTC)
    )
    repo.siniestros["sin-03"] = siniestro("sin-03", "APROBADO", datetime(2026, 10, 18, tzinfo=UTC))
    repo.peritos_por = {"per-01": perito("per-01", 4), "per-02": perito("per-02", 3, activo=False)}
    repo.evidencias_por["sin-01"] = [
        {
            "id": "ev-1",
            "siniestro_id": "sin-01",
            "archivo_id": "arch-1",
            "tipo": "FOTO",
            "titulo": "Bumper",
            "nombre_archivo": "sin-01-foto-1.jpg",
            "registrada_en": AHORA,
            "geoetiquetada": True,
            "latitud": 4.72,
            "longitud": -74.12,
            "nota": "4.72, -74.12 · tomada en sitio",
        }
    ]
    repo.verificaciones_por["sin-01"] = [
        {
            "id": "ver-1",
            "siniestro_id": "sin-01",
            "orden": 1,
            "descripcion": "Póliza vigente",
            "cumple": True,
        }
    ]
    repo.asignaciones_por["sin-02"] = {
        "id": "asig-1",
        "siniestro_id": "sin-02",
        "perito_id": "per-01",
        "instrucciones": "Revisar el bumper",
        "notificacion": "Correo certificado y expediente digital de siniestros",
        "asignada_en": AHORA,
        "vence_informe_en": AHORA,
        "actor_usuario_id": None,
    }
    repo.resoluciones_por["sin-03"] = {
        "id": "res-1",
        "siniestro_id": "sin-03",
        "tipo": "APROBACION",
        "motivo": None,
        "comentario": None,
        "notificacion": "Enviada al asegurado y a la entidad acreedora",
        "via_recurso": None,
        "indemnizacion": {"valor": "8000000", "moneda": "COP"},
        "metodo_transferencia": "ACH Colombia Interbancario",
        "plazo_dispersion": "1 a 3 días hábiles",
        "orden_pago_id": None,
        "resuelta_en": AHORA,
        "actor_usuario_id": None,
    }
    return repo


class PolicyPolizas:
    def __init__(self, cobertura="52000000", falla=None):
        self.cobertura = cobertura
        self.falla = falla

    def obtener_poliza(self, poliza_id):
        if self.falla:
            raise self.falla
        return {"id": poliza_id, "cobertura": {"valor": self.cobertura, "moneda": "COP"}}


class PaymentsFalso:
    def __init__(self, falla=None):
        self.ordenes = {}
        self.falla = falla

    def ordenar_indemnizacion(self, siniestro_id, poliza_id, monto, moneda):
        if self.falla:
            raise self.falla
        self.ordenes.setdefault(
            siniestro_id,
            {
                "id": f"ind-{siniestro_id}",
                "monto": monto,
                "metodo_transferencia": "ACH Colombia Interbancario",
            },
        )
        return self.ordenes[siniestro_id]
