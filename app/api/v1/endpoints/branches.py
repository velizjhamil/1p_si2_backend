# backend/app/api/v1/endpoints/branches.py
# CU17 — Gestión de Sucursales: GET/POST/PUT/DELETE sobre /api/v1/sucursales
# + catálogo de ciudades (GET /api/v1/ciudades) en el mismo módulo.
# DELETE es soft delete (is_active=False). La validación de "reservas o
# inventario activo" queda como gancho documentado: esas tablas no existen
# aún (CU de inventario/ventas futuros); cuando existan, DELETE debe
# consultarlas antes de desactivar.
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.empresa.models import Ciudad, Empresa, Sucursal
from app.schemas.sucursal import (
    CiudadRead,
    SucursalCreate,
    SucursalRead,
    SucursalUpdate,
)

# Router principal: /api/v1/sucursales
router = APIRouter()

# Router del catálogo de ciudades: /api/v1/ciudades (dropdown del modal)
ciudades_router = APIRouter()


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


def _obtener_empresa(db: Session) -> Empresa:
    """Primera empresa (singleton CU16); crea placeholder si no existe.

    Las sucursales cuelgan de la empresa matriz, así que POST necesita una
    fila válida en empresas aunque el perfil CU16 no se haya completado.
    """
    empresa = db.query(Empresa).order_by(Empresa.id).first()
    if empresa:
        return empresa
    empresa = Empresa(razon_social="Empresa sin configurar", nit="SIN-NIT-000", is_active=True)
    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa


def _buscar_sucursal(db: Session, codigo_sucursal: int) -> Sucursal:
    """404 consistente para todos los endpoints con path param."""
    sucursal = db.get(Sucursal, codigo_sucursal)
    if not sucursal:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la sucursal con código {codigo_sucursal}.",
        )
    return sucursal


def _validar_ciudad(db: Session, id_ciudad: int) -> Ciudad:
    """404 si la ciudad del payload no existe en el catálogo."""
    ciudad = db.get(Ciudad, id_ciudad)
    if not ciudad:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la ciudad con id {id_ciudad} en el catálogo.",
        )
    return ciudad


def _validar_nombre_unico(db: Session, empresa_id: int, nombre: str, excluir_id: int | None = None) -> None:
    """409 si otra sucursal de la misma empresa ya usa ese nombre."""
    query = db.query(Sucursal).filter(
        Sucursal.empresa_id == empresa_id, Sucursal.nombre == nombre
    )
    if excluir_id is not None:
        query = query.filter(Sucursal.codigo_sucursal != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una sucursal llamada '{nombre}' en esta empresa.",
        )


# Rutas duales ("") y ("/"): el frontend llama /api/v1/sucursales SIN barra
# final; sin la ruta extra Starlette responde 307 redirect que, con CORS +
# Authorization, degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_sucursales(db: Session = Depends(get_db)):
    """CU17: Lista de sucursales con la ciudad asociada (ordenada por ciudad)."""
    sucursales = (
        db.query(Sucursal)
        .order_by(Sucursal.id_ciudad, Sucursal.codigo_sucursal)
        .all()
    )
    return _envelope([SucursalRead.model_validate(s) for s in sucursales])


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_sucursal(sucursal_in: SucursalCreate, db: Session = Depends(get_db)):
    """CU17: Registra una nueva sucursal asignándole una ciudad existente."""
    _validar_ciudad(db, sucursal_in.id_ciudad)
    empresa = _obtener_empresa(db)
    _validar_nombre_unico(db, empresa.id, sucursal_in.nombre)

    sucursal = Sucursal(
        empresa_id=empresa.id,
        id_ciudad=sucursal_in.id_ciudad,
        nombre=sucursal_in.nombre,
        direccion=sucursal_in.direccion,
        telefono=sucursal_in.telefono,
        horario_atencion=sucursal_in.horario_atencion,
        is_active=True,
    )
    db.add(sucursal)
    db.commit()
    db.refresh(sucursal)

    return _envelope(SucursalRead.model_validate(sucursal))


@router.put("/{codigo_sucursal}", response_model=None)
def actualizar_sucursal(
    codigo_sucursal: int, sucursal_in: SucursalUpdate, db: Session = Depends(get_db)
):
    """CU17: Edita nombre, dirección, ciudad u horario (campos parciales)."""
    sucursal = _buscar_sucursal(db, codigo_sucursal)

    if sucursal_in.id_ciudad is not None:
        _validar_ciudad(db, sucursal_in.id_ciudad)
        sucursal.id_ciudad = sucursal_in.id_ciudad
    if sucursal_in.nombre is not None and sucursal_in.nombre != sucursal.nombre:
        _validar_nombre_unico(
            db, sucursal.empresa_id, sucursal_in.nombre, excluir_id=sucursal.codigo_sucursal
        )
        sucursal.nombre = sucursal_in.nombre
    if sucursal_in.direccion is not None:
        sucursal.direccion = sucursal_in.direccion
    if sucursal_in.telefono is not None:
        sucursal.telefono = sucursal_in.telefono
    if sucursal_in.horario_atencion is not None:
        sucursal.horario_atencion = sucursal_in.horario_atencion

    db.commit()
    db.refresh(sucursal)

    return _envelope(SucursalRead.model_validate(sucursal))


@router.delete("/{codigo_sucursal}", response_model=None)
def desactivar_sucursal(codigo_sucursal: int, db: Session = Depends(get_db)):
    """CU17: Desactiva la sucursal (soft delete, is_active=False).

    Validación de dependencias: cuando existan las tablas de reservas e
    inventario, este endpoint debe rechazar (409) la desactivación si la
    sucursal tiene registros activos. Hoy no existen, así que se documenta
    el gancho y se permite desactivar.
    """
    sucursal = _buscar_sucursal(db, codigo_sucursal)

    if not sucursal.is_active:
        raise HTTPException(
            status_code=409,
            detail=f"La sucursal '{sucursal.nombre}' ya está desactivada.",
        )

    # TODO(CU inventario/ventas): validar reservas/inventario activo antes
    # de desactivar; si tiene, responder 409 con detalle explicativo.

    sucursal.is_active = False
    db.commit()
    db.refresh(sucursal)

    return _envelope(SucursalRead.model_validate(sucursal))


# ---------------------------------------------------------------------------
# Catálogo de ciudades (mismo módulo de sucursales: dropdown del modal)
# ---------------------------------------------------------------------------

@ciudades_router.get("", response_model=None)
@ciudades_router.get("/", response_model=None)
def listar_ciudades(db: Session = Depends(get_db)):
    """CU17: Catálogo de ciudades ordenadas por departamento."""
    ciudades = (
        db.query(Ciudad).order_by(Ciudad.departamento, Ciudad.nombre).all()
    )
    return _envelope([CiudadRead.model_validate(c) for c in ciudades])
