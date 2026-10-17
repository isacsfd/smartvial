"""Puente HTTPS para importar el snapshot de Apps Script hacia Neon.

Contrato (JSON):
  POST /import/batches            -> crea o reanuda un lote (idempotente)
  POST /import/rows               -> inserta/actualiza filas (upsert idempotente)
  POST /import/batches/{id}/finalize -> valida conteos y sella el lote
  GET  /import/batches/{id}       -> consulta el estado del lote
  GET  /neon-check                -> verifica la conexión con PostgreSQL
  GET  /health                    -> sonda sin autenticación

Todas las rutas de importación requieren la cabecera `X-Bridge-Token`.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .body_limit import LimiteCuerpoMiddleware
from .config import Settings, cargar_settings, construir_dsn, docs_habilitados
from .models import BatchRequest, FinalizeRequest, RowsRequest, validar_payload
from .neon import comprobar_conexion_neon
from .repository import (
    BatchConflict,
    BatchNotFound,
    CountMismatch,
    PostgresRepository,
)
from .security import get_settings, requerir_token
from .sheets import es_hoja_permitida, es_usuarios

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smartvial.bridge")

VERSION = "0.4.1"

_settings_arranque = cargar_settings()
_docs = "/docs" if docs_habilitados(_settings_arranque) else None

app = FastAPI(
    title="SmartVial Neon Bridge",
    version=VERSION,
    docs_url=_docs,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

# Limita los bytes reales del cuerpo (incluye transferencia fragmentada), no
# solo la cabecera Content-Length. Se evalúa por petición para respetar la
# configuración vigente.
app.add_middleware(
    LimiteCuerpoMiddleware,
    max_bytes_getter=lambda: cargar_settings().max_body_bytes,
)


def get_repo() -> PostgresRepository:
    return PostgresRepository(construir_dsn(cargar_settings()))


def _sanitizar_error(exc: Exception) -> str:
    """Mensaje de error sin secretos (oculta la URL y la contraseña)."""
    mensaje = f"{type(exc).__name__}: {exc}"
    settings = cargar_settings()
    secretos = [settings.bridge_token]
    if settings.database_url:
        secretos.append(settings.database_url)
        try:
            from psycopg.conninfo import conninfo_to_dict

            password = conninfo_to_dict(settings.database_url).get("password")
            if password:
                secretos.append(password)
        except Exception:
            pass
    for secreto in secretos:
        if secreto:
            mensaje = mensaje.replace(secreto, "[oculto]")
    return mensaje


@app.exception_handler(Exception)
async def error_interno(request, exc):  # pragma: no cover - defensivo
    logger.exception("Error no controlado en el puente.")
    return JSONResponse(
        status_code=500, content={"detail": "Error interno del servidor."}
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "smartvial-neon-bridge", "version": VERSION}


@app.get("/neon-check", dependencies=[Depends(requerir_token)])
def neon_check():
    try:
        return comprobar_conexion_neon(construir_dsn(cargar_settings()))
    except HTTPException:
        raise
    except Exception as exc:
        # El detalle se registra en el servidor y queda saneado; el cliente
        # recibe un mensaje genérico sin credenciales ni trazas internas.
        logger.error("neon-check falló: %s", _sanitizar_error(exc))
        raise HTTPException(
            status_code=503,
            detail="No se pudo verificar la conexión con PostgreSQL.",
        )


@app.post("/import/batches", dependencies=[Depends(requerir_token)])
def crear_lote(payload: BatchRequest, repo: PostgresRepository = Depends(get_repo)):
    try:
        return repo.crear_o_reanudar_lote(
            payload.batch_id, payload.spreadsheet_id, payload.notes
        )
    except BatchNotFound:
        raise HTTPException(status_code=404, detail="El lote no existe.")
    except BatchConflict:
        raise HTTPException(status_code=409, detail="El lote no admite esta operación.")
    except Exception:
        logger.exception("Fallo al registrar el lote.")
        raise HTTPException(status_code=503, detail="No se pudo registrar el lote.")


@app.get("/import/batches/{batch_id}", dependencies=[Depends(requerir_token)])
def consultar_lote(batch_id: UUID, repo: PostgresRepository = Depends(get_repo)):
    try:
        return repo.obtener_lote(batch_id)
    except BatchNotFound:
        raise HTTPException(status_code=404, detail="El lote no existe.")
    except Exception:
        logger.exception("Fallo al consultar el lote.")
        raise HTTPException(status_code=503, detail="No se pudo consultar el lote.")


@app.post("/import/rows", dependencies=[Depends(requerir_token)])
def insertar_filas(
    payload: RowsRequest,
    repo: PostgresRepository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    if es_usuarios(payload.sheet_name):
        raise HTTPException(status_code=400, detail="La hoja Usuarios no se importa.")
    if not es_hoja_permitida(payload.sheet_name):
        raise HTTPException(status_code=400, detail="Hoja no permitida.")

    if len(payload.rows) > settings.max_rows_per_request:
        raise HTTPException(status_code=413, detail="Demasiadas filas en la solicitud.")

    numeros = [fila.source_row_number for fila in payload.rows]
    if len(numeros) != len(set(numeros)):
        raise HTTPException(
            status_code=422,
            detail="La solicitud contiene números de fila duplicados.",
        )

    for fila in payload.rows:
        if fila.source_row_number > settings.max_row_number:
            raise HTTPException(
                status_code=422, detail="Número de fila fuera de rango."
            )
        error = validar_payload(fila.payload, settings)
        if error:
            raise HTTPException(status_code=422, detail=error)

    filas = [fila.model_dump() for fila in payload.rows]
    try:
        escritas = repo.insertar_filas(payload.batch_id, payload.sheet_name, filas)
    except BatchNotFound:
        raise HTTPException(status_code=404, detail="El lote no existe.")
    except BatchConflict:
        raise HTTPException(status_code=409, detail="El lote no admite escrituras.")
    except Exception:
        logger.exception("Fallo al insertar filas.")
        raise HTTPException(status_code=503, detail="No se pudieron escribir las filas.")

    return {
        "batch_id": str(payload.batch_id),
        "sheet_name": payload.sheet_name,
        "rows_written": escritas,
    }


@app.post(
    "/import/batches/{batch_id}/finalize",
    dependencies=[Depends(requerir_token)],
)
def finalizar_lote(
    batch_id: UUID,
    payload: FinalizeRequest,
    repo: PostgresRepository = Depends(get_repo),
):
    for hoja in payload.expected_row_counts:
        if es_usuarios(hoja):
            raise HTTPException(
                status_code=400, detail="La hoja Usuarios no se importa."
            )
        if not es_hoja_permitida(hoja):
            raise HTTPException(
                status_code=400, detail="Hoja no permitida en los conteos."
            )

    try:
        return repo.finalizar_lote(
            batch_id,
            payload.expected_row_counts,
            payload.client_checksum,
        )
    except BatchNotFound:
        raise HTTPException(status_code=404, detail="El lote no existe.")
    except BatchConflict:
        raise HTTPException(status_code=409, detail="El lote no admite finalización.")
    except CountMismatch as exc:
        # Mensaje controlado (solo nombres de hoja de la lista blanca/conteos).
        raise HTTPException(
            status_code=409,
            detail=str(exc) or "Los conteos del lote no coinciden.",
        )
    except Exception:
        logger.exception("Fallo al finalizar el lote.")
        raise HTTPException(status_code=503, detail="No se pudo finalizar el lote.")
