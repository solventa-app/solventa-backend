"""El stub de KYC con la forma de Didit v3 (HU-M01): mismas rutas, multipart, `x-api-key` y un
caso por marcador dentro de la imagen de prueba."""

import pytest
from fastapi.testclient import TestClient

from app.main import NUMERO_POR_DEFECTO, create_app

LLAVE = {"x-api-key": "local"}
JPEG = b"\xff\xd8\xff\xe0"


def _imagen(*marcas: str) -> bytes:
    return JPEG + b" ".join(m.encode() for m in marcas) + b"\x00" * 16


def _documento(client, anverso: bytes, **kw):
    return client.post(
        "/v3/id-verification/",
        files={"front_image": ("a.jpg", anverso), "back_image": ("b.jpg", _imagen())},
        data={"vendor_data": "onb-1", "save_api_request": "false"},
        headers=LLAVE,
        **kw,
    )


def test_sin_marcador_todo_sale_aprobado_con_datos_ficticios() -> None:
    with TestClient(create_app()) as client:
        r = _documento(client, _imagen())
        vida = client.post(
            "/v3/passive-liveness/", files={"user_image": ("r.jpg", _imagen())}, headers=LLAVE
        )
        rostro = client.post(
            "/v3/face-match/",
            files={"user_image": ("r.jpg", _imagen()), "ref_image": ("a.jpg", _imagen())},
            headers=LLAVE,
        )

    documento = r.json()["id_verification"]
    assert r.json()["vendor_data"] == "onb-1"
    assert documento["status"] == "Approved"
    assert documento["document_number"] == NUMERO_POR_DEFECTO
    assert documento["issuing_state"] == "COL"
    assert vida.json()["liveness"]["status"] == "Approved"
    assert rostro.json()["face_match"]["status"] == "Approved"


@pytest.mark.parametrize(
    ("caso", "riesgo"), [("ilegible", "DOCUMENT_NOT_READABLE"), ("vencido", "DOCUMENT_EXPIRED")]
)
def test_documentos_que_se_rechazan(caso: str, riesgo: str) -> None:
    with TestClient(create_app()) as client:
        documento = _documento(client, _imagen(f"CASO={caso}")).json()["id_verification"]
    assert documento["status"] == "Declined"
    assert [a["risk"] for a in documento["warnings"]] == [riesgo]
    assert documento["warnings"][0]["log_type"] == "error"


def test_menor_de_edad_sale_aprobado_pero_con_su_fecha_y_el_numero_del_marcador() -> None:
    with TestClient(create_app()) as client:
        documento = _documento(client, _imagen("CASO=menor", "NUMERO=99887766")).json()[
            "id_verification"
        ]
    assert documento["status"] == "Approved"
    assert documento["age"] < 18
    assert documento["document_number"] == "99887766"


def test_suplantacion_y_rostro_distinto() -> None:
    with TestClient(create_app()) as client:
        vida = client.post(
            "/v3/passive-liveness/",
            files={"user_image": ("r.jpg", _imagen("CASO=suplantacion"))},
            headers=LLAVE,
        ).json()["liveness"]
        rostro = client.post(
            "/v3/face-match/",
            files={
                "user_image": ("r.jpg", _imagen("CASO=rostro-distinto")),
                "ref_image": ("a.jpg", _imagen()),
            },
            headers=LLAVE,
        ).json()["face_match"]
    assert (vida["status"], vida["warnings"][0]["risk"]) == ("Declined", "LIVENESS_FACE_ATTACK")
    assert rostro["status"] == "Declined" and rostro["score"] < 30


def test_sin_llave_403_sin_imagen_400_y_respeta_el_modo_error() -> None:
    with TestClient(create_app()) as client:
        assert (
            client.post(
                "/v3/id-verification/", files={"front_image": ("a.jpg", _imagen())}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v3/passive-liveness/", files={"otra": ("a.jpg", b"x")}, headers=LLAVE
            ).status_code
            == 400
        )
        assert _documento(client, _imagen(), params={"modo": "error"}).status_code == 503
