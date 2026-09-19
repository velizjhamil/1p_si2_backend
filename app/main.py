# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import (
    auth,
    branches,
    catalogo,
    categories,
    company,
    dashboard,
    descuentos,
    devoluciones,
    inventario,
    notificaciones,
    probador,
    products,
    reportes,
    reservas,
    roles,
    suppliers,
    users,
    variantes,
    ventas,
)
from app.core.config import get_settings

# Importar TODOS los módulos de modelos para que sus tablas se registren
# en el metadata compartido antes de cualquier operación DDL futura.
import app.modules.usuarios.models  # noqa: F401  (usuarios, roles, permisos)
import app.modules.empresa.models  # noqa: F401  (empresas, ciudades, sucursales)
import app.modules.compras.models  # noqa: F401  (proveedores CU23)
import app.modules.inventario.models  # noqa: F401  (productos CU6, tallas/colores CU7, categorias CU9, colecciones/temporadas CU24)
import app.modules.ventas.models  # noqa: F401  (reservas CU14, ventas/detalle CU15+CU21)
import app.modules.probador.models  # noqa: F401  (fotos_usuario y simulaciones CU8)
import app.modules.descuentos.models  # noqa: F401  (descuentos/cupones CU12)
import app.modules.devoluciones.models  # noqa: F401  (devoluciones y detalle_devoluciones CU13)
import app.modules.notificaciones.models  # noqa: F401  (notificaciones CU10)

app = FastAPI(
    title="Attention E-Commerce API",
    description="API REST para plataforma de ropa con probador virtual",
    version="1.0.0"
)

# CORS: orígenes configurables desde .env (Settings); por defecto el
# frontend Angular en localhost:4200 necesita permiso explícito.
settings = get_settings()

# Convertir CORS_ORIGINS a lista si viene como string
cors_origins = settings.CORS_ORIGINS
if isinstance(cors_origins, str):
    cors_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]

print(f"[CORS] Configured with origins: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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
# CU9: gestión de categorías — GET/POST/PUT/DELETE /api/v1/categorias
app.include_router(categories.router, prefix="/api/v1/categorias", tags=["Categorías"])
# CU24: temporadas y colecciones — CRUD /api/v1/temporadas y /api/v1/colecciones
app.include_router(catalogo.router, prefix="/api/v1/colecciones", tags=["Colecciones"])
app.include_router(
    catalogo.temporadas_router, prefix="/api/v1/temporadas", tags=["Temporadas"]
)
# CU7: tallas y colores — CRUD /api/v1/tallas y /api/v1/colores
app.include_router(variantes.router, prefix="/api/v1/tallas", tags=["Tallas"])
app.include_router(
    variantes.colores_router, prefix="/api/v1/colores", tags=["Colores"]
)
# CU6: gestión de productos — GET/POST/PUT/DELETE /api/v1/productos
app.include_router(products.router, prefix="/api/v1/productos", tags=["Productos"])
# CU14: reservas de prendas — GET/POST /api/v1/reservas, PATCH/DELETE por id
app.include_router(reservas.router, prefix="/api/v1/reservas", tags=["Reservas"])
# CU22: inventario/kardex — GET /api/v1/inventario/stock|movimientos, POST movimientos
app.include_router(inventario.router, prefix="/api/v1/inventario", tags=["Inventario"])
# CU15+CU21: carrito/checkout — POST /api/v1/ventas/checkout, GET /api/v1/ventas
app.include_router(ventas.router, prefix="/api/v1/ventas", tags=["Ventas"])
# CU12: gestion de descuentos/cupones — CRUD /api/v1/descuentos (GS/ASU)
app.include_router(descuentos.router, prefix="/api/v1/descuentos", tags=["Descuentos"])
# CU8: probador virtual AR — subir-foto, probar, lookbook, historial
app.include_router(
    probador.router, prefix="/api/v1/probador-virtual", tags=["Probador Virtual"]
)
# CU13: gestion de devoluciones — CRUD /api/v1/devoluciones (cliente solicita, V/GS/ASU procesan)
app.include_router(devoluciones.router, prefix="/api/v1/devoluciones", tags=["Devoluciones"])
# CU10: gestion de notificaciones — CRUD /api/v1/notificaciones (bandeja in-app por usuario)
app.include_router(notificaciones.router, prefix="/api/v1/notificaciones", tags=["Notificaciones"])
# CU20: gestion de reportes — panel ejecutivo (ASU/GS). READ-ONLY sobre
# ventas / inventario / rendimiento de vendedores. Sin prefijo extra:
# los sub-paths viven en el router (/ventas, /inventario, /rendimiento-vendedores).
app.include_router(reportes.router, prefix="/api/v1/reportes", tags=["Reportes"])
# Dashboard: métricas resumen del panel — GET /api/v1/dashboard/metrics (JWT)
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
