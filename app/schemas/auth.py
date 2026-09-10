# backend/app/schemas/auth.py
from pydantic import BaseModel, EmailStr

from app.schemas.usuario import UsuarioResponse


# Lo que el frontend (Angular) nos enviará
class LoginRequest(BaseModel):
    correo: EmailStr
    password: str


# Lo que el backend responde si el login es exitoso (dentro del envelope)
class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int  # segundos hasta expirar el token
    user: UsuarioResponse
