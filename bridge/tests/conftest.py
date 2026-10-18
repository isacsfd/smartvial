"""Pruebas del puente. No requieren credenciales reales ni escriben en Neon.

Se reemplaza la dependencia `get_repo` por un repositorio en memoria que imita
la semántica de idempotencia del UPSERT y la validación de conteos.
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.main import app, get_repo  # noqa: E402
from src.repository import (  # noqa: E402
    BatchConflict,
    BatchNotFound,
    CountMismatch,
    NoActiveMigration,
)
from src.sheets import HOJAS_PERMITIDAS  # noqa: E402

TOKEN = "token-de-prueba"


class FakeRepository:
    """Repositorio en memoria con la misma interfaz que PostgresRepository."""

    def __init__(self) -> None:
        self.batches: dict[str, dict] = {}
        self.rows: dict[tuple[str, str, int], dict] = {}
        self.active_batch: str | None = None

    def crear_o_reanudar_lote(self, batch_id, spreadsheet_id, notes):
        bid = str(batch_id)
        existente = self.batches.get(bid)
        if existente is not None:
            if existente["spreadsheet_id"] != spreadsheet_id:
                raise BatchConflict("otro spreadsheet")
            if existente["status"] not in ("running", "failed"):
                raise BatchConflict("no admite reanudación")
            existente["status"] = "running"
            return {"batch_id": bid, "status": "running", "created": False}
        self.batches[bid] = {
            "spreadsheet_id": spreadsheet_id,
            "status": "running",
            "notes": notes,
        }
        return {"batch_id": bid, "status": "running", "created": True}

    def obtener_lote(self, batch_id):
        bid = str(batch_id)
        if bid not in self.batches:
            raise BatchNotFound("no existe")
        return {"batch_id": bid, **self.batches[bid]}

    def resumen_filas_lote(self, batch_id, sheet_name, limite):
        bid = str(batch_id)
        if bid not in self.batches:
            raise BatchNotFound("no existe")
        conteos: dict[str, int] = {}
        for (b, s, _rn) in self.rows:
            if b == bid and s in HOJAS_PERMITIDAS:
                conteos[s] = conteos.get(s, 0) + 1
        resultado = {
            "batch_id": bid,
            "status": self.batches[bid]["status"],
            "hojas": conteos,
        }
        if sheet_name is not None:
            filas = [
                {
                    "source_row_number": int(rn),
                    "claves": len(fila["payload"]),
                    "tiene_record_id": bool(fila.get("record_id")),
                    "tiene_company_id": bool(fila.get("company_id")),
                }
                for (b, s, rn), fila in self.rows.items()
                if b == bid and s == sheet_name
            ]
            filas.sort(key=lambda x: x["source_row_number"])
            resultado["sheet_name"] = sheet_name
            resultado["filas"] = filas[: int(limite)]
        return resultado

    def leer_filas_empresa(self, sheet_name, company_id, batch_id=None):
        bid = str(batch_id) if batch_id is not None else self.active_batch
        if bid is None:
            raise NoActiveMigration("No hay un lote activo.")
        if bid not in self.batches:
            raise BatchNotFound("no existe")
        empresa = str(company_id).upper()
        filas = [
            {
                "source_row_number": int(rn),
                "payload": fila["payload"],
            }
            for (b, s, rn), fila in self.rows.items()
            if b == bid
            and s == sheet_name
            and str(fila.get("company_id") or "").upper() == empresa
        ]
        filas.sort(key=lambda x: x["source_row_number"])
        return {
            "batch_id": bid,
            "sheet_name": sheet_name,
            "company_id": empresa,
            "rows": filas,
        }

    def _verificar_activo(self, bid, para="escrituras"):
        if bid not in self.batches:
            raise BatchNotFound("no existe")
        if self.batches[bid]["status"] not in ("running", "failed"):
            raise BatchConflict("no admite " + para)

    def insertar_filas(self, batch_id, sheet_name, filas):
        bid = str(batch_id)
        self._verificar_activo(bid)
        vistas: dict[int, dict] = {}
        for fila in filas:
            vistas[int(fila["source_row_number"])] = fila
        for numero, fila in vistas.items():
            self.rows[(bid, sheet_name, numero)] = fila
        return len(vistas)

    def finalizar_lote(self, batch_id, expected_row_counts, client_checksum):
        bid = str(batch_id)
        self._verificar_activo(bid, "finalización")
        reales: dict[str, int] = {}
        hashes: dict[str, str] = {}
        for (b, s, _rn), fila in self.rows.items():
            if b != bid:
                continue
            reales[s] = reales.get(s, 0) + 1
            hashes.setdefault(s, "")
            hashes[s] += str(fila["payload"])

        for hoja in reales:
            if hoja not in HOJAS_PERMITIDAS:
                raise CountMismatch("hoja no permitida")

        esperados = {str(k): int(v) for k, v in expected_row_counts.items()}
        faltantes = sorted(set(HOJAS_PERMITIDAS) - set(esperados))
        if faltantes:
            raise CountMismatch("faltan hojas: " + ",".join(faltantes))
        extra = sorted(set(esperados) - set(HOJAS_PERMITIDAS))
        if extra:
            raise CountMismatch("hojas no permitidas: " + ",".join(extra))

        for hoja in set(reales) | set(esperados):
            if reales.get(hoja, 0) != esperados.get(hoja, 0):
                raise CountMismatch("conteo " + hoja)

        huesos = {
            hoja: hashlib.md5(hashes[hoja].encode("utf-8")).hexdigest()
            for hoja in hashes
        }
        material = "|".join(f"{k}:{huesos[k]}" for k in sorted(huesos))
        checksum = hashlib.sha256(material.encode("utf-8")).hexdigest()

        self.batches[bid]["status"] = "validated"
        self.batches[bid]["checksum"] = checksum
        self.batches[bid]["row_counts"] = esperados
        return {
            "batch_id": bid,
            "status": "validated",
            "row_counts": esperados,
            "checksum": checksum,
            "client_checksum": client_checksum,
        }


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MAX_BODY_BYTES", "65536")
    monkeypatch.setenv("MAX_ROWS_PER_REQUEST", "3")
    monkeypatch.setenv("MAX_PAYLOAD_KEYS", "4")
    monkeypatch.setenv("MAX_VALUE_LENGTH", "16")
    monkeypatch.setenv("MAX_ROW_NUMBER", "1000")


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {"X-Bridge-Token": TOKEN}
