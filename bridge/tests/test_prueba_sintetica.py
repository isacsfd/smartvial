"""Pruebas locales de la Fase B (script sintético). No escriben en Neon.

Se ejecuta el flujo completo contra la app FastAPI real con el repositorio en
memoria; el esquema y `active_migration` se inyectan como lecturas simuladas.
"""

from __future__ import annotations

import pytest

from scripts.prueba_sintetica_fase_b import (
    TABLAS_REQUERIDAS,
    ClienteBase,
    ErrorPrueba,
    _sanitizar,
    construir_conector,
    conteos_esperados,
    ejecutar_prueba,
    filas_sinteticas,
    nuevo_batch_id,
)
from src import main
from tests.conftest import TOKEN

BATCH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class ClienteASGI(ClienteBase):
    """Adapta el TestClient de FastAPI a la interfaz del script."""

    def __init__(self, test_client, token):
        super().__init__(token)
        self._cliente = test_client

    def _peticion(self, method, path, token, body):
        headers = {}
        if token is not None:
            headers["X-Bridge-Token"] = token
        if method == "GET":
            respuesta = self._cliente.get(path, headers=headers)
        else:
            respuesta = self._cliente.post(path, json=body, headers=headers)
        try:
            datos = respuesta.json()
        except Exception:
            datos = {}
        return respuesta.status_code, datos


@pytest.fixture(autouse=True)
def _neon_simulado(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://usuario:clave@host.neon.tech/db")
    monkeypatch.setattr(
        main,
        "comprobar_conexion_neon",
        lambda dsn: {"connected": True, "tls_enabled": True},
    )


def _sin_log(*_args, **_kwargs):
    pass


def test_conteos_cubren_17_hojas():
    conteos = conteos_esperados()
    assert len(conteos) == 17
    assert conteos["Empresas"] == 2
    assert conteos["Vehiculos"] == 2
    assert sum(v for k, v in conteos.items() if k not in ("Empresas", "Vehiculos")) == 0


def test_filas_sinteticas_coinciden_con_conteos():
    filas = filas_sinteticas()
    assert len(filas["Empresas"]) == 2
    assert len(filas["Vehiculos"]) == 2
    assert set(filas) == {"Empresas", "Vehiculos"}


def test_flujo_completo_ok(client, repo):
    cliente = ClienteASGI(client, TOKEN)
    resultado = ejecutar_prueba(
        cliente,
        batch_id=BATCH,
        spreadsheet_id="PRUEBA-FASE-B",
        verificar_esquema=lambda: set(TABLAS_REQUERIDAS),
        leer_active_migration=lambda: {"batch_id": None, "activated_at": None},
        log=_sin_log,
    )
    assert resultado["status"] == "validated"
    assert resultado["row_counts"]["Empresas"] == 2
    assert resultado["row_counts"]["Vehiculos"] == 2
    assert len(resultado["checksum"]) == 64
    assert resultado["active_migration"]["batch_id"] is None
    # Nunca se escribió ninguna fila de Usuarios.
    assert all(hoja != "Usuarios" for (_b, hoja, _n) in repo.rows)


def test_aborta_sin_esquema_y_no_escribe(client, repo):
    cliente = ClienteASGI(client, TOKEN)

    def esquema_faltante():
        raise ErrorPrueba("faltan tablas")

    with pytest.raises(ErrorPrueba):
        ejecutar_prueba(
            cliente,
            batch_id=BATCH,
            spreadsheet_id="PRUEBA-FASE-B",
            verificar_esquema=esquema_faltante,
            leer_active_migration=lambda: {"batch_id": None, "activated_at": None},
            log=_sin_log,
        )
    assert repo.batches == {}
    assert repo.rows == {}


def test_aborta_si_active_migration_activo(client, repo):
    cliente = ClienteASGI(client, TOKEN)
    with pytest.raises(ErrorPrueba):
        ejecutar_prueba(
            cliente,
            batch_id=BATCH,
            spreadsheet_id="PRUEBA-FASE-B",
            verificar_esquema=lambda: set(TABLAS_REQUERIDAS),
            leer_active_migration=lambda: {"batch_id": "lote-activo", "activated_at": "2026-01-01"},
            log=_sin_log,
        )
    assert repo.batches == {}


def test_construir_conector_sin_url(monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    assert construir_conector() is None


def test_payloads_cumplen_validaciones():
    for hoja, registros in filas_sinteticas().items():
        assert hoja in conteos_esperados()
        for fila in registros:
            assert fila["source_row_number"] >= 2
            assert len(fila["payload"]) <= 200
            for valor in fila["payload"].values():
                assert valor is None or isinstance(valor, (str, int, float, bool))
                if isinstance(valor, str):
                    assert len(valor) <= 50000


def test_nuevo_batch_id_unico():
    import uuid as _uuid

    primero = nuevo_batch_id()
    segundo = nuevo_batch_id()
    assert primero != segundo
    assert _uuid.UUID(primero).version == 4
    assert _uuid.UUID(segundo).version == 4


def test_fallo_de_conteo_no_activa_migracion(client):
    cliente = ClienteASGI(client, TOKEN)
    llamadas = {"n": 0}

    def active():
        llamadas["n"] += 1
        return {"batch_id": None, "activated_at": None}

    conteos = conteos_esperados()
    conteos["Empresas"] = 5  # incorrecto a propósito
    with pytest.raises(ErrorPrueba):
        ejecutar_prueba(
            cliente,
            batch_id=BATCH,
            spreadsheet_id="PRUEBA-FASE-B",
            verificar_esquema=lambda: set(TABLAS_REQUERIDAS),
            leer_active_migration=active,
            conteos=conteos,
            log=_sin_log,
        )
    # Se comprobó antes de escribir y tras el fallo.
    assert llamadas["n"] >= 2


def test_detecta_activacion_indebida(client):
    cliente = ClienteASGI(client, TOKEN)
    estado = {"n": 0}

    def active():
        estado["n"] += 1
        if estado["n"] == 1:
            return {"batch_id": None, "activated_at": None}
        return {"batch_id": "lote-activado", "activated_at": "2026-01-01"}

    conteos = conteos_esperados()
    conteos["Empresas"] = 5  # forzar fallo en finalize
    with pytest.raises(ErrorPrueba) as info:
        ejecutar_prueba(
            cliente,
            batch_id=BATCH,
            spreadsheet_id="PRUEBA-FASE-B",
            verificar_esquema=lambda: set(TABLAS_REQUERIDAS),
            leer_active_migration=active,
            conteos=conteos,
            log=_sin_log,
        )
    assert "activ" in str(info.value).lower()


def test_sanitizar_oculta_secretos(monkeypatch):
    monkeypatch.setenv(
        "NEON_DATABASE_URL", "postgresql://usuario:SuperSecreta@host.neon.tech/db"
    )
    monkeypatch.setenv("BRIDGE_TOKEN", "token-bridge-1234567890")
    texto = (
        "fallo postgresql://usuario:SuperSecreta@host.neon.tech/db "
        "token-bridge-1234567890 SuperSecreta"
    )
    limpio = _sanitizar(texto)
    assert "SuperSecreta" not in limpio
    assert "token-bridge-1234567890" not in limpio
    assert "host.neon.tech" not in limpio
