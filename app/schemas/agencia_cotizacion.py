# backend/app/schemas/agencia_cotizacion.py
# Esquemas Pydantic para CU19 - COTIZACION del costo de agencia para un envio.
#
# Es una consulta de solo lectura: no se persiste nada. `costo_agencia` es el
# costo INTERNO que se le pagaria a la agencia; no tiene relacion con
# `ventas.costo_envio` (lo que se cobra al cliente).
#
# La respuesta no incluye NIT ni datos de facturacion de la agencia.
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.agencia_disponible import CiudadConsultada


class AgenciaCotizada(BaseModel):
    id_agencia: int
    razon_social: str


class TarifaAplicada(BaseModel):
    """Tarifa elegida: la que determina el costo."""

    id_tarifa: int
    id_zona: int
    # Subzona de la zona (None = la tarifa es de la zona de ciudad completa).
    nombre_zona: str | None = None
    criterio: str
    rango_min: Decimal
    rango_max: Decimal | None = None
    costo: Decimal
    vigente_desde: date
    vigente_hasta: date | None = None


class CandidataCotizacion(BaseModel):
    """Toda tarifa que aplicaba a las dimensiones recibidas (la elegida y las
    descartadas), en orden de preferencia, para que la regla sea visible."""

    id_tarifa: int
    criterio: str
    costo: Decimal
    nombre_zona: str | None = None
    seleccionada: bool


class CotizacionRead(BaseModel):
    agencia: AgenciaCotizada
    ciudad: CiudadConsultada
    peso_kg: Decimal
    volumen_m3: Decimal
    # Fecha (UTC) con la que se evaluo la vigencia de las tarifas.
    fecha_referencia: date
    criterio: str
    costo_agencia: Decimal
    tarifa: TarifaAplicada
    candidatas: list[CandidataCotizacion]
