"""Persistencia de propuestas en PostgreSQL (HU-W03, KAN-209).

Capa simple (sin puertos y adaptadores: hexagonal es solo para `acl-worker`, regla #3). POLICY es el
**único escritor** de `propuestas` (regla #1).

**Idempotencia en la base de datos (CA-W03-01):** `oferta_id` es UNIQUE y `crear_o_obtener` hace
`INSERT ... ON CONFLICT (oferta_id) DO NOTHING RETURNING ...`. Con dos aceptaciones simultáneas de
la misma oferta, Postgres serializa el conflicto con el lock del índice y la segunda lee la fila de
la primera. El pool va en `autocommit` (una sentencia = una transacción) para que la garantía
dependa solo de ese lock.

La oferta (prima, cobertura, plazo) se guarda cifrada por campo (HA-SEG-002), con el id de la
propuesta como dato asociado. La evidencia de firma (`evidencias_firma`) es append-only en la base y
guarda el documento firmado cifrado, con el id de la evidencia como dato asociado.

Bloque 9 (W03-19 a W03-37): el contacto que da el ASESOR (`nombre_cifrado`, `correo_cifrado`) y el
texto del modo «nombre» (`firma_texto_cifrado`) van cifrados por campo con su propio contexto
(`nombre:<id>`, `correo:<id>`, `firma:<id>`) y **no salen en el dict de la propuesta**: solo los
devuelve `contacto` a quien los necesita para avisar o armar el PDF. El código y el token del
enlace se guardan solo como huella.

HU-W07 (socios, migración `0008`): con `actor_rol = SOCIO`, `actor_usuario_id` es el id del socio y
`tasa_comision` su tasa congelada al aceptar. Emitir la póliza de un socio causa su comisión en la
misma transacción (`comisiones`, una por póliza, con la prima y la comisión cifradas)."""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos
from app.application.comisiones import calcular_comision, mes

_COLUMNAS = (
    "id, oferta_id, cliente_id, consentimiento_id, version_reglas, actor_usuario_id, estado, "
    "oferta_cifrada, intentos_emision, motivo_estado, creado_en, actualizado_en, "
    "origen_contacto, actor_rol, contacto_borrado_en, tasa_comision"
)
_PRIVADAS = ("nombre_cifrado", "correo_cifrado", "firma_texto_cifrado")
# La póliza de la propuesta, si ya se emitió (una subconsulta: `polizas.propuesta_id` es UNIQUE).
_POLIZA_ID = "(SELECT p.id FROM polizas p WHERE p.propuesta_id = propuestas.id) AS poliza_id"

_COLUMNAS_POLIZA = (
    "id, propuesta_id, numero, cliente_id, estado, vigencia_desde, vigencia_hasta, notificada_en, "
    "aviso_omitido_motivo"
)

_COLUMNAS_EVIDENCIA = (
    "id, propuesta_id, documento_hash, version_documento, firmante_id, "
    "referencia_proveedor, firmada_en, creado_en, modo_firma, imagen_firma_archivo_id"
)
# Estados en los que una propuesta todavía puede cancelarse (W03-26): todo lo que no es final.
ESTADOS_ABIERTOS = (
    "ACEPTADA",
    "IDENTIDAD_VERIFICADA",
    "EN_FIRMA",
    "FIRMADA",
    "EN_EMISION",
    "EN_REVISION",
    "PENDIENTE_PAGO",
    "PAGO_RECHAZADO",
)
ESTADOS_POR_VISIBLE = {
    "EMITIENDO": ESTADOS_ABIERTOS,
    "EMITIDA": ("EMITIDA",),
    "NO_EMITIDA": ("NO_EMITIDA", "CANCELADA"),
}


