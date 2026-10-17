"""Pruebas de /neon-check y /health. No conectan a Neon (se simula la comprobación)."""

from __future__ import annotations

import psycopg
import pytest

from src import main

URL = "postgresql://usuario:clave@host.neon.tech/db?sslmode=require"


def test_health_incluye_version(client):
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "ok"
    assert cuerpo["version"] == main.VERSION


def test_neon_check_sin_token_es_401(client):
    respuesta = client.get("/neon-check")
    assert respuesta.status_code == 401


def test_neon_check_ok(client, auth_headers, monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", URL)

    def fake_check(dsn):
        assert "sslmode=verify-full" in dsn
        return {
            "connected": True,
            "database": "neondb",
            "user": "usuario",
            "tls_enabled": True,
            "tls_required": True,
            "server_version": "17.0",
        }

    monkeypatch.setattr(main, "comprobar_conexion_neon", fake_check)
    respuesta = client.get("/neon-check", headers=auth_headers)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["connected"] is True
    assert cuerpo["tls_enabled"] is True


def test_neon_check_sin_url_es_503(client, auth_headers, monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    respuesta = client.get("/neon-check", headers=auth_headers)
    assert respuesta.status_code == 503


def test_neon_check_error_de_conexion_es_503(client, auth_headers, monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", URL)

    def fake_check(dsn):
        raise psycopg.OperationalError("fallo simulado de conexión")

    monkeypatch.setattr(main, "comprobar_conexion_neon", fake_check)
    respuesta = client.get("/neon-check", headers=auth_headers)
    assert respuesta.status_code == 503
    assert "traceback" not in respuesta.text.lower()
    assert "password" not in respuesta.text.lower()


def test_sanitizar_error_oculta_url_y_password(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", URL)
    monkeypatch.setenv("BRIDGE_TOKEN", "token-de-prueba")
    error = psycopg.OperationalError(
        "connection to server failed: " + URL + " password=clave"
    )
    mensaje = main._sanitizar_error(error)
    assert "clave" not in mensaje
    assert "usuario:clave" not in mensaje
    assert "token-de-prueba" not in mensaje
