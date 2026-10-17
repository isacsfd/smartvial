"""Middleware ASGI que limita los bytes REALES recibidos en el cuerpo.

El middleware HTTP de Starlette (`@app.middleware("http")`) solo ve la cabecera
`Content-Length`, que un cliente puede omitir usando transferencia fragmentada
(`Transfer-Encoding: chunked`). Este middleware opera a nivel ASGI: cuenta cada
fragmento que llega por `receive` y corta con `413` en cuanto se supera el
límite, sin confiar en `Content-Length`.

Además hace un rechazo temprano si `Content-Length` ya supera el límite. El
cuerpo admitido se reproduce al resto de la aplicación, de modo que las rutas
lo siguen leyendo con normalidad.
"""

from __future__ import annotations

from typing import Awaitable, Callable

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CUERPO_EXCESIVO = b'{"detail":"Cuerpo demasiado grande."}'
_LONGITUD_INVALIDA = b'{"detail":"Content-Length inv\xc3\xa1lido."}'


class LimiteCuerpoMiddleware:
    def __init__(self, app: ASGIApp, max_bytes_getter: Callable[[], int]) -> None:
        self.app = app
        self._max_bytes = max_bytes_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limite = self._max_bytes()

        for nombre, valor in scope.get("headers", []):
            if nombre == b"content-length":
                try:
                    declarado = int(valor)
                except (TypeError, ValueError):
                    await self._json(send, 400, _LONGITUD_INVALIDA)
                    return
                if declarado > limite:
                    await self._json(send, 413, _CUERPO_EXCESIVO)
                    return

        # Lectura acotada: se detiene en cuanto se supera el límite.
        cuerpo = bytearray()
        while True:
            mensaje = await receive()
            if mensaje.get("type") == "http.disconnect":
                return
            cuerpo.extend(mensaje.get("body", b"") or b"")
            if len(cuerpo) > limite:
                await self._json(send, 413, _CUERPO_EXCESIVO)
                return
            if not mensaje.get("more_body", False):
                break

        datos = bytes(cuerpo)
        entregado = False

        async def receive_reproducido() -> dict:
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": datos, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, receive_reproducido, send)

    @staticmethod
    async def _json(send: Send, status: int, cuerpo: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(cuerpo)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": cuerpo})
