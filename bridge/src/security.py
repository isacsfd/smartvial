"""Autenticación por token con comparación en tiempo constante."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException
from .config import Settings, cargar_settings


def comparar_seguro(proporcionado: str, esperado: str) -> bool:
    """Compara dos cadenas sin filtrar información por temporización."""
    if not proporcionado or not esperado:
        return False
    return hmac.compare_digest(str(proporcionado), str(esperado))


def get_settings() -> Settings:
    return cargar_settings()


def requerir_token(
    x_bridge_token: str = Header(default="", alias="X-Bridge-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not comparar_seguro(x_bridge_token, settings.bridge_token):
        raise HTTPException(status_code=401, detail="No autorizado.")
