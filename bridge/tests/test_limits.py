BATCH = "33333333-3333-3333-3333-333333333333"


def _crear_lote(client, auth_headers):
    client.post(
        "/import/batches",
        json={"batch_id": BATCH, "spreadsheet_id": "ss-1"},
        headers=auth_headers,
    )


def _enviar(client, auth_headers, rows):
    return client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "Vehiculos", "rows": rows},
        headers=auth_headers,
    )


def _fila(numero):
    return {
        "source_row_number": numero,
        "record_id": None,
        "company_id": None,
        "payload": {"A": "1"},
    }


def test_demasiadas_filas(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "Vehiculos",
            "rows": [_fila(n) for n in range(2, 6)],
        },
        headers=auth_headers,
    )
    assert respuesta.status_code == 413


def test_cuerpo_demasiado_grande(client, auth_headers):
    respuesta = client.post(
        "/import/rows",
        content=b"x" * 70000,
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert respuesta.status_code == 413


def test_cuerpo_fragmentado_demasiado_grande(client, auth_headers):
    def generador():
        yield b"x" * 40000
        yield b"x" * 40000

    respuesta = client.post(
        "/import/rows",
        content=generador(),
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert respuesta.status_code == 413


def test_numero_de_fila_fuera_de_rango(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "Vehiculos", "rows": [_fila(99999)]},
        headers=auth_headers,
    )
    assert respuesta.status_code == 422


def test_fila_con_payload_no_valido(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "Vehiculos",
            "rows": [_fila(2) | {"payload": {"A": {"anidado": 1}}}],
        },
        headers=auth_headers,
    )
    assert respuesta.status_code == 422


def test_valor_de_texto_demasiado_largo(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={
            "batch_id": BATCH,
            "sheet_name": "Vehiculos",
            "rows": [_fila(2) | {"payload": {"A": "x" * 50}}],
        },
        headers=auth_headers,
    )
    assert respuesta.status_code == 422


def test_numero_de_fila_uno_es_invalido(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = client.post(
        "/import/rows",
        json={"batch_id": BATCH, "sheet_name": "Vehiculos", "rows": [_fila(1)]},
        headers=auth_headers,
    )
    assert respuesta.status_code == 422


def test_valor_en_el_limite_es_aceptado(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(client, auth_headers, [_fila(2) | {"payload": {"A": "x" * 16}}])
    assert respuesta.status_code == 200


def test_valor_sobre_el_limite_por_un_caracter(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(client, auth_headers, [_fila(2) | {"payload": {"A": "x" * 17}}])
    assert respuesta.status_code == 422


def test_demasiadas_claves_en_payload(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(
        client, auth_headers, [_fila(2) | {"payload": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}}]
    )
    assert respuesta.status_code == 422


def test_claves_en_el_limite_son_aceptadas(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(
        client, auth_headers, [_fila(2) | {"payload": {"A": "1", "B": "2", "C": "3", "D": "4"}}]
    )
    assert respuesta.status_code == 200


def test_record_id_demasiado_largo(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(client, auth_headers, [_fila(2) | {"record_id": "r" * 257}])
    assert respuesta.status_code == 422


def test_numero_de_fila_debe_ser_entero(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(client, auth_headers, [_fila(2.5)])
    assert respuesta.status_code == 422


def test_numeros_de_fila_duplicados(client, auth_headers):
    _crear_lote(client, auth_headers)
    respuesta = _enviar(client, auth_headers, [_fila(2), _fila(2)])
    assert respuesta.status_code == 422
