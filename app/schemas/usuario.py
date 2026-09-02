# backend/app/schemas/usuario.py
from pydantic import BaseModel, EmailStr

# Datos requeridos para crear un usuario
class UsuarioCreate(BaseModel):
    email: EmailStr
    password: str
    rol: str

# Datos que devolveremos como respuesta (Ocultamos el password)
class UsuarioResponse(BaseModel):
    id: int
    email: EmailStr
    rol: str
    is_active: bool

    class Config:
        from_attributes = True # Permite a Pydantic leer los objetos de SQLAlchemy