# backend/app/schemas/auth.py
from pydantic import BaseModel, EmailStr

# Lo que el frontend (Angular/Flutter) nos enviará
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# Lo que el backend responderá si el login es exitoso
class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    rol: str