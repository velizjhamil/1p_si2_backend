# backend/app/api/v1/endpoints/variantes.py
# CU7 — Gestión de Tallas y Colores: CRUD sobre /api/v1/tallas y
# /api/v1/colores. Tablas creadas en la migración f2a9c8e4d1b3.
#
# Reglas de negocio:
# - Talla: nombre único (409 si se repite).
# - Color: nombre único Y codigo_hex único (409 si se repite alguno);
#   el formato #RRGGBB se valida en el schema (422).
# - DELETE: 409 si la talla/color está asociada a productos/variantes
#   del catálogo (verificación dinámica: la tabla llegará con CU6).
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.inventario.models import Color, Talla
from app.schemas.variante import (
    ColorCreate,
    ColorRead,
    ColorUpdate,
    TallaCreate,
    TallaRead,
    TallaUpdate,
)

# Router de tallas: /api/v1/tallas
router = APIRouter()

# Router de colores: /api/v1/colores
colores_router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras de paginación."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------
def _tabla_existe(db: Session, tabla: str) -> bool:
    """True si la tabla física existe en la DB (para validaciones dinámicas)."""
    inspector = inspect(db.get_bind())
    return tabla in inspector.get_table_names()


def _columna_existe(db: Session, tabla: str, columna: str) -> bool:
    """True si la columna existe en la tabla física (para FKs futuras)."""
    if not _tabla_existe(db, tabla):
        return False
    columnas = {c["name"] for c in inspect(db.get_bind()).get_columns(tabla)}
    return columna in columnas


# ---------------------------------------------------------------------------
# Tallas
# ---------------------------------------------------------------------------
def _buscar_talla(db: Session, id_talla: int) -> Talla:
    """404 consistente para endpoints con path param."""
    talla = db.get(Talla, id_talla)
    if not talla:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la talla con id {id_talla}.",
        )
    return talla


def _validar_talla_unico(
    db: Session, nombre: str, excluir_id: int | None = None
) -> None:
    """409 si ya existe una talla con ese nombre (unique en DB)."""
    query = db.query(Talla).filter(Talla.nombre_talla == nombre)
    if excluir_id is not None:
        query = query.filter(Talla.id_talla != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una talla llamada '{nombre}'.",
        )


def _variantes_asociadas(db: Session, talla: Talla) -> int:
    """Cuenta variantes/productos asociados a esta talla.

    Dinámico: si la tabla de variantes/productos no existe aún (CU6
    futuro) devuelve 0 — la restricción se activará sola cuando llegue.
    """
    # Esquema esperado del CU6: productos.id_talla (variante directa)
    if _columna_existe(db, "productos", "id_talla"):
        resultado = db.execute(
            text("SELECT COUNT(*) FROM productos WHERE id_talla = :tid"),
            {"tid": talla.id_talla},
        ).scalar()
        return int(resultado or 0)
    return 0


# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_tallas(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre de talla"),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU7: Lista paginada de tallas del catálogo."""
    query = db.query(Talla)

    if q:
        query = query.filter(Talla.nombre_talla.ilike(f"%{q}%"))

    total = query.count()
    items = (
        query.order_by(Talla.id_talla)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [TallaRead.model_validate(t) for t in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,  # techo de división
    )


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_talla(talla_in: TallaCreate, db: Session = Depends(get_db)):
    """CU7: Registra una talla (nombre único)."""
    _validar_talla_unico(db, talla_in.nombre_talla)

    talla = Talla(
        nombre_talla=talla_in.nombre_talla,
        descripcion=talla_in.descripcion,
        activo=True,
    )
    db.add(talla)
    db.commit()
    db.refresh(talla)

    return _envelope(TallaRead.model_validate(talla))


@router.put("/{id_talla}", response_model=None)
def actualizar_talla(id_talla: int, talla_in: TallaUpdate, db: Session = Depends(get_db)):
    """CU7: Actualiza nombre, descripción o estado activo de una talla."""
    talla = _buscar_talla(db, id_talla)

    if talla_in.nombre_talla is not None and talla_in.nombre_talla != talla.nombre_talla:
        _validar_talla_unico(db, talla_in.nombre_talla, excluir_id=talla.id_talla)
        talla.nombre_talla = talla_in.nombre_talla

    # Campos opcionales: solo se pisan si vienen en el payload
    if talla_in.descripcion is not None:
        talla.descripcion = talla_in.descripcion
    if talla_in.activo is not None:
        talla.activo = talla_in.activo

    db.commit()
    db.refresh(talla)

    return _envelope(TallaRead.model_validate(talla))


@router.delete("/{id_talla}", response_model=None)
def eliminar_talla(id_talla: int, db: Session = Depends(get_db)):
    """CU7: Elimina una talla con RESTRICCIÓN DE NEGOCIO.

    Si la talla está asociada a productos/variantes del catálogo, se
    IMPIDE la eliminación física (409) para no romper el historial; solo
    puede desactivarse (soft delete, activo=False).
    """
    talla = _buscar_talla(db, id_talla)

    variantes = _variantes_asociadas(db, talla)
    if variantes > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la talla '{talla.nombre_talla}' está "
                f"asociada a {variantes} producto(s)/variante(s) del catálogo. "
                f"Solo puede desactivarla para conservar el historial."
            ),
        )

    nombre = talla.nombre_talla
    db.delete(talla)
    db.commit()

    return _envelope(None, message=f"Talla '{nombre}' eliminada correctamente.")