class RepositorioPropuestasPostgres:
    def __init__(self, pool: ConnectionPool, cifrador: CifradorCampos) -> None:
        self._pool = pool
        self._cifrador = cifrador

    def _a_propuesta(self, fila: dict[str, Any]) -> dict[str, Any]:
        propuesta = {k: v for k, v in fila.items() if k != "oferta_cifrada" and k not in _PRIVADAS}
        propuesta.update(json.loads(self._cifrador.descifrar(fila["oferta_cifrada"], fila["id"])))
        return propuesta

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE id = %(id)s",
                {"id": propuesta_id},
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": oferta_id},
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]:
        """Devuelve la propuesta y si se creó ahora (False = ya existía para esa oferta)."""
        oferta = {k: datos[k] for k in ("tipo", "prima", "cobertura", "plazo_meses")}
        # El nivel de riesgo de la oferta lo necesita UNDER al decidir (puede faltar).
        oferta["nivel_riesgo"] = datos.get("nivel_riesgo")
        cifrada = self._cifrador.cifrar(json.dumps(oferta), propuesta_id)
        contacto = datos.get("contacto")
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO propuestas
                    (id, oferta_id, cliente_id, consentimiento_id, version_reglas,
                     actor_usuario_id, estado, oferta_cifrada, actor_rol, origen_contacto,
                     nombre_cifrado, correo_cifrado, tasa_comision)
                VALUES
                    (%(id)s, %(oferta_id)s, %(cliente_id)s, %(consentimiento_id)s,
                     %(version_reglas)s, %(actor_usuario_id)s, 'ACEPTADA', %(oferta_cifrada)s,
                     %(actor_rol)s, %(origen_contacto)s, %(nombre_cifrado)s, %(correo_cifrado)s,
                     %(tasa_comision)s)
                ON CONFLICT (oferta_id) DO NOTHING
                RETURNING {_COLUMNAS}, NULL::text AS poliza_id
                """,
                {
                    "id": propuesta_id,
                    "oferta_id": datos["oferta_id"],
                    "cliente_id": datos["cliente_id"],
                    "consentimiento_id": datos.get("consentimiento_id"),
                    "version_reglas": datos.get("version_reglas"),
                    "actor_usuario_id": datos.get("actor_usuario_id"),
                    "oferta_cifrada": cifrada,
                    "actor_rol": datos.get("actor_rol"),
                    "origen_contacto": datos.get("origen_contacto"),
                    "tasa_comision": datos.get("tasa_comision"),
                    "nombre_cifrado": self._cifrar_opcional(
                        contacto and contacto["nombre"], f"nombre:{propuesta_id}"
                    ),
                    "correo_cifrado": self._cifrar_opcional(
                        contacto and contacto["correo"], f"correo:{propuesta_id}"
                    ),
                },
            ).fetchone()
            if fila is not None:
                return self._a_propuesta(fila), True
            existente = conexion.execute(
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": datos["oferta_id"]},
            ).fetchone()
        return self._a_propuesta(existente), False

    def registrar_intento(self, propuesta_id: str) -> int:
        """Cuenta un intento de emisión y devuelve el total (acota los reintentos del cobro)."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE propuestas SET intentos_emision = intentos_emision + 1 "
                "WHERE id = %(id)s RETURNING intentos_emision",
                {"id": propuesta_id},
            ).fetchone()
        return fila["intentos_emision"]

    def reiniciar_intentos(self, propuesta_id: str) -> None:
        """Vuelve a cero el contador de intentos: una propuesta que la reconciliación reabre para
        seguir consultando su cobro tiene derecho a otra tanda completa de consultas."""
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE propuestas SET intentos_emision = 0 WHERE id = %(id)s",
                {"id": propuesta_id},
            )

    def listar_para_reconciliar(
        self, estados: tuple[str, ...], dias: int, limite: int
    ) -> list[str]:
        """Ids de las propuestas en `estados` que cambiaron en los últimos `dias`, las más
        antiguas primero, hasta `limite`. Es la lista de trabajo del barrido de reconciliación."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                "SELECT id FROM propuestas WHERE estado = ANY(%(estados)s) "
                "AND actualizado_en >= now() - make_interval(days => %(dias)s) "
                "ORDER BY actualizado_en ASC LIMIT %(limite)s",
                {"estados": list(estados), "dias": dias, "limite": limite},
            ).fetchall()
        return [fila["id"] for fila in filas]

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool:
        """Cambia el estado solo si sigue en uno de `desde`: dos emisiones a la vez no se pisan.
        Devuelve si cambió."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE propuestas SET estado = %(nuevo)s, motivo_estado = %(motivo)s, "
                "actualizado_en = now() WHERE id = %(id)s AND estado = ANY(%(desde)s) RETURNING id",
                {"id": propuesta_id, "nuevo": nuevo, "motivo": motivo, "desde": list(desde)},
            ).fetchone()
        return fila is not None

    def emitir_poliza(
        self,
        propuesta_id: str,
        cliente_id: str,
        vigencia_desde: Any,
        vigencia_hasta: Any,
        desde: tuple[str, ...] = ("EN_EMISION", "PENDIENTE_PAGO"),
    ) -> tuple[dict[str, Any], bool]:
        """Pasa la propuesta a `EMITIDA` y crea su póliza, todo en una transacción. Devuelve la
        póliza y si se creó ahora (False = ya existía: emitir dos veces no
        duplica). Solo emite si la propuesta sigue en uno de `desde`; por defecto, los de la
        emisión normal: un `PAGO_RECHAZADO` no se emite por ahí, solo lo reabre la reconciliación
        (que lo pide explícitamente). Si ya no está en un estado emisible (p. ej. otra emisión la
        movió), devuelve la póliza existente o `None`."""
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                "UPDATE propuestas SET estado = 'EMITIDA', motivo_estado = NULL, "
                "actualizado_en = now() WHERE id = %(id)s "
                "AND estado = ANY(%(desde)s) RETURNING id",
                {"id": propuesta_id, "desde": list(desde)},
            ).fetchone()
            if fila is None:
                existente = conexion.execute(
                    f"SELECT {_COLUMNAS_POLIZA} FROM polizas WHERE propuesta_id = %(id)s",
                    {"id": propuesta_id},
                ).fetchone()
                return existente, False
            poliza = conexion.execute(
                f"""
                INSERT INTO polizas
                    (id, propuesta_id, numero, cliente_id, vigencia_desde, vigencia_hasta)
                VALUES
                    (%(id)s, %(propuesta_id)s, nextval('polizas_numero_seq'), %(cliente_id)s,
                     %(vigencia_desde)s, %(vigencia_hasta)s)
                RETURNING {_COLUMNAS_POLIZA}
                """,
                {
                    "id": str(uuid.uuid4()),
                    "propuesta_id": propuesta_id,
                    "cliente_id": cliente_id,
                    "vigencia_desde": vigencia_desde,
                    "vigencia_hasta": vigencia_hasta,
                },
            ).fetchone()
            self._causar_comision(conexion, propuesta_id, poliza["id"])
        return poliza, True

    def _causar_comision(self, conexion: Any, propuesta_id: str, poliza_id: str) -> None:
        """W07-7: si la vendió un socio, su comisión, en la transacción de la emisión. `poliza_id`
        es único en `comisiones`: una segunda emisión no la duplica."""
        fila = conexion.execute(
            "SELECT id, actor_rol, actor_usuario_id, tasa_comision, oferta_cifrada "
            "FROM propuestas WHERE id = %(id)s",
            {"id": propuesta_id},
        ).fetchone()
        if fila["actor_rol"] != "SOCIO" or fila["tasa_comision"] is None:
            return
        prima = json.loads(self._cifrador.descifrar(fila["oferta_cifrada"], fila["id"]))["prima"]
        comision_id = str(uuid.uuid4())
        montos = {
            "prima": prima["valor"],
            "comision": calcular_comision(prima["valor"], fila["tasa_comision"]),
        }
        conexion.execute(
            """
            INSERT INTO comisiones
                (id, socio_id, poliza_id, propuesta_id, tasa, moneda, montos_cifrados)
            VALUES (%(id)s, %(socio)s, %(poliza)s, %(propuesta)s, %(tasa)s, %(moneda)s, %(montos)s)
            ON CONFLICT (poliza_id) DO NOTHING
            """,
            {
                "id": comision_id,
                "socio": fila["actor_usuario_id"],
                "poliza": poliza_id,
                "propuesta": propuesta_id,
                "tasa": fila["tasa_comision"],
                "moneda": prima["moneda"],
                "montos": self._cifrador.cifrar(json.dumps(montos), comision_id),
            },
        )

    def propuestas_de_socio_por_mes(
        self, socio_id: str, desde: date, hasta: date
    ) -> dict[str, int]:
        """Cuántas propuestas aceptó el socio cada mes (W07, `GET /socios/{id}/ventas`)."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                """
                SELECT date_trunc('month', creado_en AT TIME ZONE 'UTC')::date AS mes, count(*) AS n
                FROM propuestas
                WHERE actor_rol = 'SOCIO' AND actor_usuario_id = %(socio)s
                  AND creado_en >= %(desde)s AND creado_en < %(hasta)s::date + 1
                GROUP BY 1
                """,
                {"socio": socio_id, "desde": desde, "hasta": hasta},
            ).fetchall()
        return {mes(f["mes"]): f["n"] for f in filas}

    def comisiones_de_socio(self, socio_id: str, desde: date, hasta: date) -> list[dict[str, Any]]:
        """Las comisiones causadas en el rango, con la prima y la comisión descifradas."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                """
                SELECT id, poliza_id, tasa, moneda, montos_cifrados, causada_en FROM comisiones
                WHERE socio_id = %(socio)s
                  AND causada_en >= %(desde)s AND causada_en < %(hasta)s::date + 1
                ORDER BY causada_en
                """,
                {"socio": socio_id, "desde": desde, "hasta": hasta},
            ).fetchall()
        resultado = []
        for fila in filas:
            montos = json.loads(self._cifrador.descifrar(fila["montos_cifrados"], fila["id"]))
            resultado.append(
                {
                    "poliza_id": fila["poliza_id"],
                    "tasa": Decimal(fila["tasa"]),
                    "moneda": fila["moneda"],
                    "causada_en": fila["causada_en"],
                    **montos,
                }
            )
        return resultado

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con la prima, la cobertura y el dueño de su propuesta."""
        with self._pool.connection() as conexion:
            poliza = conexion.execute(
                f"SELECT {_COLUMNAS_POLIZA} FROM polizas WHERE id = %(id)s", {"id": poliza_id}
            ).fetchone()
        if poliza is None:
            return None
        propuesta = self.obtener(poliza["propuesta_id"])
        evidencia = self.obtener_evidencia(poliza["propuesta_id"])
        return {
            **poliza,
            "prima": propuesta["prima"],
            "cobertura": propuesta["cobertura"],
            "plazo_meses": propuesta["plazo_meses"],
            "actor_usuario_id": propuesta["actor_usuario_id"],
            # Quien firmó es el CLIENTE titular: a él se le avisa y de él es el nombre del
            # documento.
            "firmante_id": evidencia["firmante_id"] if evidencia else None,
        }

    def marcar_notificada(self, poliza_id: str) -> None:
        """Registra que ya se avisó al cliente. Solo la primera vez: no pisa una fecha anterior."""
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE polizas SET notificada_en = now() "
                "WHERE id = %(id)s AND notificada_en IS NULL",
                {"id": poliza_id},
            )

    def marcar_aviso_omitido(self, poliza_id: str, motivo: str) -> None:
        """Registra que al cliente **no se le va a avisar** y por qué (W03-18). Solo si todavía no
        se avisó ni se omitió: no pisa un resultado anterior."""
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE polizas SET aviso_omitido_motivo = %(motivo)s "
                "WHERE id = %(id)s AND notificada_en IS NULL AND aviso_omitido_motivo IS NULL",
                {"id": poliza_id, "motivo": motivo},
            )

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS_EVIDENCIA} FROM evidencias_firma "
                "WHERE propuesta_id = %(propuesta_id)s",
                {"propuesta_id": propuesta_id},
            ).fetchone()
        return fila

    def registrar_firma(self, propuesta_id: str, evidencia: dict[str, Any]) -> tuple[dict, bool]:
        """Guarda la evidencia (una sola vez por propuesta) y pasa la propuesta a `EN_EMISION`,
        todo en una transacción: nunca queda firmada sin evidencia ni con evidencia sin estado.
        Devuelve la evidencia y si se creó ahora (False = otra petición ya la había guardado)."""
        documento = self._cifrador.cifrar(evidencia["documento"], evidencia["id"])
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                f"""
                INSERT INTO evidencias_firma
                    (id, propuesta_id, documento_hash, version_documento, documento_cifrado,
                     firmante_id, consentimiento_id, referencia_proveedor, firmada_en)
                VALUES
                    (%(id)s, %(propuesta_id)s, %(documento_hash)s, %(version_documento)s,
                     %(documento_cifrado)s, %(firmante_id)s, %(consentimiento_id)s,
                     %(referencia_proveedor)s, %(firmada_en)s)
                ON CONFLICT (propuesta_id) DO NOTHING
                RETURNING {_COLUMNAS_EVIDENCIA}
                """,
                {
                    **{k: v for k, v in evidencia.items() if k != "documento"},
                    "propuesta_id": propuesta_id,
                    "documento_cifrado": documento,
                },
            ).fetchone()
            creada = fila is not None
            if creada:
                conexion.execute(
                    "UPDATE propuestas SET estado = 'EN_EMISION', actualizado_en = now() "
                    "WHERE id = %(id)s AND estado = 'ACEPTADA'",
                    {"id": propuesta_id},
                )
            else:
                fila = conexion.execute(
                    f"SELECT {_COLUMNAS_EVIDENCIA} FROM evidencias_firma "
                    "WHERE propuesta_id = %(propuesta_id)s",
                    {"propuesta_id": propuesta_id},
                ).fetchone()
        return fila, creada

    # ------------------------------------------------------------------ bloque 9 (W03-19 a W03-37)

    def _cifrar_opcional(self, valor: str | None, contexto: str) -> str | None:
        return self._cifrador.cifrar(valor, contexto) if valor else None

    def _descifrar_opcional(self, valor: str | None, contexto: str) -> str | None:
        return self._cifrador.descifrar(valor, contexto) if valor else None

    def contacto(self, propuesta_id: str) -> dict[str, str | None] | None:
        """El nombre, el correo y el texto de la firma en claro (datos personales; nunca se
        loguean). `None` si la propuesta no existe."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "SELECT nombre_cifrado, correo_cifrado, firma_texto_cifrado FROM propuestas "
                "WHERE id = %(id)s",
                {"id": propuesta_id},
            ).fetchone()
        if fila is None:
            return None
        return {
            "nombre": self._descifrar_opcional(fila["nombre_cifrado"], f"nombre:{propuesta_id}"),
            "correo": self._descifrar_opcional(fila["correo_cifrado"], f"correo:{propuesta_id}"),
            "firma_texto": self._descifrar_opcional(
                fila["firma_texto_cifrado"], f"firma:{propuesta_id}"
            ),
        }

    def actualizar_correo(self, propuesta_id: str, correo: str) -> None:
        """El ASESOR corrige el correo en el modal (W03-25)."""
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE propuestas SET correo_cifrado = %(c)s, actualizado_en = now() "
                "WHERE id = %(id)s AND contacto_borrado_en IS NULL",
                {"id": propuesta_id, "c": self._cifrador.cifrar(correo, f"correo:{propuesta_id}")},
            )

    def crear_verificacion(self, propuesta_id: str, huella: str, vence_en: datetime) -> None:
        """Un código nuevo anula los anteriores sin verificar (W03-20)."""
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                "UPDATE verificaciones_identidad SET anulada_en = now() "
                "WHERE propuesta_id = %(p)s AND anulada_en IS NULL AND verificada_en IS NULL",
                {"p": propuesta_id},
            )
            conexion.execute(
                "INSERT INTO verificaciones_identidad (id, propuesta_id, huella, vence_en) "
                "VALUES (%(id)s, %(p)s, %(h)s, %(v)s)",
                {"id": str(uuid.uuid4()), "p": propuesta_id, "h": huella, "v": vence_en},
            )

    def verificacion_vigente(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                "SELECT id, huella, vence_en, intentos, verificada_en "
                "FROM verificaciones_identidad "
                "WHERE propuesta_id = %(p)s AND anulada_en IS NULL "
                "ORDER BY creado_en DESC LIMIT 1",
                {"p": propuesta_id},
            ).fetchone()

    def sumar_intento_codigo(self, verificacion_id: str) -> int:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE verificaciones_identidad SET intentos = intentos + 1 "
                "WHERE id = %(id)s RETURNING intentos",
                {"id": verificacion_id},
            ).fetchone()
        return fila["intentos"]

    def marcar_verificada(self, verificacion_id: str, propuesta_id: str) -> bool:
        """Marca el código y pasa la propuesta a IDENTIDAD_VERIFICADA, en una transacción."""
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                "UPDATE verificaciones_identidad SET verificada_en = now() WHERE id = %(id)s",
                {"id": verificacion_id},
            )
            fila = conexion.execute(
                "UPDATE propuestas SET estado = 'IDENTIDAD_VERIFICADA', motivo_estado = NULL, "
                "actualizado_en = now() WHERE id = %(id)s AND estado = 'ACEPTADA' RETURNING id",
                {"id": propuesta_id},
            ).fetchone()
        return fila is not None

    def crear_enlace(self, propuesta_id: str, huella: str, vence_en: datetime) -> None:
        """Un enlace nuevo anula el anterior (W03-22)."""
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                "UPDATE enlaces_firma SET anulado_en = now() "
                "WHERE propuesta_id = %(p)s AND anulado_en IS NULL",
                {"p": propuesta_id},
            )
            conexion.execute(
                "INSERT INTO enlaces_firma (id, propuesta_id, huella, vence_en) "
                "VALUES (%(id)s, %(p)s, %(h)s, %(v)s)",
                {"id": str(uuid.uuid4()), "p": propuesta_id, "h": huella, "v": vence_en},
            )

    def enlace_por_huella(self, huella: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                "SELECT id, propuesta_id, vence_en, anulado_en, creado_en FROM enlaces_firma "
                "WHERE huella = %(h)s",
                {"h": huella},
            ).fetchone()

    def enlace_activo(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                "SELECT id, propuesta_id, vence_en, anulado_en, creado_en FROM enlaces_firma "
                "WHERE propuesta_id = %(p)s AND anulado_en IS NULL "
                "ORDER BY creado_en DESC LIMIT 1",
                {"p": propuesta_id},
            ).fetchone()

    def registrar_firma_enlace(
        self, propuesta_id: str, evidencia: dict[str, Any], firma_texto: str | None
    ) -> tuple[dict, bool]:
        """Como `registrar_firma`, pero del flujo nuevo: guarda la evidencia (con el modo y el id de
        la imagen) y el texto del modo «nombre», y pasa la propuesta de EN_FIRMA a FIRMADA, todo en
        una transacción."""
        documento = self._cifrador.cifrar(evidencia["documento"], evidencia["id"])
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                f"""
                INSERT INTO evidencias_firma
                    (id, propuesta_id, documento_hash, version_documento, documento_cifrado,
                     firmante_id, consentimiento_id, referencia_proveedor, firmada_en,
                     modo_firma, imagen_firma_archivo_id)
                VALUES
                    (%(id)s, %(propuesta_id)s, %(documento_hash)s, %(version_documento)s,
                     %(documento_cifrado)s, %(firmante_id)s, %(consentimiento_id)s,
                     %(referencia_proveedor)s, %(firmada_en)s, %(modo_firma)s,
                     %(imagen_firma_archivo_id)s)
                ON CONFLICT (propuesta_id) DO NOTHING
                RETURNING {_COLUMNAS_EVIDENCIA}
                """,
                {
                    **{k: v for k, v in evidencia.items() if k != "documento"},
                    "propuesta_id": propuesta_id,
                    "documento_cifrado": documento,
                },
            ).fetchone()
            creada = fila is not None
            if creada:
                conexion.execute(
                    "UPDATE propuestas SET estado = 'FIRMADA', motivo_estado = NULL, "
                    "firma_texto_cifrado = %(texto)s, actualizado_en = now() "
                    "WHERE id = %(id)s AND estado = 'EN_FIRMA'",
                    {
                        "id": propuesta_id,
                        "texto": self._cifrar_opcional(firma_texto, f"firma:{propuesta_id}"),
                    },
                )
            else:
                fila = conexion.execute(
                    f"SELECT {_COLUMNAS_EVIDENCIA} FROM evidencias_firma "
                    "WHERE propuesta_id = %(propuesta_id)s",
                    {"propuesta_id": propuesta_id},
                ).fetchone()
        return fila, creada

    def cancelar_y_borrar_contacto(self, propuesta_id: str) -> str | None:
        """W03-26: la propuesta que no se emitió pasa a CANCELADA (si sigue abierta), se borran el
        nombre, el correo y el texto de la firma, y se anulan sus enlaces. Devuelve el id de la
        imagen de la firma, si la hay, para pedir su retención. Idempotente. Una póliza emitida no
        se toca: el contacto se conserva por el contrato."""
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                """
                UPDATE propuestas SET
                    estado = CASE WHEN estado = ANY(%(abiertos)s) THEN 'CANCELADA' ELSE estado END,
                    motivo_estado = CASE WHEN estado = ANY(%(abiertos)s)
                                         THEN 'CONSENTIMIENTO_NO_VIGENTE' ELSE motivo_estado END,
                    nombre_cifrado = NULL, correo_cifrado = NULL, firma_texto_cifrado = NULL,
                    contacto_borrado_en = COALESCE(contacto_borrado_en, now()),
                    actualizado_en = now()
                WHERE id = %(id)s AND estado <> 'EMITIDA'
                RETURNING id
                """,
                {"id": propuesta_id, "abiertos": list(ESTADOS_ABIERTOS)},
            ).fetchone()
            if fila is None:
                return None
            conexion.execute(
                "UPDATE enlaces_firma SET anulado_en = now() "
                "WHERE propuesta_id = %(p)s AND anulado_en IS NULL",
                {"p": propuesta_id},
            )
            evidencia = conexion.execute(
                "SELECT imagen_firma_archivo_id FROM evidencias_firma WHERE propuesta_id = %(p)s",
                {"p": propuesta_id},
            ).fetchone()
        return evidencia["imagen_firma_archivo_id"] if evidencia else None

    def propuestas_del_consentimiento(self, consentimiento_id: str, titular_id: str) -> list[str]:
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                "SELECT id FROM propuestas WHERE consentimiento_id = %(c)s AND cliente_id = %(t)s "
                "AND estado <> 'EMITIDA'",
                {"c": consentimiento_id, "t": titular_id},
            ).fetchall()
        return [f["id"] for f in filas]

    def candidatas_a_purga(self, limite: int) -> list[dict[str, Any]]:
        """Propuestas sin emitir que todavía guardan contacto, firma o enlace: la purga diaria les
        pregunta a AUTH si su consentimiento sigue vigente. Las más antiguas primero."""
        with self._pool.connection() as conexion:
            return conexion.execute(
                """
                SELECT id, consentimiento_id FROM propuestas
                WHERE estado <> 'EMITIDA' AND contacto_borrado_en IS NULL
                  AND consentimiento_id IS NOT NULL
                ORDER BY creado_en ASC LIMIT %(limite)s
                """,
                {"limite": limite},
            ).fetchall()

    def listar(
        self,
        cliente_id: str | None,
        actor_usuario_id: str | None,
        estado_visible: str | None,
        pagina: int,
        tamano: int,
        actor_rol: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Las propuestas del titular, las que aceptó un usuario o todas (W03-35), de la más
        reciente a la más antigua, con su póliza si existe. Quién ve qué lo decide el BFF por el
        rol del token: aquí solo se filtra."""
        estados = list(ESTADOS_POR_VISIBLE[estado_visible]) if estado_visible else None
        filtro = """
            FROM propuestas
            WHERE (%(cliente)s::text IS NULL OR cliente_id = %(cliente)s)
              AND (%(actor)s::text IS NULL OR actor_usuario_id = %(actor)s)
              AND (%(rol)s::text IS NULL OR actor_rol = %(rol)s)
              AND (%(estados)s::text[] IS NULL OR estado = ANY(%(estados)s))
        """
        parametros = {
            "cliente": cliente_id,
            "actor": actor_usuario_id,
            "rol": actor_rol,
            "estados": estados,
            "limite": tamano,
            "desde": (pagina - 1) * tamano,
        }
        with self._pool.connection() as conexion:
            total = conexion.execute(f"SELECT count(*) AS n {filtro}", parametros).fetchone()["n"]
            filas = conexion.execute(
                f"SELECT {_COLUMNAS}, {_POLIZA_ID}, nombre_cifrado {filtro} "
                "ORDER BY creado_en DESC LIMIT %(limite)s OFFSET %(desde)s",
                parametros,
            ).fetchall()
        propuestas = []
        for fila in filas:
            propuesta = self._a_propuesta(fila)
            propuesta["nombre_titular"] = self._descifrar_opcional(
                fila["nombre_cifrado"], f"nombre:{fila['id']}"
            )
            propuestas.append(propuesta)
        return propuestas, total

    def polizas_resumen(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """La póliza (número y vigencia) de cada id pedido, para las listas."""
        if not ids:
            return {}
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"SELECT {_COLUMNAS_POLIZA} FROM polizas WHERE id = ANY(%(ids)s)", {"ids": ids}
            ).fetchall()
        return {f["id"]: f for f in filas}


def construir_pool(database_url: str) -> ConnectionPool:
    if not database_url:
        raise ValueError("Falta DATABASE_URL")
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
