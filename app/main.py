# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import (
    auth,
    branches,
    company,
    dashboard,
    roles,
    suppliers,
    users,
)
from app.core.config import get_settings

# Importar TODOS los módulos de modelos para que sus tablas se registren
# en el metadata compartido antes de cualquier operación DDL futura.
import app.modules.usuarios.models  # noqa: F401  (usuarios, roles, permisos)
import app.modules.empresa.models  # noqa: F401  (empresas, ciudades, sucursales)
import app.modules.compras.models  # noqa: F401  (proveedores CU23)

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


# Conectamos los enrutadores (Endpoints) — desacoplados por CU en
# app/api/v1/endpoints/. Los prefijos NO cambian (contratos de API intactos).
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Autenticación"])
app.include_router(users.router, prefix="/api/v1/usuarios", tags=["Usuarios"])
# CU4 + CU5: catálogos de roles y permisos + matriz (montados directo bajo
# /api/v1 porque los consumen varias vistas de Angular)
app.include_router(roles.router, prefix="/api/v1", tags=["Roles y Permisos"])
# CU16: perfil institucional — GET/PUT /api/v1/empresa
app.include_router(company.router, prefix="/api/v1/empresa", tags=["Empresa"])
# CU17: gestión de sucursales + catálogo de ciudades
app.include_router(branches.router, prefix="/api/v1/sucursales", tags=["Sucursales"])
app.include_router(branches.ciudades_router, prefix="/api/v1/ciudades", tags=["Ciudades"])
# CU23: gestión de proveedores — GET/POST/PUT/DELETE /api/v1/proveedores
app.include_router(suppliers.router, prefix="/api/v1/proveedores", tags=["Proveedores"])
# Dashboard: métricas resumen del panel — GET /api/v1/dashboard/metrics (JWT)
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
