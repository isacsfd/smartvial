"""Modelos de entrada y validación de payloads."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .config import Settings

# Tipos escalares aceptados en el payload de una fila (celdas de Sheets).
_ESCALARES = (bool, int, float, str)


class BatchRequest(BaseModel):
    batch_id: UUID
    spreadsheet_id: str = Field(min_length=1, max_length=128)
    notes: Optional[str] = Field(default=None, max_length=2000)


class RowIn(BaseModel):
    source_row_number: int = Field(ge=2)
    record_id: Optional[str] = Field(default=None, max_length=256)
    company_id: Optional[str] = Field(default=None, max_length=256)
    payload: dict[str, Any]


class RowsRequest(BaseModel):
    batch_id: UUID
    sheet_name: str = Field(min_length=1, max_length=64)
    rows: list[RowIn] = Field(min_length=1)


class FinalizeRequest(BaseModel):
    expected_row_counts: dict[str, int] = Field(default_factory=dict)
    client_checksum: Optional[str] = Field(default=None, max_length=128)


def validar_payload(payload: dict[str, Any], settings: Settings) -> Optional[str]:
    """Devuelve un mensaje de error o None si el payload es válido."""
    if not isinstance(payload, dict):
        return "El payload debe ser un objeto JSON."

    if len(payload) > settings.max_payload_keys:
        return "El payload supera el máximo de columnas permitido."

    for clave, valor in payload.items():
        if not isinstance(clave, str) or not clave.strip():
            return "El payload contiene una clave inválida."
        if valor is None:
            continue
        if isinstance(valor, _ESCALARES):
            if isinstance(valor, str) and len(valor) > settings.max_value_length:
                return "El payload contiene un valor de texto demasiado largo."
            continue
        return "El payload solo admite valores escalares."
    return None
