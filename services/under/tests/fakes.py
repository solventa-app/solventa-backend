"""Doble del ACL Worker para las pruebas de UNDER (sin red)."""

from app.application.cliente_acl import ResultadoKyc


class AclFalso:
    def __init__(self, resultado: ResultadoKyc | None = None) -> None:
        self.resultado = resultado or ResultadoKyc(estado="aprobada", referencia="kyc-1")
        self.llamadas: list[tuple[str, str]] = []

    def verificar_kyc(self, id_verificacion: str, cliente_id: str) -> ResultadoKyc:
        self.llamadas.append((id_verificacion, cliente_id))
        return self.resultado
