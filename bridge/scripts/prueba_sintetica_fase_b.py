"""Fase B: prueba sintética del puente de importación (datos ficticios).

Uso (Windows PowerShell / bash):

    # Variables (no se imprimen; no se guardan en el repositorio)
    $env:BRIDGE_URL   = "https://smartvial-neon-bridge.onrender.com"
    $env:BRIDGE_TOKEN = "<token>"
    $env:NEON_DATABASE_URL = "<url pooled de Neon>"   # necesaria para verificar el esquema

    python -m scripts.prueba_sintetica_fase_b --dry-run   # solo lectura
    python -m scripts.prueba_sintetica_fase_b             # ejecuta la prueba

Garantías:
- Solo envía datos ficticios y usa un UUID nuevo por ejecución.
- Verifica el esquema (4 tablas) y `active_migration` ANTES de cualquier
  escritura, leyendo Neon. Si no puede verificar el esquema, se detiene y NO
  envía peticiones de escritura.
- Nunca imprime el token ni la URL de conexión.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from src.config import cargar_settings, construir_dsn  # noqa: E402
from src.sheets import HOJAS_PERMITIDAS  # noqa: E402

TABLAS_REQUERIDAS = (
    "migration_batches",
    "legacy_sheet_rows",
    "active_migration",
    "api_request_nonces",
)

# Valor por defecto del parámetro `token` en las peticiones.
_POR_DEFECTO = object()


class ErrorPrueba(Exception):
    """Fallo de la prueba sintética."""


# --------------------------------------------------------------------------
# Cliente HTTP
# --------------------------------------------------------------------------
class ClienteBase:
    def __init__(self, token: str) -> None:
        self.token = token

    def _peticion(self, method: str, path: str, token, body):
        raise NotImplementedError

    def get(self, path: str, token=_POR_DEFECTO):
        return self._peticion("GET", path, self.token if token is _POR_DEFECTO else token, None)

    def post(self, path: str, body, token=_POR_DEFECTO):
        return self._peticion("POST", path, self.token if token is _POR_DEFECTO else token, body)


class ClienteHTTP(ClienteBase):
    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(token)
        self.base_url = base_url.rstrip("/")

    def _peticion(self, method: str, path: str, token, body):
        datos = json.dumps(body).encode("utf-8") if body is not None else None
        peticion = urllib.request.Request(self.base_url + path, data=datos, method=method)
        peticion.add_header("Content-Type", "application/json")
        if token is not None:
            peticion.add_header("X-Bridge-Token", token)
        try:
            with urllib.request.urlopen(peticion, timeout=60) as respuesta:
                texto = respuesta.read().decode("utf-8") or "{}"
                return respuesta.status, json.loads(texto)
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8") or "{}")
            except Exception:
                return error.code, {}


# --------------------------------------------------------------------------
# Verificaciones de solo lectura contra Neon
# --------------------------------------------------------------------------
def verificar_esquema(conectar) -> set[str]:
    """Confirma que existen las 4 tablas; lanza ErrorPrueba si falta alguna."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(TABLAS_REQUERIDAS),),
        )
        presentes = {str(fila[0]) for fila in cur.fetchall()}
    faltan = sorted(set(TABLAS_REQUERIDAS) - presentes)
    if faltan:
        raise ErrorPrueba("Faltan tablas en Neon: " + ", ".join(faltan))
    return presentes


def leer_active_migration(conectar) -> dict:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT batch_id, activated_at FROM active_migration WHERE singleton = TRUE")
        fila = cur.fetchone()
    return {
        "batch_id": str(fila[0]) if fila and fila[0] else None,
        "activated_at": fila[1] if fila else None,
    }


def construir_conector():
    """Devuelve un callable conectar() o None si no hay NEON_DATABASE_URL."""
    if not os.environ.get("NEON_DATABASE_URL", "").strip():
        return None
    dsn = construir_dsn(cargar_settings())

    def conectar():
        import psycopg

        return psycopg.connect(dsn)

    return conectar


# --------------------------------------------------------------------------
# Datos y conteos sintéticos
# --------------------------------------------------------------------------
def conteos_esperados() -> dict[str, int]:
    conteos = {hoja: 0 for hoja in HOJAS_PERMITIDAS}
    conteos["Empresas"] = 2
    conteos["Vehiculos"] = 2
    return conteos


