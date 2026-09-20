# backend/app/schemas/agencia.py
# Esquemas Pydantic para CU19 - Gestion de Agencias de Reparto (agencias).
#
# Aca solo se valida la FORMA del payload (recorte de espacios, obligatorios no
# vacios, largos maximos). Las reglas de negocio (formato/consistencia del NIT
# y del correo de facturacion, razon social, duplicados, permisos) viven en
# app/modules/delivery/agencias_service.py, que es la unica fuente de esas
# reglas y responde con HTTPException (422 datos invalidos, 409 duplicados).
#
# `AgenciaResumen` es lo que ve el Encargado de Delivery (D): solo lo
# necesario para elegir/contactar una agencia. Los datos de facturacion
# (NIT, correo y direccion fiscal) solo se exponen a ASU/GS.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.envio import _limpiar  # recorta; texto vacio -> None


def _recortar(valor):
    """before-validator: recorta espacios de cualquier string. Un NUL (0x00) se
    rechaza (422): PostgreSQL no lo admite en texto y llegaria como un 500."""
    if isinstance(valor, str):
        if "\x00" in valor:
            raise ValueError("El texto no puede contener el caracter NUL (0x00).")
        return valor.strip()
    return valor


def _requerido_no_vacio(valor: str | None) -> str | None:
    """Un campo obligatorio que se envia no puede quedar vacio."""
    if valor is not None and not valor.strip():
        raise ValueError("Este campo es obligatorio y no puede estar vacio.")
    return valor


_OBLIGATORIOS = ("razon_social", "nit", "correo_facturacion", "direccion_fiscal")
_OPCIONALES = ("contacto_operativo", "telefono", "correo", "direccion")


# ---------------------------------------------------------------------------
# Payloads (entrada)
# ---------------------------------------------------------------------------
class AgenciaCreate(BaseModel):
    """Payload de POST /api/v1/agencias-reparto.

    Obligatorios: razon_social, nit, correo_facturacion y direccion_fiscal.
    `nit` puede venir con separadores (guiones, puntos, espacios): el service
    lo normaliza antes de validar y guardar.
    """

    razon_social: str = Field(max_length=150)
    nit: str = Field(max_length=30)
    correo_facturacion: str = Field(max_length=150)
    direccion_fiscal: str = Field(max_length=255)
    contacto_operativo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=150)
    direccion: str | None = Field(default=None, max_length=255)

    _recorta = field_validator(*_OBLIGATORIOS, *_OPCIONALES, mode="before")(_recortar)
    _obligatorios = field_validator(*_OBLIGATORIOS)(_requerido_no_vacio)
    _opcionales = field_validator(*_OPCIONALES)(_limpiar)


class AgenciaUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/agencias-reparto/{id}.

    None = "no cambiar" (misma semantica que proveedores). Un obligatorio que
    se envia no puede quedar vacio. No modifica `is_active` (eso es
    PATCH /estado) ni zonas/tarifas.
    """

    razon_social: str | None = Field(default=None, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    correo_facturacion: str | None = Field(default=None, max_length=150)
    direccion_fiscal: str | None = Field(default=None, max_length=255)
    contacto_operativo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=150)
    direccion: str | None = Field(default=None, max_length=255)

    _recorta = field_validator(*_OBLIGATORIOS, *_OPCIONALES, mode="before")(_recortar)
    _obligatorios = field_validator(*_OBLIGATORIOS)(_requerido_no_vacio)
    _opcionales = field_validator(*_OPCIONALES)(_limpiar)


class AgenciaEstadoPayload(BaseModel):
    """PATCH /{id}/estado: habilitar (true) o deshabilitar (false)."""

    is_active: bool


# ---------------------------------------------------------------------------
# Respuestas (salida)
# ---------------------------------------------------------------------------
class AgenciaResumen(BaseModel):
    """Vista de consulta (rol D): sin datos de facturacion."""

    model_config = ConfigDict(from_attributes=True)

    id_agencia: int
    razon_social: str
    contacto_operativo: str | None = None
    telefono: str | None = None
    is_active: bool


class AgenciaRead(AgenciaResumen):
    """Vista completa (ASU/GS): incluye datos de facturacion."""

    nit: str
    correo: str | None = None
    direccion: str | None = None
    correo_facturacion: str
    direccion_fiscal: str
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class AgenciaDetalle(AgenciaRead):
    """Detalle (ASU/GS). Las cantidades permiten a la UI saber si la agencia
    se puede eliminar fisicamente (`total_envios == 0`)."""

    total_zonas: int = 0
    total_envios: int = 0
