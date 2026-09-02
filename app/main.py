# backend/app/main.py
from fastapi import FastAPI
from app.api.v1 import auth, usuarios
from app.core.database import engine
from app.models.base import Base

# Esta línea instruye a SQLAlchemy a crear todas las tablas en PostgreSQL
# (Solo las crea si no existen; no borra tus datos actuales)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Attention E-Commerce API",
    description="API REST para plataforma de ropa con probador virtual",
    version="1.0.0"
)

# Ruta raíz para evitar el 404 en http://127.0.0.1:8000/
@app.get("/")
def home():
    return {
        "message": "Bienvenido a la API de Attention E-Commerce",
        "documentacion": "/docs"
    }

# Conectamos los enrutadores (Endpoints)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Autenticación"])
app.include_router(usuarios.router, prefix="/api/v1/usuarios", tags=["Usuarios"])