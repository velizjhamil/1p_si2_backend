# backend/app/main.py
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import JSONResponse

from app.core.database import engine
from app.models.base import Base
from app.models.rol import Rol  # noqa: F401 — registra Rol en el metadata
from app.models.usuario import Usuario  # noqa: F401 — registra Usuario en el metadata
from app.routers.auth_router import router as auth_router
from fastapi.middleware.cors import CORSMiddleware


# Crea las tablas si no existen (no borra datos).
# NOTA: el bootstrap real (drop de la tabla legacy + seed de roles/admin)
# lo maneja `python -m app.core.seed` — ejecutarlo una vez antes del primer arranque.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Attention E-Commerce API",
    description="API REST para plataforma de ropa con probador virtual",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    """Formatea los errores HTTP al estándar del proyecto (backend.md).

    {"status": "error", "detail": "...", "code": 400}
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "detail": exc.detail,
            "code": exc.status_code,
        },
        headers=exc.headers,
    )


# Ruta raíz para evitar el 404 en http://127.0.0.1:8000/
@app.get("/")
def home():
    return {
        "message": "Bienvenido a la API de Attention E-Commerce",
        "documentacion": "/docs",
    }


# CU1 + CU2 — Iniciar/Cerrar sesión
app.include_router(
    auth_router, prefix="/api/v1/auth", tags=["Autenticación"]
)
