"""Dobles del bloque 9 (W03-19 a W03-37): el repositorio con contacto, código, enlace y purga, el
almacenamiento de la imagen de la firma y las sesiones de pago de PAYMENTS."""

import io
import uuid
from datetime import UTC, datetime
from typing import Any

from fakes import RepositorioPropuestasFalso
from PIL import Image

from app.application.cliente_almacenamiento import ArchivoRechazado, Subida
from app.application.cliente_payments import PaymentsNoDisponible, SesionPago
from app.application.repositorio_propuestas import ESTADOS_ABIERTOS, ESTADOS_POR_VISIBLE


def png(ancho: int = 600, alto: int = 200, fondo=(255, 255, 255, 255), trazo=True) -> bytes:
    """Una firma de prueba: fondo blanco (o el que se pida) y un trazo negro en el centro."""
    imagen = Image.new("RGBA", (ancho, alto), fondo)
    if trazo:
        for x in range(ancho // 4, 3 * ancho // 4):
            imagen.putpixel((x, alto // 2), (0, 0, 0, 255))
    salida = io.BytesIO()
    imagen.save(salida, format="PNG")
    return salida.getvalue()


class RepositorioBloque9Falso(RepositorioPropuestasFalso):
    def __init__(self) -> None:
        super().__init__()
        self.contactos: dict[str, dict[str, str | None]] = {}
        self.verificaciones: list[dict[str, Any]] = []
        self.enlaces: list[dict[str, Any]] = []

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]:
        propuesta, creada = super().crear_o_obtener(propuesta_id, datos)
        if creada:
            guardada = self._propuesta(propuesta["id"])
            guardada.update(
                actor_rol=datos.get("actor_rol"),
                origen_contacto=datos.get("origen_contacto"),
                contacto_borrado_en=None,
            )
            contacto = datos.get("contacto") or {}
            self.contactos[propuesta["id"]] = {
                "nombre": contacto.get("nombre"),
                "correo": contacto.get("correo"),
                "firma_texto": None,
            }
            propuesta = dict(guardada)
        return propuesta, creada

    def contacto(self, propuesta_id: str) -> dict[str, str | None] | None:
        if self._propuesta(propuesta_id) is None:
            return None
        return dict(self.contactos.get(propuesta_id) or {})

    def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]:
        contactos = {i: self.contactos.get(i) or {} for i in propuesta_ids}
        return {i: c["nombre"] for i, c in contactos.items() if c.get("nombre")}

    def actualizar_correo(self, propuesta_id: str, correo: str) -> None:
        self.contactos[propuesta_id]["correo"] = correo

    def crear_verificacion(self, propuesta_id: str, huella: str, vence_en: datetime) -> None:
        for v in self.verificaciones:
            if v["propuesta_id"] == propuesta_id and v["verificada_en"] is None:
                v["anulada_en"] = datetime.now(UTC)
        self.verificaciones.append(
            {
                "id": str(uuid.uuid4()),
                "propuesta_id": propuesta_id,
                "huella": huella,
                "vence_en": vence_en,
                "intentos": 0,
                "verificada_en": None,
                "anulada_en": None,
            }
        )

    def verificacion_vigente(self, propuesta_id: str) -> dict[str, Any] | None:
        vigentes = [
            v
            for v in self.verificaciones
            if v["propuesta_id"] == propuesta_id and not v["anulada_en"]
        ]
        return dict(vigentes[-1]) if vigentes else None

    def _verificacion(self, verificacion_id: str) -> dict[str, Any]:
        return next(v for v in self.verificaciones if v["id"] == verificacion_id)

    def sumar_intento_codigo(self, verificacion_id: str) -> int:
        verificacion = self._verificacion(verificacion_id)
        verificacion["intentos"] += 1
        return verificacion["intentos"]

    def marcar_verificada(self, verificacion_id: str, propuesta_id: str) -> bool:
        self._verificacion(verificacion_id)["verificada_en"] = datetime.now(UTC)
        return self.cambiar_estado(propuesta_id, "IDENTIDAD_VERIFICADA", ("ACEPTADA",))

    def crear_enlace(self, propuesta_id: str, huella: str, vence_en: datetime) -> None:
        for e in self.enlaces:
            if e["propuesta_id"] == propuesta_id and e["anulado_en"] is None:
                e["anulado_en"] = datetime.now(UTC)
        self.enlaces.append(
            {
                "id": str(uuid.uuid4()),
                "propuesta_id": propuesta_id,
                "huella": huella,
                "vence_en": vence_en,
                "anulado_en": None,
                "creado_en": datetime.now(UTC),
            }
        )

    def enlace_por_huella(self, huella: str) -> dict[str, Any] | None:
        return next((dict(e) for e in self.enlaces if e["huella"] == huella), None)

    def enlace_activo(self, propuesta_id: str) -> dict[str, Any] | None:
        activos = [
            e for e in self.enlaces if e["propuesta_id"] == propuesta_id and not e["anulado_en"]
        ]
        return dict(activos[-1]) if activos else None

    def registrar_firma_enlace(
        self, propuesta_id: str, evidencia: dict[str, Any], firma_texto: str | None
    ) -> tuple[dict, bool]:
        existente = self._evidencias.get(propuesta_id)
        if existente is not None:
            return dict(existente), False
        guardada = {
            k: evidencia[k]
            for k in (
                "id",
                "documento_hash",
                "version_documento",
                "firmante_id",
                "referencia_proveedor",
                "firmada_en",
                "modo_firma",
                "imagen_firma_archivo_id",
            )
        }
        guardada.update(propuesta_id=propuesta_id, creado_en=datetime.now(UTC))
        self._evidencias[propuesta_id] = guardada
        self.documentos_guardados[propuesta_id] = evidencia["documento"]
        self.contactos.setdefault(propuesta_id, {})["firma_texto"] = firma_texto
        self.cambiar_estado(propuesta_id, "FIRMADA", ("EN_FIRMA",))
        return dict(guardada), True

    def cancelar_y_borrar_contacto(self, propuesta_id: str) -> str | None:
        propuesta = self._propuesta(propuesta_id)
        if propuesta is None or propuesta["estado"] == "EMITIDA":
            return None
        if propuesta["estado"] in ESTADOS_ABIERTOS:
            propuesta["estado"] = "CANCELADA"
            propuesta["motivo_estado"] = "CONSENTIMIENTO_NO_VIGENTE"
        propuesta["contacto_borrado_en"] = propuesta.get("contacto_borrado_en") or datetime.now(UTC)
        self.contactos[propuesta_id] = {"nombre": None, "correo": None, "firma_texto": None}
        for e in self.enlaces:
            if e["propuesta_id"] == propuesta_id and e["anulado_en"] is None:
                e["anulado_en"] = datetime.now(UTC)
        evidencia = self._evidencias.get(propuesta_id)
        return evidencia.get("imagen_firma_archivo_id") if evidencia else None

    def propuestas_del_consentimiento(self, consentimiento_id: str, titular_id: str) -> list[str]:
        return [
            p["id"]
            for p in self._por_oferta.values()
            if p["consentimiento_id"] == consentimiento_id
            and p["cliente_id"] == titular_id
            and p["estado"] != "EMITIDA"
        ]

    def candidatas_a_purga(self, limite: int) -> list[dict[str, Any]]:
        return [
            {"id": p["id"], "consentimiento_id": p["consentimiento_id"]}
            for p in self._por_oferta.values()
            if p["estado"] != "EMITIDA" and not p.get("contacto_borrado_en")
        ][:limite]

    def listar(self, cliente_id, actor_usuario_id, estado_visible, pagina, tamano, actor_rol=None):
        estados = ESTADOS_POR_VISIBLE[estado_visible] if estado_visible else None
        filas = [
            dict(p, nombre_titular=self.contactos.get(p["id"], {}).get("nombre"))
            for p in self._por_oferta.values()
            if (cliente_id is None or p["cliente_id"] == cliente_id)
            and (actor_usuario_id is None or p["actor_usuario_id"] == actor_usuario_id)
            and (actor_rol is None or p.get("actor_rol") == actor_rol)
            and (estados is None or p["estado"] in estados)
        ]
        filas.sort(key=lambda p: p["creado_en"], reverse=True)
        return filas[(pagina - 1) * tamano : pagina * tamano], len(filas)

    def polizas_resumen(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {p["id"]: dict(p) for p in self._polizas.values() if p["id"] in ids}


class AlmacenamientoFalso:
    """El servicio de archivos: guarda lo que el «navegador» subió y responde como el real."""

    def __init__(self) -> None:
        self.archivos: dict[str, dict[str, Any]] = {}
        self.contenidos: dict[str, bytes] = {}
        self.retenidos: list[str] = []

    def solicitar_subida(self, propuesta_id, consentimiento_id, tipo_contenido, tamano) -> Subida:
        if tamano > 512_000:
            raise ArchivoRechazado("TAMANO_NO_ADMITIDO")
        archivo_id = str(uuid.uuid4())
        self.archivos[archivo_id] = {
            "id": archivo_id,
            "tipo": "firma-imagen",
            "estado": "PENDIENTE",
            "propietario_id": propuesta_id,
            "consentimiento_id": consentimiento_id,
        }
        return Subida(
            archivo=dict(self.archivos[archivo_id]),
            url=f"http://bucket/{archivo_id}",
            metodo="PUT",
            encabezados={"Content-Type": tipo_contenido},
            vence_en="2026-10-27T12:10:00+00:00",
        )

    def subir(self, archivo_id: str, contenido: bytes, rechazar: bool = False) -> None:
        """Lo que hace el navegador con el PUT y lo que decide el servicio al confirmar."""
        self.contenidos[archivo_id] = contenido
        self.archivos[archivo_id]["rechazar"] = rechazar

    def confirmar(self, archivo_id: str) -> dict[str, Any]:
        archivo = self.archivos.get(archivo_id)
        if archivo is None:
            raise ArchivoRechazado("ARCHIVO_NO_ENCONTRADO")
        if archivo_id not in self.contenidos:
            raise ArchivoRechazado("OBJETO_NO_SUBIDO")
        archivo["estado"] = "RECHAZADO" if archivo.get("rechazar") else "DISPONIBLE"
        if archivo["estado"] == "RECHAZADO":
            archivo["motivo"] = "CONTENIDO_NO_COINCIDE"
        return {k: v for k, v in archivo.items() if k != "rechazar"}

    def bajar(self, archivo_id: str) -> bytes:
        if self.archivos.get(archivo_id, {}).get("estado") != "DISPONIBLE":
            raise ArchivoRechazado("ARCHIVO_NO_DISPONIBLE")
        return self.contenidos[archivo_id]

    def retener(self, archivo_id: str) -> None:
        self.retenidos.append(archivo_id)


class PagosFalso:
    """PAYMENTS visto por POLICY: una sesión por propuesta que «paga» la prueba."""

    def __init__(self) -> None:
        self.sesiones: dict[str, SesionPago] = {}
        self.por_propuesta: dict[str, str] = {}
        self.cae = False
        self.aperturas = 0

    def abrir_sesion(self, propuesta_id, cliente_id, monto, moneda, descripcion, url, idioma):
        if self.cae:
            raise PaymentsNoDisponible("caído")
        self.aperturas += 1
        actual = self.por_propuesta.get(propuesta_id)
        if actual and self.sesiones[actual].estado in ("abierta", "cobrada"):
            return self.sesiones[actual]
        sesion_id = f"ses_{uuid.uuid4().hex[:8]}"
        self.sesiones[sesion_id] = SesionPago(
            estado="abierta",
            sesion_id=sesion_id,
            url_pago=f"http://localhost:8103/checkout/{sesion_id}",
            vence_en="2026-10-27T12:10:00+00:00",
        )
        self.por_propuesta[propuesta_id] = sesion_id
        return self.sesiones[sesion_id]

    def pagar(self, sesion_id: str, estado: str = "cobrada", motivo: str | None = None) -> None:
        sesion = self.sesiones[sesion_id]
        sesion.estado, sesion.motivo = estado, motivo
        if estado == "cobrada":
            sesion.cobro = {"id": "cobro-1", "estado": "cobrado", "origen": "INTERACTIVO"}

    def confirmar_sesion(self, sesion_id: str, propuesta_id: str) -> SesionPago | None:
        if self.cae:
            raise PaymentsNoDisponible("caído")
        if self.por_propuesta.get(propuesta_id) != sesion_id:
            return None
        return self.sesiones[sesion_id]


class ConsentimientosFalso:
    def __init__(self, vigentes: dict[str, bool] | None = None, cae: bool = False) -> None:
        self.vigentes = vigentes or {}
        self.cae = cae

    def consentimiento_vigente(self, consentimiento_id: str) -> bool:
        from app.application.cliente_auth import AuthNoDisponible

        if self.cae:
            raise AuthNoDisponible("caído")
        return self.vigentes.get(consentimiento_id, True)
