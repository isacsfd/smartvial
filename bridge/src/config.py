"""Configuración del puente leída desde variables de entorno.

Nunca se guardan secretos en el repositorio: NEON_DATABASE_URL y BRIDGE_TOKEN
se inyectan como variables de entorno (Render: `sync: false`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo

# Modos de TLS aceptados. Se evita cualquier valor que permita una conexión
# sin cifrar ("disable", "allow", "prefer"). El modo más seguro es verify-full:
# cifra y además valida el certificado del servidor contra una CA de confianza.
_SSLMODES_SEGUROS = frozenset({"require", "verify-ca", "verify-full"})
_DEFAULT_SSLMODE = "verify-full"
# CA a usar con verify-ca/verify-full. "certifi" resuelve al bundle de CA que
# incluye el propio wheel (portable en Render); "system" delega en el almacén
# del SO; cualquier otro valor se trata como ruta a un archivo PEM.
_DEFAULT_SSLROOTCERT = "certifi"


def _resolver_sslrootcert(valor: str) -> str:
    """Resuelve 'certifi'/'system'/ruta. Nunca devuelve un valor vacío."""
    valor = (valor or "").strip()
    if valor and valor.lower() != "certifi":
        return valor
    try:
        import certifi

        return certifi.where()
    except Exception:
        # Sin certifi, se delega en el almacén del sistema.
        return "system"

_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB por petición
_DEFAULT_MAX_ROWS_PER_REQUEST = 500  # el importador envía bloques de 100
_DEFAULT_MAX_PAYLOAD_KEYS = 200  # la hoja más ancha documentada es Diagnosticos (52)
# Tope de una celda de Google Sheets (50 000 caracteres). Así no se rechaza
# ningún valor que Sheets pueda contener realmente.
_DEFAULT_MAX_VALUE_LENGTH = 50_000
_DEFAULT_MAX_ROW_NUMBER = 1_000_000


def _entero(nombre: str, por_defecto: int) -> int:
    crudo = os.getenv(nombre, "").strip()
    if not crudo:
        return por_defecto
    try:
        valor = int(crudo)
    except ValueError:
        return por_defecto
    return valor if valor > 0 else por_defecto


def _booleano(nombre: str, por_defecto: bool = False) -> bool:
    crudo = os.getenv(nombre, "").strip().lower()
    if not crudo:
        return por_defecto
    return crudo in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bridge_token: str
    database_url: str
    sslmode: str
    sslrootcert: str
    connect_timeout: int
    environment: str
    max_body_bytes: int
    max_rows_per_request: int
    max_payload_keys: int
    max_value_length: int
    max_row_number: int

    @property
    def produccion(self) -> bool:
        return self.environment == "production"


def cargar_settings() -> Settings:
    sslmode = os.getenv("PG_SSLMODE", _DEFAULT_SSLMODE).strip().lower()
    if sslmode not in _SSLMODES_SEGUROS:
        # Valor inseguro o desconocido: se eleva al modo seguro, nunca se degrada.
        sslmode = _DEFAULT_SSLMODE
    return Settings(
        bridge_token=os.getenv("BRIDGE_TOKEN", "").strip(),
        database_url=os.getenv("NEON_DATABASE_URL", "").strip(),
        sslmode=sslmode,
        sslrootcert=_resolver_sslrootcert(os.getenv("PG_SSLROOTCERT", _DEFAULT_SSLROOTCERT)),
        connect_timeout=_entero("PG_CONNECT_TIMEOUT", 10),
        environment=os.getenv("ENVIRONMENT", "development").strip().lower() or "development",
        max_body_bytes=_entero("MAX_BODY_BYTES", _DEFAULT_MAX_BODY_BYTES),
        max_rows_per_request=_entero("MAX_ROWS_PER_REQUEST", _DEFAULT_MAX_ROWS_PER_REQUEST),
        max_payload_keys=_entero("MAX_PAYLOAD_KEYS", _DEFAULT_MAX_PAYLOAD_KEYS),
        max_value_length=_entero("MAX_VALUE_LENGTH", _DEFAULT_MAX_VALUE_LENGTH),
        max_row_number=_entero("MAX_ROW_NUMBER", _DEFAULT_MAX_ROW_NUMBER),
    )


def construir_dsn(settings: Settings) -> str:
    """Construye el DSN forzando TLS. Jamás acepta un sslmode inseguro."""
    if not settings.database_url:
        raise RuntimeError("Falta configurar NEON_DATABASE_URL en el servidor.")

    sslmode = settings.sslmode if settings.sslmode in _SSLMODES_SEGUROS else _DEFAULT_SSLMODE
    parametros = {
        "sslmode": sslmode,
        "connect_timeout": settings.connect_timeout,
    }
    if sslmode in ("verify-ca", "verify-full"):
        # Requiere una CA para validar el certificado del servidor. Si el valor
        # no apunta a una CA válida, la conexión falla (fail-closed).
        parametros["sslrootcert"] = settings.sslrootcert
    return make_conninfo(settings.database_url, **parametros)


def docs_habilitados(settings: Settings) -> bool:
    """OpenAPI se publica solo fuera de producción o con bandera explícita."""
    if _booleano("BRIDGE_ENABLE_DOCS"):
        return True
    if _booleano("BRIDGE_DISABLE_DOCS"):
        return False
    return not settings.produccion
