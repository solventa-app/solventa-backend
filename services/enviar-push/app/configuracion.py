"""Configuración por variables de entorno del servicio `enviar-push` (KAN-259).

Sin secretos en el código: la llave de la cuenta de servicio de AWS no existe (D7, federación de
identidad); lo único sensible es el ARN de la Platform Application de SNS, que llega por entorno
(Secret Manager en la nube, variable simple en local/CI)."""

import os
from dataclasses import dataclass

MODOS_SNS = ("stub", "real")


@dataclass(frozen=True)
class Configuracion:
    # AUTH resuelve el destino del push y marca un token inválido; llamada privada (OIDC).
    auth_url: str
    autenticacion_servicios: str

    # "stub" (local, CI, demo sin AWS real) o "real" (boto3 + SNS). Ver app/sns.py.
    sns_modo: str
    sns_region: str
    # Platform Application de SNS ya configurada para FCM (paso manual de felipe, ver el plan).
    sns_platform_application_arn: str
    # D7: GCP -> AWS por federación de identidad (`AssumeRoleWithWebIdentity`), sin llaves
    # guardadas. Vacío en local/CI: boto3 no se usa (sns_modo=stub) o usa credenciales de gcloud.
    aws_role_arn: str

    # Si está vacío, los eventos entrantes se procesan igual pero `POST /v1/push` no publica en
    # Pub/Sub real: queda en el log (mismo patrón que `BusEventosLog` de RISK/CLAIMS).
    pubsub_tema_notificaciones_push: str

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        modo = os.getenv("SNS_MODO", "stub").strip().lower()
        if modo not in MODOS_SNS:
            raise ValueError(f"SNS_MODO desconocido: {modo}")
        return cls(
            auth_url=os.getenv("AUTH_URL", "http://localhost:8001"),
            autenticacion_servicios=os.getenv("AUTENTICACION_SERVICIOS", "ninguna"),
            sns_modo=modo,
            sns_region=os.getenv("SNS_REGION", "us-east-1"),
            sns_platform_application_arn=os.getenv("SNS_PLATFORM_APPLICATION_ARN", ""),
            aws_role_arn=os.getenv("AWS_ROLE_ARN", ""),
            pubsub_tema_notificaciones_push=os.getenv(
                "PUBSUB_TEMA_NOTIFICACIONES_PUSH", ""
            ).strip(),
        )
