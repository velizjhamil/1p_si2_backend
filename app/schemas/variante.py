# backend/app/schemas/variante.py
# Esquemas Pydantic para CU7 — Gestión de Tallas y Colores.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Regex de código HEX válido: #RRGGBB (6 dígitos hexadecimales).
HEX_PATTERN = r"^#[0-9a-fA-F]{6}$"


# ---------------------------------------------------------------------------
# Tallas
# ---------------------------------------------------------------------------
class TallaRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/tallas."""

    model_config = ConfigDict(from_attributes=True)

    id_talla: int
    nombre_talla: str
    descripcion: str | None = None
    activo: bool
    fecha_creacion: datetime | None = None


class TallaCreate(BaseModel):
    """Payload de POST /api/v1/tallas — nombre obligatorio y único."""

    nombre_talla: str = Field(min_length=1, max_length=20)
    descripcion: str | None = Field(default=None, max_length=255)


class TallaUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/tallas/{id}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que el resto).
    """

    nombre_talla: str | None = Field(default=None, min_length=1, max_length=20)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool | None = None


# ---------------------------------------------------------------------------
# Colores
# ---------------------------------------------------------------------------
class ColorRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/colores.

    `codigo_hex` sale en minúsculas normalizadas para el frontend.
    """

    model_config = ConfigDict(from_attributes=True)

    id_color: int
    nombre_color: str
    codigo_hex: str
    descripcion: str | None = None
    activo: bool
    fecha_creacion: datetime | None = None


class ColorCreate(BaseModel):
    """Payload de POST /api/v1/colores.

    Regla de negocio CU7: codigo_hex debe ser formato #RRGGBB (422 si no).
    Nombre y HEX son únicos en DB (409 en el router si ya existen).
    """

    nombre_color: str = Field(min_length=2, max_length=50)
    codigo_hex: str = Field(pattern=HEX_PATTERN, max_length=7)
    descripcion: str | None = Field(default=None, max_length=255)


class ColorUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/colores/{id}.

    None = "no cambiar". El HEX, si viene, se re-valida con la regex.
    """

    nombre_color: str | None = Field(default=None, min_length=2, max_length=50)
    codigo_hex: str | None = Field(default=None, pattern=HEX_PATTERN, max_length=7)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool | None = None
