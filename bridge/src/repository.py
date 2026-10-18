"""Acceso a datos con consultas parametrizadas y transacciones.

Todas las sentencias usan un SQL fijo con parámetros; nunca se interpola
SQL dinámico con nombres de tabla o columna provenientes del cliente.
No se realizan operaciones destructivas (DROP/DELETE/TRUNCATE) y nunca se
modifica la tabla `active_migration` (más allá del singleton preexistente).
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

from .sheets import HOJAS_PERMITIDAS

_HOJAS_PERMITIDAS_SET = frozenset(HOJAS_PERMITIDAS)


class BatchNotFound(Exception):
    """El lote solicitado no existe."""


class BatchConflict(Exception):
    """El lote existe pero no admite la operación solicitada."""


class CountMismatch(Exception):
    """Los conteos persistidos no coinciden con los esperados."""


class NoActiveMigration(Exception):
    """No hay un lote activo para las lecturas operativas."""


def calcular_checksum_global(hashes_por_hoja: dict[str, str]) -> str:
    """SHA-256 estable sobre las huellas por hoja, ordenadas por nombre."""
    material = "|".join(
        f"{nombre}:{hashes_por_hoja[nombre]}" for nombre in sorted(hashes_por_hoja)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class PostgresRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conectar(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn)

    def crear_o_reanudar_lote(
        self, batch_id, spreadsheet_id: str, notes: str | None
    ) -> dict[str, Any]:
        bid = str(batch_id)
        with self._conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO migration_batches "
                    "(id, source_spreadsheet_id, status, notes) "
                    "VALUES (%s, %s, 'running', %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (bid, spreadsheet_id, notes),
                )
                creado = cur.rowcount == 1
                cur.execute(
                    "SELECT status, source_spreadsheet_id "
                    "FROM migration_batches WHERE id = %s",
                    (bid,),
                )
                fila = cur.fetchone()
                if fila is None:
                    raise BatchNotFound("El lote no existe.")
                estado, ssid = fila[0], fila[1]
                if str(ssid) != str(spreadsheet_id):
                    raise BatchConflict("El lote pertenece a otro spreadsheet.")
                if estado not in ("running", "failed"):
                    raise BatchConflict("El lote no admite reanudación.")
                if estado == "failed":
                    cur.execute(
                        "UPDATE migration_batches SET status = 'running', "
                        "notes = COALESCE(%s, notes) WHERE id = %s",
                        (notes, bid),
                    )
            conn.commit()
        return {"batch_id": bid, "status": "running", "created": creado}

    def obtener_lote(self, batch_id) -> dict[str, Any]:
        bid = str(batch_id)
        with self._conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, source_spreadsheet_id, status, row_counts, checksum "
                    "FROM migration_batches WHERE id = %s",
                    (bid,),
                )
                fila = cur.fetchone()
        if fila is None:
            raise BatchNotFound("El lote no existe.")
        return {
            "batch_id": str(fila[0]),
            "spreadsheet_id": fila[1],
            "status": fila[2],
            "row_counts": fila[3],
            "checksum": fila[4],
        }

    def _estado_lote(self, cur, bid: str) -> str:
        cur.execute("SELECT status FROM migration_batches WHERE id = %s", (bid,))
        fila = cur.fetchone()
        if fila is None:
            raise BatchNotFound("El lote no existe.")
        return fila[0]

    def resumen_filas_lote(
        self, batch_id, sheet_name: str | None, limite: int
    ) -> dict[str, Any]:
        """Lectura de diagnóstico de un lote (solo metadatos, sin payloads).

        No depende de `active_migration`: usa el `batch_id` explícito. Solo
        devuelve los conteos por hoja de la lista blanca y, si se pide una
        hoja concreta, una muestra mínima (número de fila y presencia de
        identificadores/claves), nunca los valores del payload.
        """
        bid = str(batch_id)
        with self._conectar() as conn:
            with conn.cursor() as cur:
                estado = self._estado_lote(cur, bid)
                cur.execute(
                    "SELECT sheet_name, count(*) FROM legacy_sheet_rows "
                    "WHERE batch_id = %s GROUP BY sheet_name",
                    (bid,),
                )
                conteos = {
                    str(nombre): int(total)
                    for nombre, total in cur.fetchall()
                    if str(nombre) in _HOJAS_PERMITIDAS_SET
                }
                resultado: dict[str, Any] = {
                    "batch_id": bid,
                    "status": estado,
                    "hojas": conteos,
                }
                if sheet_name is not None:
                    cur.execute(
                        "SELECT source_row_number, record_id, company_id, payload "
                        "FROM legacy_sheet_rows "
                        "WHERE batch_id = %s AND sheet_name = %s "
                        "ORDER BY source_row_number LIMIT %s",
                        (bid, sheet_name, int(limite)),
                    )
                    filas = [
                        {
                            "source_row_number": int(numero),
                            "claves": len(payload) if isinstance(payload, dict) else 0,
                            "tiene_record_id": bool(registro),
                            "tiene_company_id": bool(empresa),
                        }
                        for numero, registro, empresa, payload in cur.fetchall()
                    ]
                    resultado["sheet_name"] = sheet_name
                    resultado["filas"] = filas
                return resultado

    def leer_filas_empresa(
        self, sheet_name: str, company_id: str, batch_id=None
    ) -> dict[str, Any]:
        """Lectura operativa de una hoja/empresa en el lote activo o indicado.

        Solo lectura. Si no hay lote activo, lanza `NoActiveMigration` en vez
        de devolver una lista vacía (no se ocultan fallos como datos vacíos).
        """
        with self._conectar() as conn:
            with conn.cursor() as cur:
                if batch_id is None:
                    cur.execute(
                        "SELECT batch_id FROM active_migration WHERE singleton = TRUE"
                    )
                    fila = cur.fetchone()
                    if not fila or fila[0] is None:
                        raise NoActiveMigration("No hay un lote activo.")
                    bid = str(fila[0])
                else:
                    bid = str(batch_id)
                    self._estado_lote(cur, bid)

                cur.execute(
                    "SELECT source_row_number, payload FROM legacy_sheet_rows "
                    "WHERE batch_id = %s AND sheet_name = %s AND company_id = %s "
                    "ORDER BY source_row_number",
                    (bid, sheet_name, company_id),
                )
                filas = [
                    {"source_row_number": int(numero), "payload": payload}
                    for numero, payload in cur.fetchall()
                ]
        return {
            "batch_id": bid,
            "sheet_name": sheet_name,
            "company_id": company_id,
            "rows": filas,
        }

    def insertar_filas(
        self, batch_id, sheet_name: str, filas: Iterable[dict[str, Any]]
    ) -> int:
        bid = str(batch_id)
        # Deduplicación defensiva por número de fila (la última fila gana, igual
        # que el UPSERT). La ruta ya rechaza duplicados, pero así el conteo
        # devuelto siempre coincide con lo persistido.
        vistas: dict[tuple[str, int], dict[str, Any]] = {}
        for fila in filas:
            vistas[(sheet_name, int(fila["source_row_number"]))] = fila

        parametros = [
            (
                bid,
                nombre,
                numero,
                fila.get("record_id") or None,
                fila.get("company_id") or None,
                Jsonb(fila["payload"]),
            )
            for (nombre, numero), fila in vistas.items()
        ]
        if not parametros:
            return 0
        with self._conectar() as conn:
            with conn.cursor() as cur:
                estado = self._estado_lote(cur, bid)
                if estado not in ("running", "failed"):
                    raise BatchConflict("El lote no admite escrituras.")
                cur.executemany(
                    "INSERT INTO legacy_sheet_rows "
                    "(batch_id, sheet_name, source_row_number, record_id, company_id, payload) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (batch_id, sheet_name, source_row_number) DO UPDATE SET "
                    "payload = EXCLUDED.payload, "
                    "record_id = EXCLUDED.record_id, "
                    "company_id = EXCLUDED.company_id, "
                    "imported_at = now()",
                    parametros,
                )
            conn.commit()
        return len(parametros)

    def finalizar_lote(
        self,
        batch_id,
        expected_row_counts: dict[str, int],
        client_checksum: str | None,
    ) -> dict[str, Any]:
        bid = str(batch_id)
        with self._conectar() as conn:
            with conn.cursor() as cur:
                estado = self._estado_lote(cur, bid)
                if estado not in ("running", "failed"):
                    raise BatchConflict("El lote no admite finalización.")

                cur.execute(
                    "SELECT sheet_name, count(*) FROM legacy_sheet_rows "
                    "WHERE batch_id = %s GROUP BY sheet_name",
                    (bid,),
                )
                reales = {str(s): int(n) for s, n in cur.fetchall()}

                for hoja in reales:
                    if hoja not in _HOJAS_PERMITIDAS_SET:
                        raise CountMismatch("El lote contiene una hoja no permitida.")

                esperados = {str(k): int(v) for k, v in expected_row_counts.items()}

                # Integridad: deben estar TODAS las hojas de la lista blanca,
                # incluidas las de cero filas. Así una hoja olvidada por el
                # cliente no puede finalizar como si estuviera vacía.
                faltantes = sorted(_HOJAS_PERMITIDAS_SET - set(esperados))
                if faltantes:
                    raise CountMismatch(
                        "Faltan hojas en la finalización: " + ", ".join(faltantes)
                    )
                extra = sorted(set(esperados) - _HOJAS_PERMITIDAS_SET)
                if extra:
                    raise CountMismatch(
                        "Hay hojas no permitidas en la finalización: " + ", ".join(extra)
                    )

                for hoja in set(reales) | set(esperados):
                    if reales.get(hoja, 0) != esperados.get(hoja, 0):
                        raise CountMismatch(
                            "Los conteos no coinciden para la hoja: " + str(hoja)
                        )

                cur.execute(
                    "SELECT sheet_name, "
                    "md5(string_agg(md5(payload::text), '' ORDER BY source_row_number)) "
                    "FROM legacy_sheet_rows WHERE batch_id = %s GROUP BY sheet_name",
                    (bid,),
                )
                hashes = {str(s): (h or "") for s, h in cur.fetchall()}
                checksum = calcular_checksum_global(hashes)

                cur.execute(
                    "UPDATE migration_batches SET status = 'validated', "
                    "completed_at = now(), row_counts = %s, checksum = %s "
                    "WHERE id = %s",
                    (Jsonb(esperados), checksum, bid),
                )
            conn.commit()

        return {
            "batch_id": bid,
            "status": "validated",
            "row_counts": esperados,
            "checksum": checksum,
            "client_checksum": client_checksum,
        }
