from .helpers import conteos_completos

BATCH = "44444444-4444-4444-4444-444444444444"


def _crear_lote(client, auth_headers):
    client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )


def test_acepta_escalares_y_null(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "Conductores",
            "rows": [
                {
                    "source_row_number": 2,
                    "record_id": "C-1",
                    "company_id": "EMP1",
                    "payload": {"A": "texto", "B": 3, "C": 1.5, "D": None},
                }
            ],
        },
        headers=auth_headers,
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["rows_written"] == 1


def test_rechaza_lista_en_payload(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "Conductores",
            "rows": [
                {
                    "source_row_number": 2,
                    "payload": {"A": [1, 2, 3]},
                }
            ],
        },
        headers=auth_headers,
    )
    assert respuesta.status_code == 422


def test_finalize_valida_conteos_y_sella(client, auth_headers):
    _crear_lote(client, auth_headers)
    client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "PESV",
            "rows": [
                {"source_row_number": 2, "payload": {"ID_REGISTRO": "P1"}},
                {"source_row_number": 3, "payload": {"ID_REGISTRO": "P2"}},
            ],
        },
        headers=auth_headers,
    )
    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(PESV=2)},
        headers=auth_headers,
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "validated"
    assert cuerpo["row_counts"]["PESV"] == 2
    assert len(cuerpo["checksum"]) == 64


def test_finalize_falla_si_conteos_no_coinciden(client, auth_headers):
    _crear_lote(client, auth_headers)
    client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "PESV",
            "rows": [{"source_row_number": 2, "payload": {"ID_REGISTRO": "P1"}}],
        },
        headers=auth_headers,
    )
    respuesta = client.post(
        f"/import/batches/{BATCH}/finalize",
        json={"expected_row_counts": conteos_completos(PESV=5)},
        headers=auth_headers,
    )
    assert respuesta.status_code == 409