# ---------------------------------------------------------------------------
# Colores
# ---------------------------------------------------------------------------
def _buscar_color(db: Session, id_color: int) -> Color:
    """404 consistente para endpoints con path param."""
    color = db.get(Color, id_color)
    if not color:
        raise HTTPException(
            status_code=404,
            detail=f"No existe el color con id {id_color}.",
        )
    return color


def _validar_color_unico(
    db: Session, nombre: str, hex_: str | None = None, excluir_id: int | None = None
) -> None:
    """409 si ya existe un color con ese nombre O ese codigo_hex."""
    query = db.query(Color).filter(Color.nombre_color == nombre)
    if excluir_id is not None:
        query = query.filter(Color.id_color != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un color llamado '{nombre}'.",
        )

    # El HEX también es único: dos colores no pueden compartir código
    if hex_ is not None:
        query_hex = db.query(Color).filter(Color.codigo_hex == hex_)
        if excluir_id is not None:
            query_hex = query_hex.filter(Color.id_color != excluir_id)
        existente = query_hex.first()
        if existente:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ya existe el color '{existente.nombre_color}' con el "
                    f"código {hex_}. Los códigos HEX deben ser únicos."
                ),
            )


def _variantes_color_asociadas(db: Session, color: Color) -> int:
    """Cuenta variantes/productos asociados a este color (dinámico, CU6)."""
    if _columna_existe(db, "productos", "id_color"):
        resultado = db.execute(
            text("SELECT COUNT(*) FROM productos WHERE id_color = :cid"),
            {"cid": color.id_color},
        ).scalar()
        return int(resultado or 0)
    return 0


@colores_router.get("", response_model=None)
@colores_router.get("/", response_model=None)
def listar_colores(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre de color"),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU7: Lista paginada de colores del catálogo."""
    query = db.query(Color)

    if q:
        query = query.filter(Color.nombre_color.ilike(f"%{q}%"))

    total = query.count()
    items = (
        query.order_by(Color.id_color)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [ColorRead.model_validate(c) for c in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,  # techo de división
    )


@colores_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@colores_router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_color(color_in: ColorCreate, db: Session = Depends(get_db)):
    """CU7: Registra un color (nombre y HEX únicos; #RRGGBB validado)."""
    # Normaliza el HEX a minúsculas antes de persistir/validar unicidad
    hex_norm = color_in.codigo_hex.lower()
    _validar_color_unico(db, color_in.nombre_color, hex_=hex_norm)

    color = Color(
        nombre_color=color_in.nombre_color,
        codigo_hex=hex_norm,
        descripcion=color_in.descripcion,
        activo=True,
    )
    db.add(color)
    db.commit()
    db.refresh(color)

    return _envelope(ColorRead.model_validate(color))


@colores_router.put("/{id_color}", response_model=None)
def actualizar_color(id_color: int, color_in: ColorUpdate, db: Session = Depends(get_db)):
    """CU7: Actualiza nombre, HEX, descripción o estado de un color."""
    color = _buscar_color(db, id_color)

    nuevo_nombre = (
        color_in.nombre_color if color_in.nombre_color is not None else color.nombre_color
    )
    nuevo_hex = (
        color_in.codigo_hex.lower()
        if color_in.codigo_hex is not None
        else color.codigo_hex
    )

    # Valida unicidad contra OTROS colores solo si algo cambió
    if nuevo_nombre != color.nombre_color or nuevo_hex != color.codigo_hex:
        _validar_color_unico(
            db, nuevo_nombre, hex_=nuevo_hex, excluir_id=color.id_color
        )

    color.nombre_color = nuevo_nombre
    color.codigo_hex = nuevo_hex

    if color_in.descripcion is not None:
        color.descripcion = color_in.descripcion
    if color_in.activo is not None:
        color.activo = color_in.activo

    db.commit()
    db.refresh(color)

    return _envelope(ColorRead.model_validate(color))


@colores_router.delete("/{id_color}", response_model=None)
def eliminar_color(id_color: int, db: Session = Depends(get_db)):
    """CU7: Elimina un color con RESTRICCIÓN DE NEGOCIO.

    Si el color está asociado a productos/variantes del catálogo, se
    IMPIDE la eliminación física (409); solo puede desactivarse.
    """
    color = _buscar_color(db, id_color)

    variantes = _variantes_color_asociadas(db, color)
    if variantes > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: el color '{color.nombre_color}' está "
                f"asociado a {variantes} producto(s)/variante(s) del catálogo. "
                f"Solo puede desactivarlo para conservar el historial."
            ),
        )

    nombre = color.nombre_color
    db.delete(color)
    db.commit()

    return _envelope(None, message=f"Color '{nombre}' eliminado correctamente.")
