from fastapi.testclient import TestClient

from .conftest import TOKEN


def _cuerpo():
    return {
        "batch_id": "11111111-1111-1111-1111-111111111111",
        "spreadsheet_id": "ss-1",
    }


def test_sin_token_es_401():
    from src.main import app

    with TestClient(app) as client:
        respuesta = client.post("/import/batches", json=_cuerpo())
    assert respuesta.status_code == 401


def test_token_incorrecto_es_401():
    from src.main import app

    with TestClient(app) as client:
        respuesta = client.post(
            "/import/batches", json=_cuerpo(), headers={"X-Bridge-Token": "malo"}
        )
    assert respuesta.status_code == 401


def test_token_correcto_autoriza(client, auth_headers):
    respuesta = client.post("/import/batches", json=_cuerpo(), headers=auth_headers)
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "running"


def test_health_sin_token(client):
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"
