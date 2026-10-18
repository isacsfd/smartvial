"""Pruebas de la lectura operativa `GET /reads/{sheet_name}` (solo lectura)."""

from .conftest import TOKEN

BATCH = "aaaaaaaa-0000-0000-0000-000000000001"
OTRO = "aaaaaaaa-0000-0000-0000-000000000002"


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


def _fila(numero, company_id="EMP1", payload=None):
    return {
        "source_row_number": numero,
        "record_id": "R-" + str(numero),
        "company_id": company_id,
        "payload": payload if payload is not None else {"ID_EMPRESA": company_id},
    }


def _url(sheet="Vehiculos", **params):
    base = f"/reads/{sheet}"
    if not params:
        return base
    return base + "?" + "&".join(f"{clave}={valor}" for clave, valor in params.items())


def test_sin_token_es_401(client, auth_headers):
    _crear(client, auth_headers)
    respuesta = client.get(_url(company_id="EMP1"))
    assert respuesta.status_code == 401


def test_usuarios_es_400(client, auth_headers):
    respuesta = client.get(
        _url(sheet="Usuarios", company_id="EMP1"), headers=auth_headers
    )
    assert respuesta.status_code == 400


def test_empresas_es_400(client, auth_headers):
    # Empresas se mantiene en Google Sheets: no se sirve por el puente.
    respuesta = client.get(
        _url(sheet="Empresas", company_id="EMP1"), headers=auth_headers
    )
    assert respuesta.status_code == 400


def test_hoja_no_permitida_es_400(client, auth_headers):
    respuesta = client.get(
        _url(sheet="OtraHoja", company_id="EMP1"), headers=auth_headers
    )
    assert respuesta.status_code == 400


def test_company_id_obligatorio_es_422(client, auth_headers):
    respuesta = client.get(_url(), headers=auth_headers)
    assert respuesta.status_code == 422


def test_sin_lote_activo_es_409(client, auth_headers):
    respuesta = client.get(_url(company_id="EMP1"), headers=auth_headers)
    assert respuesta.status_code == 409
    assert "lote activo" in respuesta.json()["detail"].lower()


def test_lectura_con_lote_activo_y_filtro_empresa(client, repo, auth_headers):
    _crear(client, auth_headers)
    _enviar(
        client,
        auth_headers,
        BATCH,
        "Vehiculos",
        [
            _fila(3, "EMP1", {"ID_VEHICULO": "V3"}),
            _fila(2, "EMP1", {"ID_VEHICULO": "V2"}),
            _fila(4, "EMP2", {"ID_VEHICULO": "V9"}),
        ],
    )
    repo.active_batch = BATCH

    respuesta = client.get(_url(company_id="emp1"), headers=auth_headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["sheet_name"] == "Vehiculos"
    assert cuerpo["company_id"] == "EMP1"
    assert cuerpo["batch_id"] == BATCH
    assert [r["source_row_number"] for r in cuerpo["rows"]] == [2, 3]
    assert [r["payload"]["ID_VEHICULO"] for r in cuerpo["rows"]] == ["V2", "V3"]
    assert all(isinstance(r["payload"], dict) for r in cuerpo["rows"])


def test_lectura_con_batch_explicito(client, auth_headers):
    _crear(client, auth_headers)
    _enviar(client, auth_headers, BATCH, "Vehiculos", [_fila(2, "EMP1")])

    respuesta = client.get(
        _url(company_id="EMP1", batch_id=BATCH), headers=auth_headers
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["batch_id"] == BATCH
    assert len(respuesta.json()["rows"]) == 1


def test_batch_explicito_inexistente_es_404(client, auth_headers):
    respuesta = client.get(
        _url(company_id="EMP1", batch_id=OTRO), headers=auth_headers
    )
    assert respuesta.status_code == 404


def test_aislamiento_entre_lotes(client, auth_headers):
    _crear(client, auth_headers, BATCH)
    _crear(client, auth_headers, OTRO)
    _enviar(
        client,
        auth_headers,
        BATCH,
        "Vehiculos",
        [_fila(2, "EMP1", {"ID_VEHICULO": "A"})],
    )
    _enviar(
        client,
        auth_headers,
        OTRO,
        "Vehiculos",
        [
            _fila(2, "EMP1", {"ID_VEHICULO": "B"}),
            _fila(3, "EMP1", {"ID_VEHICULO": "C"}),
        ],
    )

    lote_a = client.get(
        _url(company_id="EMP1", batch_id=BATCH), headers=auth_headers
    ).json()
    lote_b = client.get(
        _url(company_id="EMP1", batch_id=OTRO), headers=auth_headers
    ).json()
    assert [r["payload"]["ID_VEHICULO"] for r in lote_a["rows"]] == ["A"]
    assert [r["payload"]["ID_VEHICULO"] for r in lote_b["rows"]] == ["B", "C"]


def test_error_de_base_de_datos_es_503_sin_detalles(
    client, repo, auth_headers, monkeypatch
):
    _crear(client, auth_headers)
    repo.active_batch = BATCH

    def fallar(*_args, **_kwargs):
        raise RuntimeError("fallo simulado de BD con SELECT secreto")

    monkeypatch.setattr(repo, "leer_filas_empresa", fallar)
    respuesta = client.get(_url(company_id="EMP1"), headers=auth_headers)
    assert respuesta.status_code == 503
    assert "fallo simulado" not in respuesta.text
    assert "SELECT" not in respuesta.text.upper()


def test_empresa_sin_filas_devuelve_lista_vacia(client, repo, auth_headers):
    _crear(client, auth_headers)
    _enviar(client, auth_headers, BATCH, "Vehiculos", [_fila(2, "EMP1")])
    repo.active_batch = BATCH

    respuesta = client.get(_url(company_id="EMP2"), headers=auth_headers)
    assert respuesta.status_code == 200
    assert respuesta.json()["rows"] == []
