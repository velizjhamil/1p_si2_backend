# backend/app/api/v1/endpoints/suppliers.py
# CU23 — Gestión de Proveedores: GET paginado/filtrado, POST, PUT, DELETE.
#
# RESTRICCIÓN DE NEGOCIO (DELETE): si el proveedor tiene productos asociados
# en el catálogo (productos.id_proveedor), se IMPIDE la eliminación física
# (409) y se sugiere cambiar su estado a 'Inactivo'. La tabla productos no
# existe aún (CU de inventario futuro); la verificación es DINÁMICA: cuando
# el catálogo llegue, la restricción se activa sin tocar este router.
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.compras.models import Proveedor
from app.modules.empresa.models import Ciudad, Sucursal
from app.modules.usuarios.models import Usuario
from app.schemas.proveedor import (
    ESTADOS_PROVEEDOR,
    ProveedorCreate,
    ProveedorRead,
    ProveedorUpdate,
)

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras de paginación."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _buscar_proveedor(db: Session, id_proveedor: int) -> Proveedor:
    """404 consistente para endpoints con path param."""
    proveedor = db.get(Proveedor, id_proveedor)
    if not proveedor:
        raise HTTPException(
            status_code=404,
            detail=f"No existe el proveedor con id {id_proveedor}.",
        )
    return proveedor


def _validar_estado(estado: str | None) -> None:
    """422 si el estado no es uno de los 3 válidos del CU23."""
    if estado is not None and estado not in ESTADOS_PROVEEDOR:
        raise HTTPException(
            status_code=422,
            detail=f"Estado inválido '{estado}'. Valores permitidos: {', '.join(ESTADOS_PROVEEDOR)}.",
        )


def _validar_nit_unico(db: Session, nit_rut: str, excluir_id: int | None = None) -> None:
    """409 si otro proveedor ya usa ese NIT/RUT."""
    query = db.query(Proveedor).filter(Proveedor.nit_rut == nit_rut)
    if excluir_id is not None:
        query = query.filter(Proveedor.id_proveedor != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"El NIT/RUT '{nit_rut}' ya está registrado para otro proveedor.",
        )


def _validar_sucursal(db: Session, sucursal_id: int | None) -> None:
    """404 si la sucursal de gestión no existe (cuando se envía)."""
    if sucursal_id is not None and not db.get(Sucursal, sucursal_id):
        raise HTTPException(
            status_code=404,
            detail=f"No existe la sucursal con código {sucursal_id}.",
        )


def _productos_asociados(db: Session, proveedor: Proveedor) -> int:
    """Cuenta productos del catálogo asociados a este proveedor."""
    inspector = inspect(db.get_bind())
    if "productos" not in inspector.get_table_names():
        return 0
    resultado = db.execute(
        text("SELECT COUNT(*) FROM productos WHERE id_proveedor = :pid"),
        {"pid": proveedor.id_proveedor},
    ).scalar()
    return int(resultado or 0)


