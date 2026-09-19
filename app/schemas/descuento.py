# backend/app/schemas/descuento.py
# Esquemas Pydantic para CU12 — Gestion de Descuentos / Cupones.
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Tipo de descuento: PORCENTAJE (0-100) o MONTO_FIJO (Bs.).
TIPOS_DESCUENTO = ("PORCENTAJE", "MONTO_FIJO")


class DescuentoBase(BaseModel):
    """Campos comunes a Create/Update."""

    codigo: str | None = Field(default=None, max_length=30)
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)
    tipo: str
    valor: Decimal = Field(gt=0, le=99999999.99)
    fecha_inicio: date
    fecha_fin: date | None = None
    activo: bool = True
    usos_maximos: int | None = Field(default=None, ge=1)
    monto_minimo_compra: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validar_tipo_y_fechas(self) -> "DescuentoBase":
        # 1) tipo dentro del dominio
        if self.tipo not in TIPOS_DESCUENTO:
            raise ValueError(
                f"Tipo de descuento inválido. Valores permitidos: "
                f"{', '.join(TIPOS_DESCUENTO)}."
            )
        # 2) PORCENTAJE debe estar entre 0 y 100
        if self.tipo == "PORCENTAJE" and self.valor > 100:
            raise ValueError(
                "Un descuento PORCENTAJE no puede superar 100 (valor = % de rebaja)."
            )
        # 3) fecha_fin >= fecha_inicio (si viene)
        if self.fecha_fin is not None and self.fecha_fin < self.fecha_inicio:
            raise ValueError(
                "La fecha de fin debe ser igual o posterior a la fecha de inicio."
            )
        return self


class DescuentoCreate(DescuentoBase):
    """Payload de POST /api/v1/descuentos."""


class DescuentoUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/descuentos/{id}.

    None = "no cambiar" (semantica PATCH sobre PUT, igual que el resto).
    """

    codigo: str | None = Field(default=None, max_length=30)
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)
    tipo: str | None = None
    valor: Decimal | None = Field(default=None, gt=0, le=99999999.99)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    activo: bool | None = None
    usos_maximos: int | None = Field(default=None, ge=1)
    monto_minimo_compra: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validar_update(self) -> "DescuentoUpdate":
        # Si vino tipo, validarlo contra el dominio
        if self.tipo is not None and self.tipo not in TIPOS_DESCUENTO:
            raise ValueError(
                f"Tipo de descuento inválido. Valores permitidos: "
                f"{', '.join(TIPOS_DESCUENTO)}."
            )
        # Si vino fecha_fin, validar vs fecha_inicio
        if (
            self.fecha_fin is not None
            and self.fecha_inicio is not None
            and self.fecha_fin < self.fecha_inicio
        ):
            raise ValueError(
                "La fecha de fin debe ser igual o posterior a la fecha de inicio."
            )
        return self


class DescuentoResponse(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/descuentos."""

    model_config = ConfigDict(from_attributes=True)

    id_descuento: int
    codigo: str | None
    nombre: str
    descripcion: str | None
    tipo: str
    valor: Decimal
    fecha_inicio: date
    fecha_fin: date | None
    activo: bool
    usos_maximos: int | None
    usos_actuales: int
    monto_minimo_compra: Decimal | None
    fecha_creacion: datetime
