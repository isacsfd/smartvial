"""Pruebas del límite de cuerpo a nivel ASGI (incluye transferencia fragmentada)."""

from __future__ import annotations

import anyio

from src.body_limit import LimiteCuerpoMiddleware


async def _ejecutar(mensajes, limite, headers=()):
    capturado = {"llamado": False, "cuerpo": None}
    salidas: list[dict] = []

    async def app(scope, receive, send):
        capturado["llamado"] = True
        cuerpo = bytearray()
        while True:
            mensaje = await receive()
            if mensaje.get("type") == "http.disconnect":
                break
            cuerpo.extend(mensaje.get("body", b"") or b"")
            if not mensaje.get("more_body", False):
                break
        capturado["cuerpo"] = bytes(cuerpo)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    pendientes = list(mensajes)

    async def receive():
        if pendientes:
            return pendientes.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensaje):
        salidas.append(mensaje)

    middleware = LimiteCuerpoMiddleware(app, max_bytes_getter=lambda: limite)
    await middleware({"type": "http", "headers": list(headers)}, receive, send)
    return salidas, capturado


def _estado(salidas):
    return salidas[0]["status"]


def test_fragmentado_excede_limite_es_413():
    mensajes = [
        {"type": "http.request", "body": b"x" * 40000, "more_body": True},
        {"type": "http.request", "body": b"x" * 40000, "more_body": False},
    ]
    salidas, capturado = anyio.run(_ejecutar, mensajes, 50000)
    assert _estado(salidas) == 413
    assert capturado["llamado"] is False


def test_sin_content_length_bajo_limite_pasa():
    mensajes = [
        {"type": "http.request", "body": b"x" * 10, "more_body": True},
        {"type": "http.request", "body": b"y" * 10, "more_body": False},
    ]
    salidas, capturado = anyio.run(_ejecutar, mensajes, 100)
    assert _estado(salidas) == 200
    assert capturado["llamado"] is True
    assert capturado["cuerpo"] == b"x" * 10 + b"y" * 10


def test_content_length_grande_rechazo_temprano():
    mensajes = [{"type": "http.request", "body": b"hola", "more_body": False}]
    salidas, capturado = anyio.run(
        _ejecutar, mensajes, 100, [(b"content-length", b"999999")]
    )
    assert _estado(salidas) == 413
    assert capturado["llamado"] is False


def test_content_length_invalido_es_400():
    mensajes = [{"type": "http.request", "body": b"hola", "more_body": False}]
    salidas, _capturado = anyio.run(
        _ejecutar, mensajes, 100, [(b"content-length", b"abc")]
    )
    assert _estado(salidas) == 400


def test_exactamente_en_el_limite_pasa():
    mensajes = [{"type": "http.request", "body": b"x" * 100, "more_body": False}]
    salidas, capturado = anyio.run(_ejecutar, mensajes, 100)
    assert _estado(salidas) == 200
    assert len(capturado["cuerpo"]) == 100
