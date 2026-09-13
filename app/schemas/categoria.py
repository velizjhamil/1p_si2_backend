# backend/app/schemas/categoria.py
# Esquemas Pydantic para CU9 — Gestión de Categorías.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Líneas de prenda válidas del CU9
LINEAS_CATEGORIA = ("Hombre", "Mujer", "Unisex")


class CategoriaRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/categorias."""

    model_config = ConfigDict(from_attributes=True)

    id_categoria: int
    nombre: str
    linea: str
    descripcion: str | None = None
    activo: bool
    fecha_creacion: datetime | None = None


class CategoriaCreate(BaseModel):
    """Payload de POST /api/v1/categorias — nombre y línea obligatorios."""

    nombre: str = Field(min_length=2, max_length=100)
    linea: str = Field(max_length=50)
    descripcion: str | None = Field(default=None, max_length=255)


class CategoriaUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/categorias/{id}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que el resto).
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    linea: str | None = Field(default=None, max_length=50)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool | None = None
