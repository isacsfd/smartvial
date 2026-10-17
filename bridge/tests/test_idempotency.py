from .helpers import conteos_completos

BATCH = "55555555-5555-5555-5555-555555555555"


def _crear_lote(client, auth_headers):
    return client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )


def _bloque():
    return {
        "batch_id": BATCH,
        "sheet_name": "Vehiculos",
        "rows": [
            {"source_row_number": 2, "payload": {"ID_VEHICULO": "V1"}},
            {"source_row_number": 3, "payload": {"ID_VEHICULO": "V2"}},
        ],
    }


def test_crear_lote_dos_veces_es_idempotente(client, auth_headers):
    primera = _crear_lote(client, auth_headers)
    segunda = _crear_lote(client, auth_headers)
    assert primera.status_code == 200
    assert primera.json()["created"] is True
    assert segunda.status_code == 200
    assert segunda.json()["created"] is False


def test_reenviar_filas_no_duplica(client, auth_headers):
    _crear_lote(client, auth_headers)
    client.post("/import/rows", json=_bloque(), headers=auth_headers)
    client.post("/import/rows", json=_bloque(), headers=auth_headers)

    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(Vehiculos=2)},
        headers=auth_headers,
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["row_counts"]["Vehiculos"] == 2


def test_finalize_dos_veces_falla_la_segunda(client, auth_headers):
    _crear_lote(client, auth_headers)
    client.post("/import/rows", json=_bloque(), headers=auth_headers)
    primera = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(Vehiculos=2)},
        headers=auth_headers,
    )
    segunda = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(Vehiculos=2)},
        headers=auth_headers,
    )
    assert primera.status_code == 200
    assert segunda.status_code == 409


def test_upsert_actualiza_payload(client, auth_headers):
    _crear_lote(client, auth_headers)
    client.post("/import/rows", json=_bloque(), headers=auth_headers)
    actualizado = {
        "batch_id": BATCH,
        "sheet_name": "Vehiculos",
        "rows": [{"source_row_number": 2, "payload": {"ID_VEHICULO": "V1-NUEVO"}}],
    }
    respuesta = client.post("/import/rows", json=actualizado, headers=auth_headers)
    assert respuesta.status_code == 200

    final = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(Vehiculos=2)},
        headers=auth_headers,
    )
    assert final.status_code == 200


def _finalizar(client, auth_headers):
    client.post("/import/rows", json=_bloque(), headers=auth_headers)
    return client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(Vehiculos=2)},
        headers=auth_headers,
    )


def test_no_reanudar_lote_validado(client, auth_headers):
    _crear_lote(client, auth_headers)
    assert _finalizar(client, auth_headers).status_code == 200
    reanudar = _crear_lote(client, auth_headers)
    assert reanudar.status_code == 409


def test_no_escribir_en_lote_validado(client, auth_headers):
    _crear_lote(client, auth_headers)
    assert _finalizar(client, auth_headers).status_code == 200
    respuesta = client.post("/import/rows", json=_bloque(), headers=auth_headers)
    assert respuesta.status_code == 409


def test_no_reanudar_con_otro_spreadsheet(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "otro-ss"},
        headers=auth_headers,
    )
    assert respuesta.status_code == 409
