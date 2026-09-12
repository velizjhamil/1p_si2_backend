# backend/app/schemas/sucursal.py
# Esquemas Pydantic para CU17 — Gestión de Sucursales (+ catálogo Ciudades).
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CiudadRead(BaseModel):
    """Respuesta del catálogo GET /api/v1/ciudades (dropdown del modal)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    departamento: str | None = None


class SucursalRead(BaseModel):
    """Sucursal con su ciudad asociada (GET /api/v1/sucursales y mutaciones).

    `ciudad` llega anidada gracias a la relationship lazy="joined" del modelo;
    `total_personal` cuenta usuarios GS/V asignados a la sucursal (0 mientras
    no exista la FK física usuarios.id_sucursal — CU futuros).
    """

    model_config = ConfigDict(from_attributes=True)

    codigo_sucursal: int
    nombre: str
    direccion: str | None = None
    telefono: str | None = None
    horario_atencion: str | None = None
    # estado Activo/Inactivo
    is_active: bool
    ciudad: CiudadRead
    empresa_id: int
    fecha_actualizacion: datetime | None = None


class SucursalCreate(BaseModel):
    """Payload de POST /api/v1/sucursales."""

    nombre: str = Field(min_length=2, max_length=100)
    id_ciudad: int
    direccion: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=30)
    horario_atencion: str | None = Field(default=None, max_length=100)


class SucursalUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/sucursales/{codigo_sucursal}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que EmpresaUpdate).
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    id_ciudad: int | None = None
    direccion: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=30)
    horario_atencion: str | None = Field(default=None, max_length=100)