def filas_sinteticas() -> dict[str, list[dict]]:
    return {
        "Empresas": [
            {
                "source_row_number": 2,
                "record_id": "EMPTEST1",
                "company_id": "EMPTEST1",
                "payload": {"ID_EMPRESA": "EMPTEST1", "NOMBRE_EMPRESA": "Ficticia Uno", "NIT": "000000001"},
            },
            {
                "source_row_number": 3,
                "record_id": "EMPTEST2",
                "company_id": "EMPTEST2",
                "payload": {"ID_EMPRESA": "EMPTEST2", "NOMBRE_EMPRESA": "Ficticia Dos", "NIT": "000000002"},
            },
        ],
        "Vehiculos": [
            {
                "source_row_number": 2,
                "record_id": "VEHTEST1",
                "company_id": "EMPTEST1",
                "payload": {"ID_VEHICULO": "VEHTEST1", "ID_EMPRESA": "EMPTEST1", "PLACA": "AAA000"},
            },
            {
                "source_row_number": 3,
                "record_id": "VEHTEST2",
                "company_id": "EMPTEST1",
                "payload": {"ID_VEHICULO": "VEHTEST2", "ID_EMPRESA": "EMPTEST1", "PLACA": "BBB111"},
            },
        ],
    }


def _esperar(condicion, mensaje: str) -> None:
    if not condicion:
        raise ErrorPrueba(mensaje)


def nuevo_batch_id() -> str:
    """UUID nuevo por ejecución; evita reutilizar un lote previo."""
    return str(uuid.uuid4())


def _asegurar_no_activado(leer_active) -> None:
    """Confirma que `active_migration` sigue sin activarse (mejor esfuerzo)."""
    try:
        activo = leer_active()
    except Exception:
        return  # no se pudo verificar; no enmascarar el fallo original
    if activo.get("batch_id") is not None:
        raise ErrorPrueba(
            "active_migration fue activado durante la prueba; no debe ocurrir."
        )


def _sanitizar(texto) -> str:
    """Elimina de un texto la URL de conexión, su contraseña y el token."""
    resultado = str(texto)
    for variable in ("NEON_DATABASE_URL", "BRIDGE_TOKEN"):
        valor = os.environ.get(variable, "")
        if valor:
            resultado = resultado.replace(valor, "[oculto]")
    try:
        from psycopg.conninfo import conninfo_to_dict

        url = os.environ.get("NEON_DATABASE_URL", "")
        if url:
            password = conninfo_to_dict(url).get("password")
            if password:
                resultado = resultado.replace(password, "[oculto]")
    except Exception:
        pass
    return resultado


# --------------------------------------------------------------------------
# Blocos de la prueba
# --------------------------------------------------------------------------
def comprobar_autenticacion(cliente, log=print) -> None:
    estado, _ = cliente.get("/neon-check", token=None)
    _esperar(estado == 401, f"Sin token se esperaba 401, se obtuvo {estado}.")
    estado, _ = cliente.get("/neon-check", token="token-invalido")
    _esperar(estado == 401, f"Con token inválido se esperaba 401, se obtuvo {estado}.")
    estado, datos = cliente.get("/neon-check")
    _esperar(estado == 200 and datos.get("connected") is True,
             f"/neon-check autenticado: estado={estado}, cuerpo={datos}.")
    log("[auth] 401 sin token / 401 con token inválido / 200 autenticado OK")


