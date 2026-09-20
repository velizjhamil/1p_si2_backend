# backend/app/schemas/agencia_disponible.py
# Esquemas Pydantic para CU19 - consulta de agencias DISPONIBLES para una ciudad.
#
# Reutiliza `AgenciaResumen` (id, razon_social, contacto, telefono, is_active):
# es la misma vista de consulta que recibe el rol D, sin NIT ni datos de
# facturacion, asi que ASU, GS y D reciben exactamente la misma forma. Solo
# agrega el indicador de cobertura de la ciudad consultada.
#
# NO contiene tarifas ni costos: disponibilidad es solo cobertura. El costo se
# obtiene con la cotizacion (schemas/agencia_cotizacion.py).
from pydantic import BaseModel

from app.schemas.agencia import AgenciaResumen


class AgenciaDisponible(AgenciaResumen):
    """Agencia habilitada con al menos una zona en la ciudad consultada."""

    cubre_ciudad: bool = True
    # True si la agencia tiene una zona SIN subzona (cubre toda la ciudad);
    # False si solo cubre subzonas (barrios/zonas) de la ciudad.
    cobertura_completa: bool
    # Cantidad de zonas de la agencia en esa ciudad (una agencia aparece UNA vez).
    zonas_en_ciudad: int


class CiudadConsultada(BaseModel):
    """Ciudad del catalogo a la que se resolvio el nombre consultado."""

    id_ciudad: int
    nombre: str
    departamento: str | None = None
