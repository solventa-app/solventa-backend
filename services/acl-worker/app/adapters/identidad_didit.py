"""Adaptador de verificación de identidad contra Didit (HU-M01, KAN-31). Implementa
`PuertoVerificacionIdentidad`.

Usa las tres APIs independientes de Didit v3 (https://docs.didit.me/standalone-apis): lectura y
autenticidad del documento (`/v3/id-verification/`), prueba de vida pasiva sobre el fotograma del
rostro (`/v3/passive-liveness/`) y comparación del rostro con la foto del documento
(`/v3/face-match/`). Autentica con `x-api-key`. En local y en el CI apunta a `stubs/kyc`, que imita
esas mismas rutas y formas.

Ningún dato sale del ACL con la forma de Didit: la respuesta se traduce a `ResultadoIdentidad`.
`save_api_request=false` para que Didit no guarde la sesión ni enrole el rostro en su índice de
búsqueda (minimización, ley 1581). `vendor_data` lleva solo la referencia opaca del onboarding.

Didit responde HTTP 200 aunque rechace (documento vencido, suplantación, rostro distinto): el
resultado sale de `status` y `warnings`, no del código HTTP."""

import httpx

from app.domain.puertos import DatosDocumento, ImagenesIdentidad, ResultadoIdentidad

APROBADO = "Approved"
RIESGO_ERROR = "error"

MOTIVO_DOCUMENTO = "documento_rechazado"
MOTIVO_VIDA = "prueba_de_vida_fallida"
MOTIVO_ROSTRO = "rostro_no_coincide"


def _alertas_de_error(bloque: dict) -> list[str]:
    return [
        str(alerta["risk"])
        for alerta in bloque.get("warnings") or []
        if alerta.get("log_type") == RIESGO_ERROR and alerta.get("risk")
    ]


class AdaptadorIdentidadDidit:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float,
        umbral_vida: float = 30,
        umbral_rostro: float = 50,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._umbral_vida = umbral_vida
        self._umbral_rostro = umbral_rostro
        self._cliente = cliente or httpx.Client(
            base_url=base_url.rstrip("/"), headers={"x-api-key": api_key}, auth=auth
        )

    def _enviar(self, ruta: str, archivos: dict, campos: dict, timeout: float) -> dict:
        respuesta = self._cliente.post(
            ruta,
            files={nombre: (nombre + ".jpg", contenido) for nombre, contenido in archivos.items()},
            data={"save_api_request": "false", **campos},
            timeout=timeout,
        )
        respuesta.raise_for_status()
        return respuesta.json()

    def verificar_identidad(
        self, referencia: str, imagenes: ImagenesIdentidad, timeout_s: float | None = None
    ) -> ResultadoIdentidad:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        documento_archivos = {"front_image": imagenes.anverso}
        if imagenes.reverso is not None:
            documento_archivos["back_image"] = imagenes.reverso
        documento = self._enviar(
            "/v3/id-verification/",
            documento_archivos,
            {"vendor_data": referencia},
            timeout,
        )["id_verification"]
        vida = self._enviar(
            "/v3/passive-liveness/",
            {"user_image": imagenes.rostro},
            {
                "vendor_data": referencia,
                "face_liveness_score_decline_threshold": str(self._umbral_vida),
            },
            timeout,
        )["liveness"]
        rostro = self._enviar(
            "/v3/face-match/",
            {"user_image": imagenes.rostro, "ref_image": imagenes.anverso},
            {
                "vendor_data": referencia,
                "face_match_score_decline_threshold": str(self._umbral_rostro),
            },
            timeout,
        )["face_match"]
        return self._traducir(documento, vida, rostro)

    @staticmethod
    def _traducir(documento: dict, vida: dict, rostro: dict) -> ResultadoIdentidad:
        documento_ok = documento.get("status") == APROBADO
        vida_ok = vida.get("status") == APROBADO
        rostro_ok = rostro.get("status") == APROBADO
        motivo = None
        if not documento_ok:
            motivo = MOTIVO_DOCUMENTO
        elif not vida_ok:
            motivo = MOTIVO_VIDA
        elif not rostro_ok:
            motivo = MOTIVO_ROSTRO
        alertas = tuple(
            dict.fromkeys(
                _alertas_de_error(documento) + _alertas_de_error(vida) + _alertas_de_error(rostro)
            )
        )
        return ResultadoIdentidad(
            estado="aprobada" if motivo is None else "rechazada",
            motivo=motivo,
            datos=DatosDocumento(
                nombres=documento.get("first_name"),
                apellidos=documento.get("last_name"),
                numero=documento.get("document_number"),
                fecha_nacimiento=documento.get("date_of_birth"),
                pais=documento.get("issuing_state"),
                tipo=documento.get("document_type"),
            ),
            documento_autentico=documento_ok,
            coincide_rostro=rostro_ok,
            puntaje_rostro=rostro.get("score"),
            vivo=vida_ok,
            puntaje_vida=vida.get("score"),
            alertas=alertas,
        )

    def sondear(self) -> None:
        """Didit no tiene un endpoint de estado gratuito: la sonda solo confirma que el host
        responde (cualquier respuesta HTTP, incluso 403/404, cuenta como vivo). Nunca crea una
        verificación, que se cobra."""
        self._cliente.get("/", timeout=self._timeout_s)

    def cerrar(self) -> None:
        self._cliente.close()