def ejecutar_prueba(
    cliente,
    *,
    batch_id: str,
    spreadsheet_id: str,
    verificar_esquema,
    leer_active_migration,
    conteos=None,
    filas=None,
    log=print,
) -> dict:
    """Ejecuta la Fase B. `verificar_esquema` y `leer_active_migration` son
    callables sin argumentos (inyectables para pruebas)."""
    conteos = conteos or conteos_esperados()
    filas = filas or filas_sinteticas()
    _esperar(verificar_esquema is not None, "No hay verificador de esquema.")
    _esperar(leer_active_migration is not None, "No hay lector de active_migration.")

    comprobar_autenticacion(cliente, log=log)

    # Pre-flight: esquema y estado de active_migration (solo lectura).
    presentes = verificar_esquema()
    _esperar(set(presentes) >= set(TABLAS_REQUERIDAS), "Esquema incompleto en Neon.")
    activo_antes = leer_active_migration()
    _esperar(activo_antes.get("batch_id") is None,
             "active_migration ya está activo; abortar.")
    log(f"[esquema] tablas OK; active_migration.batch_id={activo_antes.get('batch_id')}")

    try:
        # 1) Crear lote (idempotente).
        estado, datos = cliente.post("/import/batches", {
            "batch_id": batch_id,
            "spreadsheet_id": spreadsheet_id,
            "notes": json.dumps({"fase": "B", "sintetica": True}),
        })
        _esperar(estado == 200 and datos.get("status") == "running",
                 f"Crear lote: estado={estado}, cuerpo={datos}.")
        log(f"[lote] creado={datos.get('created')} id={batch_id}")

        # 2) Enviar solo las filas sintéticas (2 Empresas + 2 Vehiculos).
        for hoja, registros in filas.items():
            estado, datos = cliente.post("/import/rows", {
                "batch_id": batch_id, "sheet_name": hoja, "rows": registros,
            })
            _esperar(estado == 200, f"Insertar {hoja}: estado={estado}, cuerpo={datos}.")
            log(f"[filas] {hoja}: escritas={datos.get('rows_written')}")

        # 3) `Usuarios` debe rechazarse.
        estado, datos = cliente.post("/import/rows", {
            "batch_id": batch_id,
            "sheet_name": "Usuarios",
            "rows": [{"source_row_number": 2, "payload": {"Correo": "no@importar"}}],
        })
        _esperar(estado == 400, f"Usuarios debía dar 400, dio {estado}.")
        log("[exclusión] 'Usuarios' rechazada (400)")

        # 4) Finalizar con las 17 hojas (0 salvo las usadas).
        estado, datos = cliente.post(f"/import/batches/{batch_id}/finalize", {
            "expected_row_counts": conteos,
        })
        _esperar(estado == 200 and datos.get("status") == "validated",
                 f"Finalizar: estado={estado}, cuerpo={datos}.")
        _esperar(datos.get("row_counts") == conteos,
                 f"Conteos inesperados: {datos.get('row_counts')} != {conteos}.")
        checksum = str(datos.get("checksum") or "")
        _esperar(len(checksum) == 64, f"Checksum inválido: {checksum!r}.")
        log(f"[finalize] validated; checksum={checksum[:12]}…; hojas={len(conteos)}")

        # 5) Consultar estado final.
        estado, datos = cliente.get(f"/import/batches/{batch_id}")
        _esperar(estado == 200 and datos.get("status") == "validated",
                 f"Consultar lote: estado={estado}, cuerpo={datos}.")
        _esperar(str(datos.get("checksum")) == checksum,
                 "El checksum consultado no coincide.")
        log("[estado] lote validated y checksum coincide")
    except Exception:
        # Aunque la prueba falle (p. ej. conteo incorrecto), la migración no
        # debe haberse activado.
        _asegurar_no_activado(leer_active_migration)
        raise

    # 6) `active_migration` no se activó.
    activo_despues = leer_active_migration()
    _esperar(activo_despues.get("batch_id") is None,
             "active_migration fue activado; no debe ocurrir.")
    log("[active_migration] sin activar (batch_id=None)")

    return {
        "batch_id": batch_id,
        "status": "validated",
        "row_counts": conteos,
        "checksum": checksum,
        "active_migration": activo_despues,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fase B: prueba sintética del puente.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo comprobaciones de lectura; no escribe en Neon.")
    args = parser.parse_args(argv)

    base = os.environ.get("BRIDGE_URL", "").strip()
    token = os.environ.get("BRIDGE_TOKEN", "").strip()
    if not base or not token:
        print("Faltan BRIDGE_URL y/o BRIDGE_TOKEN en el entorno.")
        return 2

    cliente = ClienteHTTP(base, token)
    conectar = construir_conector()

    if conectar is None:
        print("NEON_DATABASE_URL ausente: no se puede verificar el esquema ni "
              "active_migration. Se ejecutan solo comprobaciones de lectura y NO "
              "se envía ninguna escritura.")
        comprobar_autenticacion(cliente)
        return 3

    def _verificar():
        return verificar_esquema(conectar)

    def _active():
        return leer_active_migration(conectar)

    if args.dry_run:
        comprobar_autenticacion(cliente)
        print("[esquema]", sorted(_verificar()))
        activo = _active()
        print("[active_migration]", activo)
        print("dry-run: sin escrituras.")
        return 0

    try:
        resultado = ejecutar_prueba(
            cliente,
            batch_id=nuevo_batch_id(),
            spreadsheet_id="PRUEBA-FASE-B",
            verificar_esquema=_verificar,
            leer_active_migration=_active,
        )
    except ErrorPrueba as error:
        print(f"PRUEBA FALLIDA: {_sanitizar(error)}")
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {_sanitizar(type(error).__name__ + ': ' + str(error))}")
        return 1
    print("PRUEBA OK:", json.dumps(resultado, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
