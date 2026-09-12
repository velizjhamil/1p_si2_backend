# backend/app/api/v1/endpoints/roles.py
# CU4 + CU5 — Roles y Permisos: catálogos + matriz de permisos por rol.
# Se monta bajo /api/v1 directamente (GET/POST /roles, GET /permisos,
# PUT /roles/{id}/permisos) porque los consumen varias vistas de Angular.
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.usuarios.models import Permiso, Rol
from app.schemas.permiso import PermisoRead
from app.schemas.rol import RolCreate, RolPermisosUpdate, RolRead

router = APIRouter()


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


def _obtener_permisos_validados(db: Session, permiso_ids: list[int]) -> list[Permiso]:
    """Resuelve y valida la lista de permisos; 400 si alguno no existe."""
    if not permiso_ids:
        return []
    permisos = db.query(Permiso).filter(Permiso.id.in_(permiso_ids)).all()
    if len(permisos) != len(set(permiso_ids)):
        encontrados = {p.id for p in permisos}
        faltantes = sorted(set(permiso_ids) - encontrados)
        raise HTTPException(
            status_code=400,
            detail=f"Permisos inexistentes: {faltantes}",
        )
    return permisos


@router.get("/roles", response_model=None)
def listar_roles(db: Session = Depends(get_db)):
    """CU4 (lectura): Lista todos los roles con sus permisos heredados."""
    roles = db.query(Rol).order_by(Rol.nombre_rol).all()
    return _envelope([RolRead.model_validate(r) for r in roles])


@router.post("/roles", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_rol(rol_in: RolCreate, db: Session = Depends(get_db)):
    """CU4: Crea un rol con sus permisos (matriz de la vista unificada)."""
    # 1. Nombre de rol único
    if db.query(Rol).filter(Rol.nombre_rol == rol_in.nombre_rol).first():
        raise HTTPException(
            status_code=400,
            detail=f"El rol '{rol_in.nombre_rol}' ya existe.",
        )

    # 2. Validar que todos los permisos existan antes de tocar la DB
    permisos = _obtener_permisos_validados(db, rol_in.permiso_ids)

    rol = Rol(nombre_rol=rol_in.nombre_rol, descripcion=rol_in.descripcion)
    rol.permisos = permisos
    db.add(rol)
    db.commit()
    db.refresh(rol)

    return _envelope(RolRead.model_validate(rol))


@router.put("/roles/{id_rol}/permisos", response_model=None)
def actualizar_permisos_rol(
    id_rol: UUID,
    payload: RolPermisosUpdate,
    db: Session = Depends(get_db),
):
    """CU4+CU5: Reemplaza los permisos de un rol (matriz de checkboxes).

    Estrategia replace: la lista enviada ES el estado final del rol.
    """
    rol = db.get(Rol, id_rol)
    if not rol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol no encontrado",
        )

    permisos = _obtener_permisos_validados(db, payload.permiso_ids)
    rol.permisos = permisos
    db.commit()
    db.refresh(rol)

    return _envelope(RolRead.model_validate(rol))


@router.get("/permisos", response_model=None)
def listar_permisos(db: Session = Depends(get_db)):
    """CU5 (lectura): Lista los permisos agrupados por módulo.

    El frontend renderiza un bloque por módulo (Usuarios, Ventas, ...), así
    que agrupamos aquí y evitamos lógica de agrupamiento en el cliente.
    """
    permisos = db.query(Permiso).order_by(Permiso.modulo, Permiso.id).all()
    agrupados: dict[str, list[PermisoRead]] = {}
    for permiso in permisos:
        agrupados.setdefault(permiso.modulo, []).append(
            PermisoRead.model_validate(permiso)
        )

    return _envelope(agrupados)
