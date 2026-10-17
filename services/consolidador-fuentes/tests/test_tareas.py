"""Pruebas de la tarea de reconciliación (contrato puntos 4 y 5): si el ACL
Worker responde fresco, el job termina bien; si sigue degradado o el ACL
Worker no responde, se lanza una excepción para que `rq` reintente."""

import httpx
import pytest

import app.tareas as tareas


class RespuestaFalsa:
    def __init__(self, cuerpo: dict, status_code: int = 200) -> None:
        self._cuerpo = cuerpo
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._cuerpo


@pytest.fixture(autouse=True)
def consentimiento_vigente_por_defecto(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """AUTH responde "vigente" salvo que la prueba lo cambie. Devuelve las URL consultadas."""
    consultadas: list[str] = []

    def get_falso(url, timeout):
        consultadas.append(url)
        return RespuestaFalsa({"id": "consent-1", "vigente": True})

    monkeypatch.setattr(tareas.httpx, "get", get_falso)
    return consultadas


def _cuerpo(*, de_cache: bool, degradado: bool) -> dict:
    return {
        "fuente": "cuentas-bancarias",
        "datos": {},
        "capturado_en": "2026-10-07T00:00:00+00:00",
        "de_cache": de_cache,
        "degradado": degradado,
    }


def test_dato_fresco_no_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = []

    def post_falso(url, json, timeout):  # noqa: A002 - mismo nombre que el parámetro real de httpx
        llamadas.append((url, json, timeout))
        return RespuestaFalsa(_cuerpo(de_cache=False, degradado=False))

    monkeypatch.setattr(tareas.httpx, "post", post_falso)

    tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)

    url, cuerpo_enviado, _ = llamadas[0]
    assert url == f"{tareas.ACL_URL}/fuentes/consultar"
    assert cuerpo_enviado == {
        "cliente_id": "cliente-1",
        "consentimiento_id": "consent-1",
        "fuente": "cuentas-bancarias",
    }


def test_dato_de_cache_lanza_para_que_rq_reintente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tareas.httpx,
        "post",
        lambda url, json, timeout: RespuestaFalsa(_cuerpo(de_cache=True, degradado=False)),
    )

    with pytest.raises(tareas.FuenteSigueDegradadaError):
        tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)


def test_dato_degradado_lanza_para_que_rq_reintente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tareas.httpx,
        "post",
        lambda url, json, timeout: RespuestaFalsa(_cuerpo(de_cache=False, degradado=True)),
    )

    with pytest.raises(tareas.FuenteSigueDegradadaError):
        tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)


def test_acl_worker_caido_propaga_la_excepcion_de_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    def post_falso(url, json, timeout):
        raise httpx.ConnectError("caido")

    monkeypatch.setattr(tareas.httpx, "post", post_falso)

    with pytest.raises(httpx.ConnectError):
        tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)


# ---------- Regla #7: sin consentimiento vigente no se consulta ----------


def _post_que_no_debe_llamarse(url, json, timeout):
    raise AssertionError("no debe consultar el ACL Worker sin consentimiento vigente")


def test_verifica_el_consentimiento_en_auth_antes_de_consultar(
    monkeypatch: pytest.MonkeyPatch, consentimiento_vigente_por_defecto: list[str]
) -> None:
    monkeypatch.setattr(
        tareas.httpx,
        "post",
        lambda url, json, timeout: RespuestaFalsa(_cuerpo(de_cache=False, degradado=False)),
    )

    tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)

    assert consentimiento_vigente_por_defecto == [f"{tareas.AUTH_URL}/consentimientos/consent-1"]


def test_consentimiento_revocado_descarta_el_job_sin_consultar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tareas.httpx,
        "get",
        lambda url, timeout: RespuestaFalsa({"id": "consent-1", "vigente": False}),
    )
    monkeypatch.setattr(tareas.httpx, "post", _post_que_no_debe_llamarse)

    tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)


def test_consentimiento_inexistente_descarta_el_job_sin_consultar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tareas.httpx, "get", lambda url, timeout: RespuestaFalsa({}, 404))
    monkeypatch.setattr(tareas.httpx, "post", _post_que_no_debe_llamarse)

    tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)


def test_auth_caido_propaga_para_que_rq_reintente_y_no_consulta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_caido(url, timeout):
        raise httpx.ConnectError("caido")

    monkeypatch.setattr(tareas.httpx, "get", get_caido)
    monkeypatch.setattr(tareas.httpx, "post", _post_que_no_debe_llamarse)

    with pytest.raises(httpx.ConnectError):
        tareas.reconciliar_fuente("cuentas-bancarias", "cliente-1", "consent-1", 123.0)
