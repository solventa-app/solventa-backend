"""Dobles de prueba del BFF: en memoria, sin levantar AUTH, ACL Worker, RISK ni RATING."""

from app.clientes import Clientes, ErrorServicio

CONSENTIMIENTO_VIGENTE = {
    "id": "c-1",
    "finalidad": "cotizacion-vida-hipotecario",
    "version_texto": "v1",
    "otorgado_en": "2026-10-08T12:00:00+00:00",
    "revocado_en": None,
    "vigente": True,
}

# Misma forma que devuelve `POST /ofertas` de RATING
# (services/rating/app/application/servicio_oferta.py).
OFERTA_COMPLETA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "desgloseScore": [{"nombre": "historial-crediticio", "peso": 0.3}],
    "fuentes": [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "OK",
            "capturadoEn": None,
        }
    ],
    "prima": {"valor": "61200.00", "moneda": "COP"},
    "rangoPrima": None,
}

OFERTA_PRELIMINAR = {
    **OFERTA_COMPLETA,
    "tipo": "PRELIMINAR",
    "prima": None,
    "rangoPrima": {
        "minima": {"valor": "48960.00", "moneda": "COP"},
        "maxima": {"valor": "73440.00", "moneda": "COP"},
    },
    "fuentes": [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "CACHE",
            "capturadoEn": "2026-10-08T11:55:00+00:00",
        }
    ],
}


class AuthFalso:
    def __init__(self, consentimientos: dict | None = None, falla: bool = False) -> None:
        self.consentimientos = dict(
            {"c-1": CONSENTIMIENTO_VIGENTE} if consentimientos is None else consentimientos
        )
        self.falla = falla

    async def registrar_consentimiento(self, finalidad: str, version_texto: str) -> dict:
        if self.falla:
            raise ErrorServicio("caido")
        nuevo = {**CONSENTIMIENTO_VIGENTE, "id": "c-nuevo", "finalidad": finalidad}
        nuevo["version_texto"] = version_texto
        self.consentimientos[nuevo["id"]] = nuevo
        return nuevo

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        return self.consentimientos.get(consentimiento_id)

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None:
        if self.falla:
            raise ErrorServicio("caido")
        actual = self.consentimientos.get(consentimiento_id)
        if actual is None:
            return None
        revocado = {**actual, "revocado_en": "2026-10-08T13:00:00+00:00", "vigente": False}
        self.consentimientos[consentimiento_id] = revocado
        return revocado


class AclFalso:
    def __init__(self, fallan: set[str] | None = None) -> None:
        self.fallan = fallan or set()
        self.llamadas: list[tuple[str, str, str]] = []

    async def consultar_fuente(self, cliente_id: str, consentimiento_id: str, fuente: str) -> dict:
        self.llamadas.append((cliente_id, consentimiento_id, fuente))
        if fuente in self.fallan:
            raise ErrorServicio("timeout")
        return {
            "fuente": fuente,
            "datos": {"dato": 1},
            "capturado_en": "2026-10-08T12:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        }


class RiskFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.llamadas: list[tuple[str, list[dict]]] = []

    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict:
        self.llamadas.append((cliente_id, fuentes))
        if self.falla:
            raise ErrorServicio("HTTP 503")
        return {"cliente_id": cliente_id, "perfil_version": 1, "operation_time": "1700000000.1"}


class RatingFalso:
    def __init__(self, oferta: dict | None = None, error: Exception | None = None) -> None:
        self.oferta = oferta or OFERTA_COMPLETA
        self.error = error
        self.llamadas: list[tuple[str, str, str]] = []

    async def generar_oferta(self, cliente_id: str, operation_time: str, cobertura: str) -> dict:
        self.llamadas.append((cliente_id, operation_time, cobertura))
        if self.error is not None:
            raise self.error
        return self.oferta


def clientes_falsos(
    auth: AuthFalso | None = None,
    acl: AclFalso | None = None,
    risk: RiskFalso | None = None,
    rating: RatingFalso | None = None,
) -> Clientes:
    return Clientes(
        auth=auth or AuthFalso(),
        acl=acl or AclFalso(),
        risk=risk or RiskFalso(),
        rating=rating or RatingFalso(),
    )
