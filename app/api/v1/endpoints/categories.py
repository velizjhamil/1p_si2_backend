# backend/app/api/v1/endpoints/categories.py
# CU9 — Gestión de Categorías: GET paginado/filtrado, POST, PUT, DELETE.
#
# RESTRICCIÓN DE NEGOCIO (DELETE): si la categoría tiene productos asociados
# en el catálogo (productos.id_categoria), se IMPIDE la eliminación física
# (409) notificando el error y se permite únicamente la desactivación
# (soft delete, activo=False). La tabla productos no existe aún (CU6
# futuro); la verificación es DINÁMICA: cuando el catálogo llegue, la
# restricción se activa sin tocar este router (misma técnica que CU23).
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.modules.inventario.models import Categoria
from app.schemas.categoria import (
    LINEAS_CATEGORIA,
    CategoriaCreate,
    CategoriaRead,
    CategoriaUpdate,
)

# GESTIÓN (POST/PUT/DELETE): solo ASU. CONSULTA (GET): sin cambios (la usan el
# Cliente y otros módulos). Reutiliza deps.require_roles: 401 sin token, 403 otro rol.
requerir_asu = require_roles(
    "ASU",
    detail="Solo el Administrador super usuario (ASU) puede gestionar categorías.",
)

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras de paginación."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _buscar_categoria(db: Session, id_categoria: int) -> Categoria:
    """404 consistente para endpoints con path param."""
    categoria = db.get(Categoria, id_categoria)
    if not categoria:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la categoría con id {id_categoria}.",
        )
    return categoria


def _validar_linea(linea: str | None) -> None:
    """422 si la línea no es Hombre/Mujer/Unisex."""
    if linea is not None and linea not in LINEAS_CATEGORIA:
        raise HTTPException(
            status_code=422,
            detail=f"Línea inválida '{linea}'. Valores permitidos: {', '.join(LINEAS_CATEGORIA)}.",
        )


def _validar_nombre_unico(db: Session, nombre: str, excluir_id: int | None = None) -> None:
    """409 si ya existe una categoría con ese nombre (por línea: una misma
    categoría puede repetir nombre en otra línea — ej: 'Camisas' Hombre y
    'Camisas' Mujer conviven)."""
    query = db.query(Categoria).filter(Categoria.nombre == nombre)
    if excluir_id is not None:
        query = query.filter(Categoria.id_categoria != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una categoría llamada '{nombre}'.",
        )


def _productos_asociados(db: Session, categoria: Categoria) -> int:
    """Cuenta productos del catálogo asociados a esta categoría.

    Dinámico: si la tabla `productos` no existe aún en la DB devuelve 0
    (la restricción de negocio se activa sola cuando llegue el CU6).
    """
    inspector = inspect(db.get_bind())
    if "productos" not in inspector.get_table_names():
        return 0
    resultado = db.execute(
        text("SELECT COUNT(*) FROM productos WHERE id_categoria = :cid"),
        {"cid": categoria.id_categoria},
    ).scalar()
    return int(resultado or 0)


# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_categorias(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre o descripción"),
    linea: str | None = Query(default=None, description="Hombre | Mujer | Unisex"),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU9: Lista paginada con búsqueda y filtro opcional por línea."""
    query = db.query(Categoria)

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Categoria.nombre.ilike(term),
                Categoria.descripcion.ilike(term),
            )
        )
    if linea:
        _validar_linea(linea)
        query = query.filter(Categoria.linea == linea)

    total = query.count()
    items = (
        query.order_by(Categoria.id_categoria)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [CategoriaRead.model_validate(c) for c in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,  # techo de división
    )


@router.post(
    "",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requerir_asu)],
)
@router.post(
    "/",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requerir_asu)],
)
def crear_categoria(categoria_in: CategoriaCreate, db: Session = Depends(get_db)):
    """CU9: Registra una categoría validando nombre y línea obligatorios."""
    _validar_linea(categoria_in.linea)
    _validar_nombre_unico(db, categoria_in.nombre)

    categoria = Categoria(
        nombre=categoria_in.nombre,
        linea=categoria_in.linea,
        descripcion=categoria_in.descripcion,
        activo=True,
    )
    db.add(categoria)
    db.commit()
    db.refresh(categoria)

    return _envelope(CategoriaRead.model_validate(categoria))


@router.put("/{id_categoria}", response_model=None, dependencies=[Depends(requerir_asu)])
def actualizar_categoria(
    id_categoria: int, categoria_in: CategoriaUpdate, db: Session = Depends(get_db)
):
    """CU9: Actualiza nombre, línea, descripción o estado activo."""
    categoria = _buscar_categoria(db, id_categoria)
    _validar_linea(categoria_in.linea)

    if categoria_in.nombre is not None and categoria_in.nombre != categoria.nombre:
        _validar_nombre_unico(db, categoria_in.nombre, excluir_id=categoria.id_categoria)
        categoria.nombre = categoria_in.nombre

    # Campos opcionales: solo se pisan si vienen en el payload
    if categoria_in.linea is not None:
        categoria.linea = categoria_in.linea
    if categoria_in.descripcion is not None:
        categoria.descripcion = categoria_in.descripcion
    if categoria_in.activo is not None:
        categoria.activo = categoria_in.activo

    db.commit()
    db.refresh(categoria)

    return _envelope(CategoriaRead.model_validate(categoria))


@router.delete("/{id_categoria}", response_model=None, dependencies=[Depends(requerir_asu)])
def eliminar_categoria(id_categoria: int, db: Session = Depends(get_db)):
    """CU9: Elimina o desactiva con RESTRICCIÓN DE NEGOCIO.

    Si la categoría tiene productos asociados en el catálogo, se IMPIDE la
    eliminación física (409) notificando el error y permitiendo únicamente
    su desactivación (soft delete). Sin productos asociados, se elimina
    físicamente.
    """
    categoria = _buscar_categoria(db, id_categoria)

    productos = _productos_asociados(db, categoria)
    if productos > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la categoría '{categoria.nombre}' tiene "
                f"{productos} producto(s) asociados en el catálogo. "
                f"Solo puede desactivarla para conservar el historial."
            ),
        )

    nombre = categoria.nombre
    db.delete(categoria)
    db.commit()

    return _envelope(None, message=f"Categoría '{nombre}' eliminada correctamente.")
