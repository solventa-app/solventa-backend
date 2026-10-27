"""Los bytes viven en Cloud Storage; este servicio solo firma URLs y mira los objetos (KAN-250).

Nadie sube ni descarga a través de Cloud Run: el gateway corta a 0,7 s y una foto o un video no
caben ahí. El cliente sube con un `PUT` directo a una **URL firmada V4** de pocos minutos, que
fija el tipo de contenido, el rango de tamaño (`x-goog-content-length-range`) y que el objeto no
exista (`x-goog-if-generation-match: 0`: un archivo no se puede sobrescribir).

Dos firmantes, el mismo código:
- `iam` (Cloud Run): firma con la cuenta del servicio a través de `signBlob` de IAM
  (`roles/iam.serviceAccountTokenCreator` sobre sí misma). No hay llaves privadas en ningún lado.
- `local` (compose y CI): una llave RSA de prueba generada al arrancar, contra **fake-gcs-server**.
  El emulador no revisa la firma, ni el rango de tamaño, ni emite `OBJECT_FINALIZE`: por eso el
  servicio vuelve a medir todo al confirmar, y la firma real solo se prueba en staging."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger("almacenamiento.objetos")

CUENTA_LOCAL = "firmante-local@solventa.invalid"


class PuertoAlmacen(Protocol):
    def url_subida(
        self, objeto: str, tipo_contenido: str, tamano_max: int, vigencia_s: int
    ) -> tuple[str, dict[str, str]]: ...

    def url_descarga(self, objeto: str, vigencia_s: int) -> str: ...

    def metadatos(self, objeto: str) -> dict[str, Any] | None: ...

    def primeros_bytes(self, objeto: str, cantidad: int) -> bytes: ...

    def borrar(self, objeto: str) -> None: ...

    def fijar_custom_time(self, objeto: str, momento: datetime) -> None: ...


def _credenciales_locales() -> Any:
    """Una cuenta de servicio de mentira con una llave RSA nueva: solo para firmar contra el
    emulador. Se genera al arrancar y nunca se guarda (el repo es público)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt
    from google.oauth2 import service_account

    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = llave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return service_account.Credentials(
        signer=crypt.RSASigner.from_string(pem),
        service_account_email=CUENTA_LOCAL,
        token_uri="https://oauth2.googleapis.com/token",
    )


class AlmacenGcs:
    def __init__(
        self,
        bucket: str,
        firmante: str,
        url_publica: str | None = None,
        cliente: Any | None = None,
    ) -> None:
        if firmante not in ("iam", "local"):
            raise ValueError("FIRMANTE debe ser 'iam' o 'local'")
        from google.cloud import storage

        self._firmante = firmante
        self._url_publica = url_publica
        if cliente is None:
            if firmante == "local":
                from google.auth.credentials import AnonymousCredentials

                # Con STORAGE_EMULATOR_HOST la librería habla con fake-gcs-server.
                cliente = storage.Client(project="local", credentials=AnonymousCredentials())
            else:
                cliente = storage.Client()
        self._cliente = cliente
        self._bucket = cliente.bucket(bucket)
        self._credenciales_locales = _credenciales_locales() if firmante == "local" else None
        self._credenciales_iam: Any | None = None

    def asegurar_bucket(self) -> None:
        """Solo en local: crea el bucket del emulador si no existe (en la nube, Terraform)."""
        if self._firmante == "local" and self._cliente.lookup_bucket(self._bucket.name) is None:
            self._cliente.create_bucket(self._bucket.name)
            logger.info("bucket local creado en el emulador")

    def _firma(self) -> dict[str, Any]:
        if self._firmante == "local":
            argumentos: dict[str, Any] = {"credentials": self._credenciales_locales}
            if self._url_publica:
                argumentos["api_access_endpoint"] = self._url_publica
            return argumentos
        import google.auth
        from google.auth.transport.requests import Request

        if self._credenciales_iam is None:
            self._credenciales_iam, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not self._credenciales_iam.valid:
            self._credenciales_iam.refresh(Request())
        # Sin llave privada: la librería firma con `signBlob` de IAM usando este token.
        return {
            "service_account_email": self._credenciales_iam.service_account_email,
            "access_token": self._credenciales_iam.token,
        }

    def url_subida(
        self, objeto: str, tipo_contenido: str, tamano_max: int, vigencia_s: int
    ) -> tuple[str, dict[str, str]]:
        encabezados = {
            "Content-Type": tipo_contenido,
            "x-goog-content-length-range": f"0,{tamano_max}",
            "x-goog-if-generation-match": "0",
        }
        url = self._bucket.blob(objeto).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=vigencia_s),
            method="PUT",
            content_type=tipo_contenido,
            headers={k: v for k, v in encabezados.items() if k != "Content-Type"},
            **self._firma(),
        )
        return url, encabezados

    def url_descarga(self, objeto: str, vigencia_s: int) -> str:
        return self._bucket.blob(objeto).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=vigencia_s),
            method="GET",
            **self._firma(),
        )

    def metadatos(self, objeto: str) -> dict[str, Any] | None:
        blob = self._bucket.get_blob(objeto)
        if blob is None:
            return None
        return {
            "tamano_bytes": int(blob.size or 0),
            "tipo_contenido": blob.content_type or "",
            "crc32c": blob.crc32c,
            "generacion": str(blob.generation) if blob.generation is not None else None,
        }

    def primeros_bytes(self, objeto: str, cantidad: int) -> bytes:
        # Lectura por rango: no se baja el video entero para mirar su firma.
        return self._bucket.blob(objeto).download_as_bytes(start=0, end=cantidad - 1)

    def borrar(self, objeto: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(objeto).delete()
        except NotFound:
            pass  # ya no estaba: borrar es idempotente

    def fijar_custom_time(self, objeto: str, momento: datetime) -> None:
        """Desde este momento corre la retención: la regla de ciclo de vida del bucket borra el
        objeto `retencion_dias` después (`daysSinceCustomTime`)."""
        blob = self._bucket.blob(objeto)
        blob.custom_time = momento.astimezone(UTC)
        blob.patch()
