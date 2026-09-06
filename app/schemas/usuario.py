# backend/app/schemas/usuario.py
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UsuarioCreate(BaseModel):
    nombre: str
    correo: EmailStr
    password: str
    id_rol: UUID


class UsuarioUpdate(BaseModel):
    nombre: str | None = None
    correo: EmailStr | None = None
    password: str | None = None
    id_rol: UUID | None = None
    estado: bool | None = None


class UsuarioResponse(BaseModel):
    id_usuario: UUID
    id_rol: UUID
    nombre: str
    correo: EmailStr
    estado: bool
    rol: str | None = None  # nombre_rol via relationship

    class Config:
        from_attributes = True
