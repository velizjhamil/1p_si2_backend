# backend/app/schemas/rol.py
# Esquemas Pydantic para roles (CU4).
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.permiso import PermisoRead


class RolCreate(BaseModel):
    """Payload para crear un rol junto a sus permisos (Step 2)."""

    nombre_rol: str = Field(min_length=2, examples=["GS"])
    descripcion: str | None = None
    # IDs de permisos a asignar; lista vacía = rol sin permisos
    permiso_ids: list[int] = []


class RolUpdate(BaseModel):
    """Payload parcial para actualizar un rol (Step 2).

    `None` significa "no cambiar" (PATCH semántico); permiso_ids=None
    NO limpia los permisos — para limpiar se envía una lista vacía [].
    """

    nombre_rol: str | None = Field(default=None, min_length=2)
    descripcion: str | None = None
    permiso_ids: list[int] | None = None


class RolPermisosUpdate(BaseModel):
    """Payload para reemplazar los permisos de un rol (matriz CU4+CU5).

    Estrategia replace: la lista enviada ES el estado final del rol;
    una lista vacía [] quita todos los permisos.
    """

    permiso_ids: list[int] = []


class RolRead(BaseModel):
    """Respuesta completa de un rol con sus permisos.

    cantidad_usuarios se resuelve desde la property Rol.cantidad_usuarios
    (decisión documentada): Rol.usuarios carga con lazy="selectin", por lo
    que la property NO emite queries adicionales al serializar.
    """

    model_config = ConfigDict(from_attributes=True)

    id_rol: UUID
    nombre_rol: str
    descripcion: str | None = None
    fecha_creacion: datetime
    permisos: list[PermisoRead] = []
    cantidad_usuarios: int = 0
