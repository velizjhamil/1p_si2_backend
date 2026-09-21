# backend/app/schemas/proveedor.py
# Esquemas Pydantic para CU23 — Gestión de Proveedores.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Estados válidos del proveedor (CU23): tri-estado con badge de color en UI
ESTADOS_PROVEEDOR = ("Activo", "Verificado", "Inactivo")


class ProveedorRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/proveedores."""

    model_config = ConfigDict(from_attributes=True)

    id_proveedor: int
    nombre: str
    nit_rut: str
    contacto_operativo: str | None = None
    telefono: str | None = None
    correo: str | None = None
    categoria: str | None = None
    estado: str
    direccion: str | None = None
    sucursal_id: int | None = None
    sucursal_nombre: str | None = None
    fecha_actualizacion: datetime | None = None


class ProveedorCreate(BaseModel):
    """Payload de POST /api/v1/proveedores — campos obligatorios del CU23."""

    nombre: str = Field(min_length=2, max_length=150)
    nit_rut: str = Field(min_length=5, max_length=50)
    contacto_operativo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    correo: EmailStr | None = None
    categoria: str | None = Field(default=None, max_length=100)
    estado: str = Field(default="Activo")
    direccion: str | None = Field(default=None, max_length=255)
    sucursal_id: int | None = None


class ProveedorUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/proveedores/{id}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que el resto).
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    nit_rut: str | None = Field(default=None, min_length=5, max_length=50)
    contacto_operativo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    correo: EmailStr | None = None
    categoria: str | None = Field(default=None, max_length=100)
    estado: str | None = None
    direccion: str | None = Field(default=None, max_length=255)
    sucursal_id: int | None = None
