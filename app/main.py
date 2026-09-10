# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, empresa, usuarios
from app.core.config import get_settings
from app.core.database import engine
from app.models.base import Base

# Importar TODOS los módulos de modelos para que sus tablas se registren
# en el metadata compartido antes de cualquier operación DDL futura.
import app.modules.usuarios.models  # noqa: F401  (usuarios, roles, permisos)
import app.modules.empresa.models  # noqa: F401  (empresas, sucursales)
import app.modules.compras.models  # noqa: F401  (proveedores)

app = FastAPI(
    title="Attention E-Commerce API",
    description="API REST para plataforma de ropa con probador virtual",
    version="1.0.0"
)

# CORS: orígenes configurables desde .env (Settings); por defecto el
# frontend Angular en localhost:4200 necesita permiso explícito.
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ruta raíz para evitar el 404 en http://127.0.0.1:8000/
@app.get("/")
def home():
    return {
        "message": "Bienvenido a la API de Attention E-Commerce",
        "documentacion": "/docs",
    }


# Conectamos los enrutadores (Endpoints)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Autenticación"])
app.include_router(usuarios.router, prefix="/api/v1/usuarios", tags=["Usuarios"])
# Catálogos de solo lectura + matriz: GET/POST /api/v1/roles, GET /api/v1/permisos,
# PUT /api/v1/roles/{id}/permisos
app.include_router(usuarios.catalogos_router, prefix="/api/v1", tags=["Roles y Permisos"])
# CU16: perfil institucional — GET/PUT /api/v1/empresa
app.include_router(empresa.router, prefix="/api/v1/empresa", tags=["Empresa"])
