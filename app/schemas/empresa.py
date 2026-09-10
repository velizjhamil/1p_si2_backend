# backend/app/schemas/empresa.py
# Esquemas Pydantic para la empresa (CU16 — perfil institucional).
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmpresaRead(BaseModel):
    """Respuesta de GET /api/v1/empresa (primer registro existente)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    razon_social: str
    nit: str
    direccion: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    ciudad: str | None = None
    logo_url: str | None = None
    fecha_actualizacion: datetime | None = None


class EmpresaUpdate(BaseModel):
    """Payload parcial para PUT /api/v1/empresa.

    None = "no cambiar" (PATCH semantics sobre PUT). razon_social y nit
    son editables pero requeridos si se envían.
    """

    razon_social: str | None = Field(default=None, min_length=2)
    nit: str | None = Field(default=None, min_length=5)
    direccion: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    ciudad: str | None = None
    logo_url: str | None = None
