"""Archivos por GraphQL (KAN-250): el BFF solo reenvía el actor del token a ALMACENAMIENTO y
traduce sus códigos; los bytes nunca pasan por aquí."""

from fakes import AlmacenamientoFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

SOLICITAR = """mutation($e: SolicitudSubidaArchivoInput!) {
  solicitarSubidaArchivo(entrada: $e) {
    __typename
    ... on SubidaArchivo { url metodo encabezados { nombre valor } archivo { id estado } }
    ... on ErrorArchivo { codigo mensaje }
  }
}"""
CONFIRMAR = """mutation($id: ID!) {
  confirmarSubidaArchivo(id: $id) {
    __typename
    ... on Archivo { id estado tamanoBytes }
    ... on ErrorArchivo { codigo }
  }
}"""
DESCARGAR = """mutation($id: ID!) {
  descargarArchivo(id: $id) {
    __typename
    ... on DescargaArchivo { url }
    ... on ErrorArchivo { codigo }
  }
}"""
ARCHIVO = "query($id: ID!) { archivo(id: $id) { id estado } }"
ENTRADA = {"tipo": "foto-cliente", "tipoContenido": "image/jpeg", "tamanoBytes": 64}


def _cliente(almacen: AlmacenamientoFalso) -> TestClient:
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(almacenamiento=almacen),
        )
    )


def _post(client, query, variables, rol="CLIENTE", uid="u-1", token=True):
    cabeceras = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"} if token else {}
    r = client.post(
        "/graphql/web", json={"query": query, "variables": variables}, headers=cabeceras
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_recorrido_del_cliente_subida_confirmacion_y_descarga() -> None:
    almacen = AlmacenamientoFalso()
    with _cliente(almacen) as client:
        # Un CLIENTE sube a su nombre aunque pida otro propietario.
        entrada = {**ENTRADA, "propietarioId": "otro"}
        subida = _post(client, SOLICITAR, {"e": entrada})["data"]["solicitarSubidaArchivo"]
        assert subida["__typename"] == "SubidaArchivo" and subida["metodo"] == "PUT"
        assert subida["encabezados"] == [{"nombre": "Content-Type", "valor": "image/jpeg"}]
        assert almacen.llamadas[0][1:3] == ("CLIENTE", "u-1")
        assert almacen.llamadas[0][3]["propietario_id"] == "u-1"
        archivo_id = subida["archivo"]["id"]

        confirmado = _post(client, CONFIRMAR, {"id": archivo_id})["data"]
        assert confirmado["confirmarSubidaArchivo"]["estado"] == "DISPONIBLE"
        assert _post(client, ARCHIVO, {"id": archivo_id})["data"]["archivo"]["estado"] == (
            "DISPONIBLE"
        )
        descarga = _post(client, DESCARGAR, {"id": archivo_id})["data"]["descargarArchivo"]
        assert "firma=descarga" in descarga["url"]

        # Otro cliente no lo ve.
        assert _post(client, ARCHIVO, {"id": archivo_id}, uid="u-2")["data"]["archivo"] is None
        otro = _post(client, DESCARGAR, {"id": archivo_id}, uid="u-2")["data"]
        assert otro["descargarArchivo"]["codigo"] == "ARCHIVO_NO_ENCONTRADO"


def test_los_rechazos_llegan_como_error_archivo_con_mensaje() -> None:
    almacen = AlmacenamientoFalso()
    almacen.rechazo = "TAMANO_NO_ADMITIDO"
    with _cliente(almacen) as client:
        error = _post(client, SOLICITAR, {"e": ENTRADA})["data"]["solicitarSubidaArchivo"]
        assert error == {
            "__typename": "ErrorArchivo",
            "codigo": "TAMANO_NO_ADMITIDO",
            "mensaje": "El archivo supera el tamaño permitido.",
        }


def test_un_asesor_debe_indicar_el_propietario() -> None:
    almacen = AlmacenamientoFalso()
    with _cliente(almacen) as client:
        sin = _post(client, SOLICITAR, {"e": ENTRADA}, rol="ASESOR", uid="a-1")["data"]
        assert sin["solicitarSubidaArchivo"]["codigo"] == "PROPIETARIO_INVALIDO"
        assert almacen.llamadas == []
        con = _post(
            client, SOLICITAR, {"e": {**ENTRADA, "propietarioId": "u-9"}}, rol="ASESOR", uid="a-1"
        )["data"]["solicitarSubidaArchivo"]
        assert con["__typename"] == "SubidaArchivo"
        assert almacen.llamadas[0][3]["propietario_id"] == "u-9"


def test_exige_token_siempre_y_no_responde_5xx_si_almacenamiento_falla() -> None:
    almacen = AlmacenamientoFalso()
    with _cliente(almacen) as client:
        sin_token = _post(client, SOLICITAR, {"e": ENTRADA}, token=False)
        assert sin_token["data"] is None and sin_token["errors"]
        almacen.error = ErrorServicio("ConnectError")
        caido = _post(client, SOLICITAR, {"e": ENTRADA})
        assert caido["errors"][0]["message"] == (
            "No pudimos procesar el archivo. Intenta de nuevo."
        )
