"""Utilidades para las pruebas."""

from __future__ import annotations

from src.sheets import HOJAS_PERMITIDAS


def conteos_completos(**overrides: int) -> dict[str, int]:
    """Conteos con las 17 hojas permitidas (0 por defecto) y overrides."""
    conteos = {hoja: 0 for hoja in HOJAS_PERMITIDAS}
    conteos.update(overrides)
    return conteos
