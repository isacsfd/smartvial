"""Lista blanca de hojas. `Usuarios` se rechaza siempre de forma explícita."""

from __future__ import annotations

HOJA_EXCLUIDA = "Usuarios"
HOJA_EMPRESAS = "Empresas"

# Lista exacta de hojas operativas permitidas para el snapshot.
HOJAS_PERMITIDAS = (
    "Empresas",
    "PESV",
    "Vehiculos",
    "Conductores",
    "Documentos",
    "EvaluacionesIA",
    "Actividades",
    "Inspecciones",
    "Mantenimientos",
    "Siniestros",
    "Paso5Diagnostico",
    "Paso5Contratistas",
    "Paso5Colaboradores",
    "Paso5Rutas",
    "Paso5Encuestas",
    "Historial",
    "Diagnosticos",
)

_HOJAS_PERMITIDAS_SET = frozenset(HOJAS_PERMITIDAS)


def es_usuarios(nombre: str) -> bool:
    return str(nombre).strip().casefold() == HOJA_EXCLUIDA.casefold()


def es_empresas(nombre: str) -> bool:
    return str(nombre).strip() == HOJA_EMPRESAS


def es_hoja_permitida(nombre: str) -> bool:
    """Solo acepta coincidencia exacta con la lista blanca y nunca Usuarios."""
    if es_usuarios(nombre):
        return False
    return str(nombre).strip() in _HOJAS_PERMITIDAS_SET
