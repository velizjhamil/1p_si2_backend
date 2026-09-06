# backend/app/schemas/auth.py
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    """CU1 — cuerpo del POST /login."""

    correo: EmailStr
    password: str = Field(min_length=1)


class RolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_rol: UUID
    nombre_rol: str


class UserResponse(BaseModel):
    """Respuesta de usuario — NUNCA incluye el campo password (regla #6)."""

    model_config = ConfigDict(from_attributes=True)

    id_usuario: UUID
    nombre: str
    correo: EmailStr
    estado: bool
    rol: RolResponse


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int  # segundos hasta la expiración
    user: UserResponse
