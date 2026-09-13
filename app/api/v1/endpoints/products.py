# backend/app/api/v1/endpoints/products.py
# CU6 — Gestión de Productos de Ropa: GET paginado/filtrado, POST, PUT, DELETE.
#
# RESTRICCIÓN DE NEGOCIO (DELETE): si el producto tiene movimientos de
# inventario (kardex, CU22 futuro), se IMPIDE la eliminación física (409).
# La verificación es DINÁMICA (misma técnica que CU9/CU23): cuando la tabla
# de movimientos llegue, la restricción se activa sin tocar este router.
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.compras.models import Proveedor
from app.modules.inventario.models import Categoria, Color, Producto, Talla
from app.schemas.producto import (
    ESTADOS_PRODUCTO,
    ProductoCreatePayload,
    ProductoResponse,
    ProductoUpdatePayload,
)

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras de paginación."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _serializar_producto(p: Producto) -> dict:
    """Arma la respuesta con categoría, proveedor, tallas y colores.

    Pydantic no puede resolver `categoria` como objeto embebido directo
    desde el ORM (el campo `nombre_proveedor` es calculado), así que se
    arma el dict a mano con from_attributes por partes.
    """
    return {
        "id_producto": p.id_producto,
        "nombre": p.nombre,
        "id_categoria": p.id_categoria,
        "categoria": {
            "id_categoria": p.categoria.id_categoria,
            "nombre": p.categoria.nombre,
            "linea": p.categoria.linea,
        }
        if p.categoria
        else None,
        "id_proveedor": p.id_proveedor,
        "nombre_proveedor": p.proveedor.nombre if p.proveedor else None,
        "precio_venta": p.precio_venta,
        "stock_total": p.stock_total,
        "imagen_url": p.imagen_url,
        "descripcion": p.descripcion,
        "estado": p.estado,
        "tallas": [
            {"id_talla": t.id_talla, "nombre_talla": t.nombre_talla} for t in p.tallas
        ],
        "colores": [
            {"id_color": c.id_color, "nombre_color": c.nombre_color, "codigo_hex": c.codigo_hex}
            for c in p.colores
        ],
        "fecha_creacion": p.fecha_creacion,
    }


def _buscar_producto(db: Session, id_producto: int) -> Producto:
    """404 consistente para endpoints con path param."""
    producto = db.get(Producto, id_producto)
    if not producto:
        raise HTTPException(
            status_code=404,
            detail=f"No existe el producto con id {id_producto}.",
        )
    return producto


def _validar_estado(valor: str | None) -> None:
    """422 si el estado no es Activo/Inactivo/Agotado."""
    if valor is not None and valor not in ESTADOS_PRODUCTO:
        raise HTTPException(
            status_code=422,
            detail=f"Estado inválido '{valor}'. Valores permitidos: {', '.join(ESTADOS_PRODUCTO)}.",
        )


def _validar_nombre_unico(db: Session, nombre: str, excluir_id: int | None = None) -> None:
    """409 si ya existe un producto con ese nombre (nombre único global)."""
    query = db.query(Producto).filter(Producto.nombre == nombre)
    if excluir_id is not None:
        query = query.filter(Producto.id_producto != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un producto llamado '{nombre}'.",
        )


def _validar_fk(db: Session, id_categoria: int) -> None:
    """422 si la categoría no existe (FK obligatoria del CU9)."""
    if not db.get(Categoria, id_categoria):
        raise HTTPException(
            status_code=422,
            detail=f"No existe la categoría con id {id_categoria}.",
        )


def _validar_proveedor(db: Session, id_proveedor: int | None) -> None:
    """422 si el proveedor no existe (FK opcional del CU23)."""
    if id_proveedor is not None and not db.get(Proveedor, id_proveedor):
        raise HTTPException(
            status_code=422,
            detail=f"No existe el proveedor con id {id_proveedor}.",
        )


def _resolver_catalogo(
    db: Session, modelo, ids: list[int], etiqueta: str
) -> list:
    """Resuelve los IDs contra el catálogo: 422 si alguno no existe."""
    if not ids:
        return []
    pk = "id_talla" if modelo is Talla else "id_color"
    registros = db.query(modelo).filter(getattr(modelo, pk).in_(ids)).all()
    if len(registros) != len(set(ids)):
        encontrados = {getattr(r, pk) for r in registros}
        faltantes = [i for i in set(ids) if i not in encontrados]
        raise HTTPException(
            status_code=422,
            detail=f"No existen {etiqueta} con id(s): {', '.join(map(str, faltantes))}.",
        )
    return registros


def _movimientos_inventario(db: Session, producto: Producto) -> int:
    """Cuenta movimientos de inventario (kardex) asociados al producto.

    Dinámico: si la tabla de movimientos no existe aún (CU22 futuro)
    devuelve 0 — la restricción de negocio se activa sola cuando llegue.
    """
    inspector = inspect(db.get_bind())
    if "movimientos_inventario" not in inspector.get_table_names():
        return 0
    resultado = db.execute(
        text("SELECT COUNT(*) FROM movimientos_inventario WHERE id_producto = :pid"),
        {"pid": producto.id_producto},
    ).scalar()
    return int(resultado or 0)


