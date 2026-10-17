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


def test_sslrootcert_system_cae_en_certifi(monkeypatch):
    # Un "system" heredado no debe romper verify-full: se usa el bundle certifi.
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", "system")
    s = cargar_settings()
    assert s.sslrootcert != "system"
    assert os.path.isfile(s.sslrootcert)


def test_sslrootcert_personalizado(tmp_path, monkeypatch):
    ca = tmp_path / "neon.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", str(ca))
    s = cargar_settings()
    assert s.sslrootcert == str(ca)
    dsn = construir_dsn(s)
    assert "sslrootcert=" in dsn
    assert "cacert.pem" not in dsn  # no se usó el bundle certifi


def test_sslrootcert_inexistente_cae_en_certifi(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", "/ruta/que/no/existe.crt")
    s = cargar_settings()
    assert os.path.isfile(s.sslrootcert)


def test_sslmode_inseguro_no_se_degrada(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "disable")
    s = cargar_settings()
    assert s.sslmode == "verify-full"
    assert "sslmode=disable" not in construir_dsn(s)


def test_require_no_incluye_rootcert(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "require")
    dsn = construir_dsn(cargar_settings())
    assert "sslmode=require" in dsn
    assert "sslrootcert" not in dsn


def test_falta_url_lanza_error(monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        construir_dsn(cargar_settings())
