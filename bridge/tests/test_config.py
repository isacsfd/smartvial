"""Pruebas de configuración TLS. No conectan a Neon."""

from __future__ import annotations

import pytest

from src.config import cargar_settings, construir_dsn

URL = "postgresql://usuario:clave@host.neon.tech/db?sslmode=require"


@pytest.fixture(autouse=True)
def _url_neon(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", URL)


def test_verify_full_por_defecto():
    s = cargar_settings()
    assert s.sslmode == "verify-full"
    dsn = construir_dsn(s)
    assert "sslmode=verify-full" in dsn
    assert "sslrootcert=system" in dsn


def test_sslmode_inseguro_no_se_degrada(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "disable")
    s = cargar_settings()
    assert s.sslmode == "verify-full"
    assert "sslmode=disable" not in construir_dsn(s)


def test_sslrootcert_personalizado(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", "/etc/ssl/certs/neon.crt")
    assert "sslrootcert=/etc/ssl/certs/neon.crt" in construir_dsn(cargar_settings())


def test_require_no_incluye_rootcert(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "require")
    dsn = construir_dsn(cargar_settings())
    assert "sslmode=require" in dsn
    assert "sslrootcert" not in dsn


def test_falta_url_lanza_error(monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        construir_dsn(cargar_settings())
