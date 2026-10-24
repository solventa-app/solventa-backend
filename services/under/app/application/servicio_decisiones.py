"""Decidir la suscripción: KYC por el ACL Worker y reglas (HU-W03, KAN-213).

No guarda nada (UNDER no tiene almacén): decide y responde. Quien la usa (POLICY) guarda el
resultado en la propuesta. Como el KYC es idempotente por `id_verificacion` (el id de la propuesta),
decidir dos veces la misma propuesta da la misma decisión."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.application.cliente_acl import PuertoClienteAcl, ResultadoKyc
from app.application.identificadores import id_opaco
from app.application.reglas import Decision, decidir

logger = logging.getLogger("under.decisiones")


@dataclass
class ResultadoDecision:
    decision: Decision
    kyc: ResultadoKyc


class ServicioDecisiones:
    def __init__(self, acl: PuertoClienteAcl, cobertura_maxima: Decimal) -> None:
        self._acl = acl
        self._cobertura_maxima = cobertura_maxima

    def decidir(
        self,
        propuesta_id: str,
        cliente_id: str,
        nivel_riesgo: str | None,
        cobertura: Decimal,
        moneda: str,
    ) -> ResultadoDecision:
        kyc = self._acl.verificar_kyc(propuesta_id, cliente_id)
        decision = decidir(kyc.estado, nivel_riesgo, cobertura, moneda, self._cobertura_maxima)
        logger.info(
            "decisión %s motivos=%s propuesta=%s cliente=%s",
            decision.decision,
            ",".join(decision.motivos) or "-",
            id_opaco(propuesta_id),
            id_opaco(cliente_id),
        )
        return ResultadoDecision(decision, kyc)
