# backend/app/models/usuario.py
from sqlalchemy import Column, Integer, String, Boolean
from app.models.base import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False) # NUNCA guardamos la contraseña en texto plano
    rol = Column(String, nullable=False)           # Ej: "Administrador Super Usuario"
    is_active = Column(Boolean, default=True)