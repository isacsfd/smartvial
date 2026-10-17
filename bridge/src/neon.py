"""Comprobación de conectividad real con Neon usando psycopg.

No usa `pg_stat_ssl`/`pg_backend_pid()` (frágiles con el endpoint *pooled* de
Neon): determina si la conexión usa TLS con el estado real del socket
(`pgconn.ssl_in_use`, equivalente a `PQsslInUse` de libpq).
"""

from __future__ import annotations

import psycopg


def comprobar_conexion_neon(dsn: str) -> dict:
    with psycopg.connect(dsn) as conn:
        ssl_en_uso = bool(getattr(conn.pgconn, "ssl_in_use", False))
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            fila = cur.fetchone()

    return {
        "connected": True,
        "database": fila[0] if fila else None,
        "user": fila[1] if fila and len(fila) > 1 else None,
        "tls_enabled": ssl_en_uso,
        "tls_required": True,
        "server_version": (
            str(fila[2]).split(",")[0] if fila and len(fila) > 2 and fila[2] else None
        ),
    }
