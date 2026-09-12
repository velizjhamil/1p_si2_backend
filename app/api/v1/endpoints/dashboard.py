# backend/app/api/v1/endpoints/dashboard.py
# Dashboard de Inicio y Métricas Resumen: GET /api/v1/dashboard/metrics.
# PROTEGIDO POR JWT vía la dependencia compartida get_current_user
# (app/api/deps.py). Roles con acceso: ASU y GS (herramienta administrativa).
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.usuarios.models import Usuario
from app.modules.empresa.models import Empresa, Sucursal
from app.modules.compras.models import Proveedor
from app.schemas.empresa import EmpresaRead

router = APIRouter()

# Roles con acceso al dashboard administrativo (Rol.nombre_rol)
ROLES_DASHBOARD = {"ASU", "GS"}


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


@router.get("/metrics", response_model=None)
def metricas_dashboard(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Métricas resumen del sistema para el Panel de Administración.

    - total_sucursales: sucursales ACTIVAS (is_active=True).
    - total_usuarios: usuarios registrados (todos, activos e inactivos).
    - total_proveedores: proveedores registrados (homologados).
    - total_clientes: usuarios con rol Cliente ('C').
    - resumen_empresa: datos principales de la empresa matriz.
    """
    # Control de acceso por rol (ASU = admin, GS = gerente de sucursal)
    if usuario.rol.nombre_rol not in ROLES_DASHBOARD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su rol no tiene acceso al panel de administración.",
        )

    total_sucursales = (
        db.query(func.count(Sucursal.codigo_sucursal))
        .filter(Sucursal.is_active == True)  # noqa: E712
        .scalar()
    )
    total_usuarios = db.query(func.count(Usuario.id_usuario)).scalar()
    total_proveedores = db.query(func.count(Proveedor.id_proveedor)).scalar()
    total_clientes = (
        db.query(func.count(Usuario.id_usuario))
        .join(Usuario.rol)
        .filter(Usuario.rol.has(nombre_rol="C"))
        .scalar()
    )

    empresa = db.query(Empresa).order_by(Empresa.id).first()
    resumen_empresa = EmpresaRead.model_validate(empresa) if empresa else None

    return _envelope(
        {
            "total_sucursales": total_sucursales,
            "total_usuarios": total_usuarios,
            "total_proveedores": total_proveedores,
            "total_clientes": total_clientes,
            "resumen_empresa": resumen_empresa,
            "generado_en": datetime.now(timezone.utc).isoformat(),
        }
    )
