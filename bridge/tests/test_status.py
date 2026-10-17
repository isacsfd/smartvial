BATCH = "66666666-6666-6666-6666-666666666666"
DESCONOCIDO = "77777777-7777-7777-7777-777777777777"


def test_consultar_lote_existente(client, auth_headers):
    client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )
    respuesta = client.get(f"/import/batches/{BATCH}", headers=auth_headers)
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "running"


def test_consultar_lote_inexistente(client, auth_headers):
    respuesta = client.get(f"/import/batches/{DESCONOCIDO}", headers=auth_headers)
    assert respuesta.status_code == 404


def test_consultar_lote_sin_token(client, auth_headers):
    respuesta = client.get(f"/import/batches/{BATCH}")
    assert respuesta.status_code == 401
