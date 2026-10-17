"""Pruebas de la lectura de diagnóstico por `batch_id` (solo lectura).

No depende de `active_migration` ni de `SMARTVIAL_PG_READS_ENABLED`: usa el
`batch_id` explícito. El repositorio es el de memoria de `conftest`.
"""

from .conftest import TOKEN

BATCH = "88888888-8888-8888-8888-888888888888"
OTRO = "99999999-9999-9999-9999-999999999999"


def _crear(client, auth_headers, batch=BATCH):
    return client.post(
        "/import/batches",
        json={"batch_id": batch, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )


def _enviar(client, auth_headers, batch, sheet, rows):
    return client.post(
        "/import/rows",
        json={"batch_id": batch, "sheet_name": sheet, "rows": rows},
        headers=auth_headers,
    )


def _fila(numero, record_id="R-1", company_id="EMP1", payload=None):
    return {
        "source_row_number": numero,
        "record_id": record_id,
        "company_id": company_id,
        "payload": payload if payload is not None else {"ID_EMPRESA": "EMP1"},
    }


def _url(batch=BATCH, **params):
    base = f"/import/batches/{batch}/rows"
    if not params:
        return base
    consulta = "&".join(f"{clave}={valor}" for clave, valor in params.items())
    return f"{base}?{consulta}"


def test_sin_token_es_401(client):
    _crear(client, {"X-Bridge-Token": TOKEN})
    respuesta = client.get(_url())
    assert respuesta.status_code == 401


def test_token_incorrecto_es_401(client, auth_headers):
    _crear(client, auth_headers)
    respuesta = client.get(_url(), headers={"X-Bridge-Token": "malo"})
    assert respuesta.status_code == 401


def test_lote_inexistente_es_404(client, auth_headers):
    respuesta = client.get(_url(OTRO), headers=auth_headers)
    assert respuesta.status_code == 404


def test_resumen_conteos_por_hoja(client, auth_headers):
    _crear(client, auth_headers)
    _enviar(client, auth_headers, BATCH, "Vehiculos", [_fila(2), _fila(3)])
    _enviar(client, auth_headers, BATCH, "Conductores", [_fila(2)])

    respuesta = client.get(_url(), headers=auth_headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["batch_id"] == BATCH
    assert cuerpo["status"] == "running"
    assert cuerpo["hojas"] == {"Vehiculos": 2, "Conductores": 1}


def test_sheet_no_permitida_es_400(client, auth_headers):
    _crear(client, auth_headers)
    respuesta = client.get(_url(sheet_name="OtraHoja"), headers=auth_headers)
    assert respuesta.status_code == 400


def test_usuarios_rechazado_es_400(client, auth_headers):
    _crear(client, auth_headers)
    respuesta = client.get(_url(sheet_name="Usuarios"), headers=auth_headers)
    assert respuesta.status_code == 400
    assert "Usuarios" in respuesta.json()["detail"]


def test_muestra_filas_no_expone_payload(client, auth_headers):
    _crear(client, auth_headers)
    _enviar(
        client,
        auth_headers,
        BATCH,
        "Vehiculos",
        [
            _fila(2, record_id="VEH-2", company_id="EMP1", payload={"ID_VEHICULO": "PLACASECRETA"}),
            _fila(3, record_id="VEH-3", company_id="EMP1", payload={"ID_VEHICULO": "OTRA"}),
        ],
    )

    respuesta = client.get(_url(sheet_name="Vehiculos", limite=5), headers=auth_headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["sheet_name"] == "Vehiculos"
    filas = cuerpo["filas"]
    assert [f["source_row_number"] for f in filas] == [2, 3]
    assert filas[0]["claves"] == 1
    assert filas[0]["tiene_record_id"] is True
    assert filas[0]["tiene_company_id"] is True
    # No se expone ningún valor del payload.
    assert "PLACASECRETA" not in respuesta.text
    assert "OTRA" not in respuesta.text


def test_respeta_el_limite_de_muestra(client, auth_headers):
    _crear(client, auth_headers)
    _enviar(client, auth_headers, BATCH, "Vehiculos", [_fila(n) for n in (2, 3, 4)])

    respuesta = client.get(_url(sheet_name="Vehiculos", limite=2), headers=auth_headers)
    assert respuesta.status_code == 200
    filas = respuesta.json()["filas"]
    assert [f["source_row_number"] for f in filas] == [2, 3]


def test_aislamiento_entre_lotes(client, auth_headers):
    _crear(client, auth_headers, BATCH)
    _crear(client, auth_headers, OTRO)
    _enviar(client, auth_headers, BATCH, "Vehiculos", [_fila(2, record_id="A-2")])
    _enviar(client, auth_headers, OTRO, "Vehiculos", [_fila(2, record_id="B-2"), _fila(3, record_id="B-3")])

    resumen_batch = client.get(_url(), headers=auth_headers).json()
    resumen_otro = client.get(_url(OTRO), headers=auth_headers).json()
    assert resumen_batch["hojas"] == {"Vehiculos": 1}
    assert resumen_otro["hojas"] == {"Vehiculos": 2}

    muestra = client.get(_url(sheet_name="Vehiculos"), headers=auth_headers).json()
    assert [f["source_row_number"] for f in muestra["filas"]] == [2]


def test_limite_fuera_de_rango_es_422(client, auth_headers):
    _crear(client, auth_headers)
    assert client.get(_url(limite=0), headers=auth_headers).status_code == 422
    assert client.get(_url(limite=1000), headers=auth_headers).status_code == 422


def test_resumen_filtra_hojas_no_permitidas(repo):
    repo.batches[BATCH] = {"spreadsheet_id": "ss-1", "status": "validated", "notes": None}
    repo.rows[(BATCH, "Vehiculos", 2)] = _fila(2)
    repo.rows[(BATCH, "OtraHoja", 2)] = _fila(2)

    resumen = repo.resumen_filas_lote(BATCH, None, 5)
    assert resumen["hojas"] == {"Vehiculos": 1}
    assert "OtraHoja" not in resumen["hojas"]