# ---------------------------------------------------------------------------
# GET — listado paginado con búsqueda, categoría y estado
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_productos(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre o descripción"),
    id_categoria: int | None = Query(default=None, ge=1, description="FK categoría del CU9"),
    estado: str | None = Query(default=None, description="Activo | Inactivo | Agotado"),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU6: Lista paginada con búsqueda, filtro por categoría y estado."""
    _validar_estado(estado)

    query = db.query(Producto)

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Producto.nombre.ilike(term),
                Producto.descripcion.ilike(term),
            )
        )
    if id_categoria:
        _validar_fk(db, id_categoria)
        query = query.filter(Producto.id_categoria == id_categoria)
    if estado:
        query = query.filter(Producto.estado == estado)

    total = query.count()
    items = (
        query.order_by(Producto.id_producto)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_producto(p) for p in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,  # techo de división
    )


# ---------------------------------------------------------------------------
# POST — crear producto con relaciones N:M
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_producto(producto_in: ProductoCreatePayload, db: Session = Depends(get_db)):
    """CU6: Registra un producto y sus tallas/colores en las tablas pivote."""
    _validar_nombre_unico(db, producto_in.nombre)
    _validar_fk(db, producto_in.id_categoria)
    _validar_proveedor(db, producto_in.id_proveedor)
    tallas = _resolver_catalogo(db, Talla, producto_in.tallas, "tallas")
    colores = _resolver_catalogo(db, Color, producto_in.colores, "colores")

    producto = Producto(
        nombre=producto_in.nombre,
        id_categoria=producto_in.id_categoria,
        id_proveedor=producto_in.id_proveedor,
        precio_venta=producto_in.precio_venta,
        stock_total=producto_in.stock_total,
        imagen_url=producto_in.imagen_url,
        descripcion=producto_in.descripcion,
        estado="Activo",
        tallas=tallas,
        colores=colores,
    )
    db.add(producto)
    db.commit()
    db.refresh(producto)

    return _envelope(
        _serializar_producto(producto),
        message="Producto registrado correctamente.",
    )


# ---------------------------------------------------------------------------
# PUT — actualización parcial de datos y relaciones
# ---------------------------------------------------------------------------
@router.put("/{id_producto}", response_model=None)
def actualizar_producto(
    id_producto: int, producto_in: ProductoUpdatePayload, db: Session = Depends(get_db)
):
    """CU6: Actualiza campos del producto y/o reemplaza las relaciones N:M."""
    producto = _buscar_producto(db, id_producto)
    _validar_estado(producto_in.estado)

    if producto_in.nombre is not None and producto_in.nombre != producto.nombre:
        _validar_nombre_unico(db, producto_in.nombre, excluir_id=producto.id_producto)
        producto.nombre = producto_in.nombre
    if producto_in.id_categoria is not None:
        _validar_fk(db, producto_in.id_categoria)
        producto.id_categoria = producto_in.id_categoria
    if producto_in.id_proveedor is not None:
        _validar_proveedor(db, producto_in.id_proveedor)
        producto.id_proveedor = producto_in.id_proveedor
    if producto_in.precio_venta is not None:
        producto.precio_venta = producto_in.precio_venta
    if producto_in.stock_total is not None:
        producto.stock_total = producto_in.stock_total
    if producto_in.imagen_url is not None:
        producto.imagen_url = producto_in.imagen_url
    if producto_in.descripcion is not None:
        producto.descripcion = producto_in.descripcion
    if producto_in.estado is not None:
        producto.estado = producto_in.estado

    # Relaciones N:M: solo se reemplazan si el campo viene explícito
    if producto_in.tallas is not None:
        producto.tallas = _resolver_catalogo(db, Talla, producto_in.tallas, "tallas")
    if producto_in.colores is not None:
        producto.colores = _resolver_catalogo(db, Color, producto_in.colores, "colores")

    db.commit()
    db.refresh(producto)

    return _envelope(
        _serializar_producto(producto),
        message="Producto actualizado correctamente.",
    )


# ---------------------------------------------------------------------------
# DELETE — eliminación física con verificación de movimientos (409)
# ---------------------------------------------------------------------------
@router.delete("/{id_producto}", response_model=None)
def eliminar_producto(id_producto: int, db: Session = Depends(get_db)):
    """CU6: Elimina un producto FÍSICAMENTE si no tiene movimientos.

    RESTRICCIÓN DE NEGOCIO: con movimientos de inventario (kardex CU22)
    la eliminación física está IMPEDIDA (409) — el historial se conserva.
    Los registros de las tablas pivote se borran en cascada (ondelete).
    """
    producto = _buscar_producto(db, id_producto)

    movimientos = _movimientos_inventario(db, producto)
    if movimientos > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: el producto '{producto.nombre}' tiene "
                f"{movimientos} movimiento(s) de inventario registrados. "
                f"Considere cambiar su estado a 'Inactivo' para conservar el historial."
            ),
        )

    nombre = producto.nombre
    db.delete(producto)
    db.commit()

    return _envelope(None, message=f"Producto '{nombre}' eliminado correctamente.")