@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_proveedores(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
    sucursal_id: int | None = Query(default=None, description="Filtrar por sucursal"),
    q: str | None = Query(default=None, description="Busca por razón social, NIT/RUT o contacto"),
    categoria: str | None = Query(default=None, description="Filtra por línea/categoría"),
    estado: str | None = Query(default=None, description="Activo | Verificado | Inactivo"),
    ciudad: str | None = Query(default=None, description="Filtra por ciudad de la sucursal"),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU23: Lista paginada con filtros y soporte de aislamiento por sucursal."""
    query = db.query(Proveedor)

    rol_nombre = current_user.rol.nombre_rol.upper() if current_user.rol and current_user.rol.nombre_rol else ""
    if rol_nombre == "GS":
        if current_user.id_sucursal is not None:
            query = query.filter(
                or_(
                    Proveedor.sucursal_id == current_user.id_sucursal,
                    Proveedor.sucursal_id.is_(None),
                )
            )
    elif sucursal_id is not None:
        query = query.filter(Proveedor.sucursal_id == sucursal_id)

    # Filtro de búsqueda: razón social, NIT/RUT o contacto operativo
    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Proveedor.nombre.ilike(term),
                Proveedor.nit_rut.ilike(term),
                Proveedor.contacto_operativo.ilike(term),
            )
        )
    # Filtros exactos
    if categoria:
        query = query.filter(Proveedor.categoria == categoria)
    if estado:
        _validar_estado(estado)
        query = query.filter(Proveedor.estado == estado)
    # Filtro por ciudad: a través de la sucursal de gestión
    if ciudad:
        query = (
            query.join(Proveedor.sucursal)
            .join(Sucursal.ciudad)
            .filter(Ciudad.nombre == ciudad)
        )

    total = query.count()
    items = (
        query.order_by(Proveedor.id_proveedor)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [ProveedorRead.model_validate(p) for p in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_proveedor(
    proveedor_in: ProveedorCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU23: Registra un proveedor validando campos obligatorios."""
    _validar_estado(proveedor_in.estado)
    _validar_nit_unico(db, proveedor_in.nit_rut)

    rol_nombre = current_user.rol.nombre_rol.upper() if current_user.rol and current_user.rol.nombre_rol else ""
    sucursal_id_asignar = proveedor_in.sucursal_id
    if rol_nombre == "GS" and current_user.id_sucursal is not None:
        sucursal_id_asignar = current_user.id_sucursal

    _validar_sucursal(db, sucursal_id_asignar)

    proveedor = Proveedor(
        nombre=proveedor_in.nombre,
        nit_rut=proveedor_in.nit_rut,
        contacto_operativo=proveedor_in.contacto_operativo,
        telefono=proveedor_in.telefono,
        correo=proveedor_in.correo,
        categoria=proveedor_in.categoria,
        estado=proveedor_in.estado,
        direccion=proveedor_in.direccion,
        sucursal_id=sucursal_id_asignar,
    )
    db.add(proveedor)
    db.commit()
    db.refresh(proveedor)

    return _envelope(ProveedorRead.model_validate(proveedor))


@router.put("/{id_proveedor}", response_model=None)
def actualizar_proveedor(
    id_proveedor: int,
    proveedor_in: ProveedorUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU23: Actualiza datos de contacto, razón social o estado."""
    proveedor = _buscar_proveedor(db, id_proveedor)
    rol_nombre = current_user.rol.nombre_rol.upper() if current_user.rol and current_user.rol.nombre_rol else ""
    if rol_nombre == "GS" and current_user.id_sucursal is not None:
        if proveedor.sucursal_id is not None and proveedor.sucursal_id != current_user.id_sucursal:
            raise HTTPException(
                status_code=403,
                detail="No tiene permiso para editar proveedores de otra sucursal.",
            )

    _validar_estado(proveedor_in.estado)

    if proveedor_in.nit_rut is not None and proveedor_in.nit_rut != proveedor.nit_rut:
        _validar_nit_unico(db, proveedor_in.nit_rut, excluir_id=proveedor.id_proveedor)
        proveedor.nit_rut = proveedor_in.nit_rut
    if proveedor_in.sucursal_id is not None and proveedor_in.sucursal_id != proveedor.sucursal_id:
        _validar_sucursal(db, proveedor_in.sucursal_id)
        proveedor.sucursal_id = proveedor_in.sucursal_id

    # Campos opcionales: solo se pisan si vienen en el payload
    for campo in ("nombre", "contacto_operativo", "telefono", "correo", "categoria", "estado", "direccion"):
        valor = getattr(proveedor_in, campo)
        if valor is not None:
            setattr(proveedor, campo, valor)

    db.commit()
    db.refresh(proveedor)

    return _envelope(ProveedorRead.model_validate(proveedor))


@router.delete("/{id_proveedor}", response_model=None)
def eliminar_proveedor(
    id_proveedor: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU23: Elimina o desactiva con RESTRICCIÓN DE NEGOCIO."""
    proveedor = _buscar_proveedor(db, id_proveedor)
    rol_nombre = current_user.rol.nombre_rol.upper() if current_user.rol and current_user.rol.nombre_rol else ""
    if rol_nombre == "GS" and current_user.id_sucursal is not None:
        if proveedor.sucursal_id is not None and proveedor.sucursal_id != current_user.id_sucursal:
            raise HTTPException(
                status_code=403,
                detail="No tiene permiso para eliminar proveedores de otra sucursal.",
            )

    productos = _productos_asociados(db, proveedor)
    if productos > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: el proveedor '{proveedor.nombre}' tiene "
                f"{productos} producto(s) asociados en el catálogo. "
                f"Sugerencia: cambie su estado a 'Inactivo' para conservar el historial."
            ),
        )

    nombre = proveedor.nombre
    db.delete(proveedor)
    db.commit()

    return _envelope(None, message=f"Proveedor '{nombre}' eliminado correctamente.")
