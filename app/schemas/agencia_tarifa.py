# backend/app/schemas/agencia_tarifa.py
# Esquemas Pydantic para CU19 - tarifas de una zona de cobertura.
#
# Una tarifa cobra por UN solo `criterio` (PESO en kg o VOLUMEN en m3) y aplica
# a un rango y a un periodo de vigencia:
#
#   RANGO      [rango_min, rango_max)  -> el minimo se INCLUYE y el maximo NO.
#              `rango_max` = null es el ULTIMO TRAMO ABIERTO ("de rango_min en
#              adelante", sin tope). Tramos contiguos ([0, 5) y [5, 10)) no se
#              solapan.
#   VIGENCIA   [vigente_desde, vigente_hasta] -> ambas fechas se INCLUYEN.
#              `vigente_hasta` = null es vigencia abierta (sin fin).
#
# Aca solo se valida la FORMA de cada campo (tipos, no negativos, decimales
# maximos que caben en la columna). Las reglas entre campos (max > min,
# hasta >= desde) y el solapamiento viven en
# app/modules/delivery/tarifas_service.py.
#
# UPDATE (PUT): un campo ausente = no cambiar. Para `rango_max` y
# `vigente_hasta`, null es un valor con significado, asi que `null` explicito
# = "tramo abierto" / "sin fin". Los demas campos no aceptan null.
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Criterio = Literal["PESO", "VOLUMEN"]


def _criterio_en_mayusculas(valor):
    return valor.strip().upper() if isinstance(valor, str) else valor


# Los limites replican Numeric(10, 3) (rangos) y Numeric(10, 2) (costo): con mas
# digitos la DB fallaria con un error de desbordamiento; asi es un 422 claro.
class TarifaCreate(BaseModel):
    """POST /api/v1/agencias-reparto/{id}/zonas/{id_zona}/tarifas."""

    criterio: Criterio
    rango_min: Decimal = Field(ge=0, max_digits=10, decimal_places=3)
    rango_max: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    costo: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    vigente_desde: date
    vigente_hasta: date | None = None
    is_active: bool = True

    _mayusculas = field_validator("criterio", mode="before")(_criterio_en_mayusculas)


_NO_NULABLES = ("criterio", "rango_min", "costo", "vigente_desde", "is_active")


class TarifaUpdate(BaseModel):
    """PUT .../tarifas/{id_tarifa}: solo los campos enviados cambian."""

    criterio: Criterio | None = None
    rango_min: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    rango_max: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    costo: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    is_active: bool | None = None

    _mayusculas = field_validator("criterio", mode="before")(_criterio_en_mayusculas)

    @model_validator(mode="after")
    def _solo_dos_campos_aceptan_null(self):
        for campo in _NO_NULABLES:
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"'{campo}' no acepta null. Solo 'rango_max' y 'vigente_hasta' lo aceptan.")
        return self


class TarifaRead(BaseModel):
    """Tarifa de una zona. `vigente` = activa y con la fecha de hoy (UTC) dentro
    de su vigencia (es lo que la cotizacion considera aplicable)."""

    id_tarifa: int
    id_zona: int
    id_agencia: int
    criterio: str
    rango_min: Decimal
    rango_max: Decimal | None = None
    costo: Decimal
    vigente_desde: date
    vigente_hasta: date | None = None
    is_active: bool
    vigente: bool
    fecha_creacion: datetime
    fecha_actualizacion: datetime
