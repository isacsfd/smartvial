"""Ejecuta las pruebas del importador Apps Script (LIMITE_FILAS) vía Node.

Se salta si `node` no está disponible. No toca Google Sheets ni Neon: el
arnés simula los globals de Apps Script.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
_HARNESS = _RAIZ / "apps_script" / "tests" / "limite_filas.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_apps_script_limite_filas():
    proceso = subprocess.run(
        ["node", str(_HARNESS)],
        cwd=str(_RAIZ),
        capture_output=True,
        text=True,
    )
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr
