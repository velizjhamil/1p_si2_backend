# backend/app/schemas/permiso.py
# Esquemas Pydantic para el catálogo de permisos (CU5).
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PermisoCreate(BaseModel):
    """Payload para crear un permiso (Step 2: POST /api/v1/permisos)."""

    nombre: str  # ej: "usuarios.ver" — único en la tabla permisos
    descripcion: str | None = None
    modulo: str  # ej: "Usuarios", "Ventas" — agrupa el sidebar/matriz


class PermisoRead(BaseModel):
    """Permisos embebidos en respuestas (rol.permisos, usuario.permisos)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None = None
    modulo: str
    fecha_creacion: datetime
