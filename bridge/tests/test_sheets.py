from .helpers import conteos_completos

BATCH = "22222222-2222-2222-2222-222222222222"


def _crear_lote(client, auth_headers):
    client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )


def _fila(numero=2, **extra):
    base = {
        "source_row_number": numero,
        "record_id": "R-1",
        "company_id": "EMP1",
        "payload": {"ID_EMPRESA": "EMP1"},
    }
    base.update(extra)
    return base


def test_rechaza_usuarios(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "Usuarios", "rows": [_fila()]},
        headers=auth_headers,
    )
    assert respuesta.status_code == 400
    assert "Usuarios" in respuesta.json()["detail"]


def test_rechaza_hoja_desconocida(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "OtraHoja", "rows": [_fila()]},
        headers=auth_headers,
    )
    assert respuesta.status_code == 400


def test_acepta_hoja_permitida(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "Vehiculos", "rows": [_fila()]},
        headers=auth_headers,
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["rows_written"] == 1


def test_finalize_rechaza_hoja_no_permitida(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": {"Usuarios": 0}},
        headers=auth_headers,
    )
    assert respuesta.status_code == 400


def test_finalize_falla_si_falta_una_hoja(client, auth_headers):
    _crear_lote(client, auth_headers)
    conteos = conteos_completos()
    del conteos["Historial"]
    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos},
        headers=auth_headers,
    )
    assert respuesta.status_code == 409
    assert "Historial" in respuesta.json()["detail"]


def test_finalize_falla_si_hay_hoja_extra(client, auth_headers):
    _crear_lote(client, auth_headers)
    conteos = conteos_completos()
    conteos["OtraHoja"] = 0
    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos},
        headers=auth_headers,
    )
    assert respuesta.status_code == 400
