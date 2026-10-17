"""Pruebas de configuración TLS. No conectan a Neon."""

from __future__ import annotations

import os

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
    assert "sslrootcert=" in dsn


def test_sslrootcert_por_defecto_es_archivo_existente():
    # Debe resolver a un bundle real (certifi) y no a "system".
    s = cargar_settings()
    assert s.sslrootcert != "system"
    assert os.path.isfile(s.sslrootcert)


def test_sslrootcert_system_se_respeta(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", "system")
    s = cargar_settings()
    assert s.sslrootcert == "system"
    assert "sslrootcert=system" in construir_dsn(s)


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
