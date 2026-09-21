# backend/app/api/v1/endpoints/inventario.py
# CU22 — Gestión de Inventario: stock actual con alertas, kardex de
# movimientos y registro ENTRADA/SALIDA/AJUSTE.
#
# ACTUALIZACIÓN ATÓMICA DE STOCK: el POST bloquea la fila del producto
# con SELECT ... FOR UPDATE OF productos (el modelo Producto tiene
# relaciones lazy="joined" que generan outer joins — PostgreSQL rechaza
# FOR UPDATE sin `of=`, lección del CU14), calcula el stock nuevo,
# verifica que nunca sea negativo (409), escribe el kardex y actualiza
# productos.stock_total en la MISMA transacción.
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import InventarioSucursal, MovimientoInventario, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.usuarios.models import Usuario
from app.schemas.inventario import (
    MovimientoCreatePayload,
    MovimientoResponse,
    StockAlertResponse,
    StockProductoResponse,
    TIPOS_MOVIMIENTO,
)

router = APIRouter()

# Umbral de stock crítico/bajo del CU22 (badges del frontend: rojo < 5,
# ámbar < 15, verde >= 15). Un único criterio en backend, replicado en
# el modelo del frontend (STOCK_CRITICO / STOCK_MEDIO).
STOCK_CRITICO = 5
STOCK_MEDIO = 15


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _nivel_stock(stock: int) -> str:
    """Nivel de stock según umbrales: CRITICO (<5), BAJO (<15), OK."""
    if stock < STOCK_CRITICO:
        return "CRITICO"
    if stock < STOCK_MEDIO:
        return "BAJO"
    return "OK"


def _serializar_stock(
    p: Producto,
    umbral: int = STOCK_CRITICO,
    sucursal_id: int | None = None,
    sucursal_nombre: str | None = None,
) -> dict:
    """Fila del GET /stock: producto + stock + nivel de alerta."""
    return {
        "id_producto": p.id_producto,
        "nombre": p.nombre,
        "categoria": p.categoria.nombre if p.categoria else None,
        "estado": p.estado,
        "stock_total": p.stock_total,
        "umbral_minimo": umbral,
        "nivel": _nivel_stock(p.stock_total),
        "id_sucursal": sucursal_id,
        "sucursal_nombre": sucursal_nombre,
    }


def _serializar_stock_sucursal(
    inv: InventarioSucursal,
    umbral: int = STOCK_CRITICO,
) -> dict:
    """Fila del GET /stock para una sucursal específica (InventarioSucursal)."""
    p = inv.producto
    return {
        "id_producto": p.id_producto,
        "nombre": p.nombre,
        "categoria": p.categoria.nombre if p.categoria else None,
        "estado": p.estado,
        "stock_total": inv.stock,
        "umbral_minimo": inv.stock_minimo if inv.stock_minimo else umbral,
        "nivel": _nivel_stock(inv.stock),
        "id_sucursal": inv.id_sucursal,
        "sucursal_nombre": inv.sucursal.nombre if inv.sucursal else None,
    }


def _serializar_movimiento(m: MovimientoInventario) -> dict:
    """Movimiento con producto, usuario y sucursal embebidos (respuesta detallada)."""
    return {
        "id_movimiento": m.id_movimiento,
        "tipo": m.tipo,
        "cantidad": m.cantidad,
        "stock_anterior": m.stock_anterior,
        "stock_nuevo": m.stock_nuevo,
        "motivo": m.motivo,
        "fecha_movimiento": m.fecha_movimiento,
        "id_sucursal": m.id_sucursal,
        "sucursal_nombre": m.sucursal.nombre if m.sucursal else None,
        "producto": {
            "id_producto": m.producto.id_producto,
            "nombre": m.producto.nombre,
            "categoria": (
                m.producto.categoria.nombre if m.producto.categoria else None
            ),
            "estado": m.producto.estado,
            "stock_total": m.producto.stock_total,
        },
        "usuario": {
            "id_usuario": str(m.usuario.id_usuario),
            "nombre": m.usuario.nombre,
            "apellido": m.usuario.apellido,
            "correo": m.usuario.correo,
        },
    }


def _validar_tipo_filtro(valor: str | None) -> None:
    """422 si el filtro de tipo no es un tipo válido."""
    if isinstance(valor, str) and valor not in TIPOS_MOVIMIENTO:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo inválido '{valor}'. Valores permitidos: {', '.join(TIPOS_MOVIMIENTO)}.",
        )


# ---------------------------------------------------------------------------
# GET /stock — stock actual por producto con alertas y filtros
# ---------------------------------------------------------------------------
@router.get("/stock", response_model=None)
def listar_stock(
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
    sucursal_id: int | None = Query(default=None, description="Filtrar por ID de sucursal"),
    q: str | None = Query(default=None, description="Busca por nombre del producto"),
    solo_alertas: bool = Query(
        default=False,
        description="true => solo productos con stock < umbral_minimo",
    ),
    umbral_minimo: int = Query(
        default=STOCK_CRITICO, ge=0, description="Umbral de stock bajo"
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    if not isinstance(q, str):
        q = None
    if not isinstance(sucursal_id, int):
        sucursal_id = None
    if not isinstance(solo_alertas, bool):
        solo_alertas = False
    if not isinstance(umbral_minimo, int):
        umbral_minimo = STOCK_CRITICO
    if not isinstance(page, int):
        page = 1
    if not isinstance(limit, int):
        limit = 20

    # RBAC e aislamiento multi-sucursal:
    # - GS y V: aislamiento forzado a su sucursal asignada (403 si intentan consultar otra o no tienen asignación).
    # - ASU: puede consultar global o filtrar por cualquier sucursal.
    # - C / otros: consulta stock disponible (global o sucursal).
    rol = _usuario.rol.nombre_rol.upper() if _usuario.rol and _usuario.rol.nombre_rol else ""
    if rol in ("GS", "V"):
        if not _usuario.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El personal operativo debe tener una sucursal asignada para consultar el stock local.",
            )
        if sucursal_id is not None and sucursal_id != _usuario.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tiene permisos para consultar el stock de otra sucursal. Su tienda asignada es {_usuario.id_sucursal}.",
            )
        sucursal_id = _usuario.id_sucursal

    if sucursal_id is not None:
        query = (
            db.query(InventarioSucursal)
            .join(InventarioSucursal.producto)
            .filter(InventarioSucursal.id_sucursal == sucursal_id)
        )
        if q:
            query = query.filter(Producto.nombre.ilike(f"%{q}%"))
        if solo_alertas:
            query = query.filter(InventarioSucursal.stock < umbral_minimo)

        total = query.count()
        items = (
            query.order_by(InventarioSucursal.stock.asc(), InventarioSucursal.id_producto)
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        data = [_serializar_stock_sucursal(inv, umbral_minimo) for inv in items]
    else:
        query = db.query(Producto)
        if q:
            query = query.filter(Producto.nombre.ilike(f"%{q}%"))
        if solo_alertas:
            query = query.filter(Producto.stock_total < umbral_minimo)

        total = query.count()
        items = (
            query.order_by(Producto.stock_total.asc(), Producto.id_producto)
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        data = [_serializar_stock(p, umbral_minimo) for p in items]

    return _envelope(
        data,
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
        umbral_minimo=umbral_minimo,
        alertas=sum(1 for d in data if d["nivel"] == "CRITICO"),
        sucursal_id=sucursal_id,
    )


# ---------------------------------------------------------------------------
# GET /movimientos — historial (kardex) con filtro por fecha y tipo
# ---------------------------------------------------------------------------
@router.get("/movimientos", response_model=None)
def listar_movimientos(
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
    sucursal_id: int | None = Query(default=None, description="Filtrar por sucursal"),
    tipo: str | None = Query(
        default=None, description="ENTRADA | SALIDA | AJUSTE"
    ),
    fecha: date | None = Query(
        default=None, description="Fecha exacta del movimiento (YYYY-MM-DD)"
    ),
    fecha_desde: date | None = Query(default=None, description="Rango: desde"),
    fecha_hasta: date | None = Query(default=None, description="Rango: hasta"),
    id_producto: int | None = Query(default=None, ge=1, description="Kardex de un producto"),
    q: str | None = Query(default=None, description="Busca por nombre de producto"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    if not isinstance(tipo, str):
        tipo = None
    if not isinstance(sucursal_id, int):
        sucursal_id = None
    if not isinstance(fecha, date):
        fecha = None
    if not isinstance(fecha_desde, date):
        fecha_desde = None
    if not isinstance(fecha_hasta, date):
        fecha_hasta = None
    if not isinstance(id_producto, int):
        id_producto = None
    if not isinstance(q, str):
        q = None
    if not isinstance(page, int):
        page = 1
    if not isinstance(limit, int):
        limit = 20

    _validar_tipo_filtro(tipo)

    rol = _usuario.rol.nombre_rol.upper() if _usuario.rol and _usuario.rol.nombre_rol else ""
    if rol not in ("ASU", "GS", "V"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el personal administrativo y operativo puede consultar el historial de movimientos de inventario.",
        )

    if rol in ("GS", "V"):
        if not _usuario.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El personal operativo debe tener una sucursal asignada para consultar el kardex.",
            )
        if sucursal_id is not None and sucursal_id != _usuario.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tiene permisos para consultar movimientos de otra sucursal. Su tienda asignada es {_usuario.id_sucursal}.",
            )
        sucursal_id = _usuario.id_sucursal

    query = db.query(MovimientoInventario)

    if sucursal_id is not None:
        query = query.filter(MovimientoInventario.id_sucursal == sucursal_id)
    if tipo:
        query = query.filter(MovimientoInventario.tipo == tipo)
    if fecha:
        query = query.filter(
            MovimientoInventario.fecha_movimiento >= _inicio_dia(fecha),
            MovimientoInventario.fecha_movimiento < _fin_dia(fecha),
        )
    if fecha_desde:
        query = query.filter(
            MovimientoInventario.fecha_movimiento >= _inicio_dia(fecha_desde)
        )
    if fecha_hasta:
        query = query.filter(
            MovimientoInventario.fecha_movimiento < _fin_dia(fecha_hasta)
        )
    if id_producto:
        query = query.filter(MovimientoInventario.id_producto == id_producto)
    if q:
        query = query.join(MovimientoInventario.producto).filter(
            Producto.nombre.ilike(f"%{q}%")
        )

    total = query.count()
    items = (
        query.order_by(MovimientoInventario.fecha_movimiento.desc(), MovimientoInventario.id_movimiento.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_movimiento(m) for m in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
        sucursal_id=sucursal_id,
    )


def _inicio_dia(d: date):
    from datetime import datetime, time

    return datetime.combine(d, time.min)


def _fin_dia(d: date):
    from datetime import datetime, timedelta, time

    return datetime.combine(d, time.min) + timedelta(days=1)


# ---------------------------------------------------------------------------
# POST /movimientos — registrar ENTRADA/SALIDA/AJUSTE (atómico)
# ---------------------------------------------------------------------------
@router.post(
    "/movimientos", response_model=None, status_code=status.HTTP_201_CREATED
)
def registrar_movimiento(
    payload: MovimientoCreatePayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU22: Registra un movimiento de stock y actualiza inventario_sucursal y productos.stock_total."""
    rol = usuario_actual.rol.nombre_rol.upper() if usuario_actual.rol and usuario_actual.rol.nombre_rol else ""
    if rol not in ("ASU", "GS", "V"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para registrar movimientos de inventario.",
        )

    if rol in ("GS", "V"):
        if not usuario_actual.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El personal operativo debe tener una sucursal asignada para gestionar inventario.",
            )
        if payload.id_sucursal is not None and payload.id_sucursal != usuario_actual.id_sucursal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No puede modificar el inventario de otra sucursal. Su tienda asignada es {usuario_actual.id_sucursal}.",
            )
        id_sucursal = usuario_actual.id_sucursal
    else:
        id_sucursal = payload.id_sucursal

    # Bloquear la fila del producto
    producto = (
        db.query(Producto)
        .filter(Producto.id_producto == payload.id_producto)
        .with_for_update(of=Producto)
        .first()
    )
    if not producto:
        raise HTTPException(
            status_code=422,
            detail=f"No existe el producto con id {payload.id_producto}.",
        )

    tipo = payload.tipo
    cantidad = payload.cantidad

    # Si hay sucursal asociada, gestionar InventarioSucursal
    if id_sucursal:
        inv_sucursal = (
            db.query(InventarioSucursal)
            .filter(
                InventarioSucursal.id_sucursal == id_sucursal,
                InventarioSucursal.id_producto == producto.id_producto,
            )
            .with_for_update(of=InventarioSucursal)
            .first()
        )
        if not inv_sucursal:
            inv_sucursal = InventarioSucursal(
                id_sucursal=id_sucursal,
                id_producto=producto.id_producto,
                stock=0,
                stock_minimo=5,
            )
            db.add(inv_sucursal)
            db.flush()

        stock_anterior = inv_sucursal.stock

        if tipo == "ENTRADA":
            stock_nuevo = stock_anterior + cantidad
        elif tipo == "SALIDA":
            if stock_anterior < cantidad:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Stock insuficiente en la sucursal: disponible {stock_anterior}, "
                        f"salida solicitada {cantidad}."
                    ),
                )
            stock_nuevo = stock_anterior - cantidad
        else:  # AJUSTE
            stock_nuevo = cantidad

        inv_sucursal.stock = stock_nuevo
        db.flush()

        # Alerta automática si el stock de la sucursal quedó en nivel crítico (<= 5)
        if tipo in ("SALIDA", "AJUSTE"):
            verificar_y_notificar_stock_critico(
                db,
                id_producto=producto.id_producto,
                id_sucursal=id_sucursal,
                stock_nuevo=stock_nuevo,
                commit=False,
            )

        # Recalcular stock_total del producto sumando todas las sucursales
        total_global = (
            db.query(func.coalesce(func.sum(InventarioSucursal.stock), 0))
            .filter(InventarioSucursal.id_producto == producto.id_producto)
            .scalar()
        )
        producto.stock_total = total_global
    else:
        stock_anterior = producto.stock_total
        if tipo == "ENTRADA":
            stock_nuevo = stock_anterior + cantidad
        elif tipo == "SALIDA":
            if stock_anterior < cantidad:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Stock insuficiente para '{producto.nombre}': "
                        f"disponible {stock_anterior}, salida solicitada {cantidad}."
                    ),
                )
            stock_nuevo = stock_anterior - cantidad
        else:  # AJUSTE
            stock_nuevo = cantidad

        producto.stock_total = stock_nuevo

    if producto.estado == "Agotado" and producto.stock_total > 0:
        producto.estado = "Activo"

    movimiento = MovimientoInventario(
        id_producto=producto.id_producto,
        tipo=tipo,
        cantidad=cantidad,
        stock_anterior=stock_anterior,
        stock_nuevo=stock_nuevo,
        motivo=payload.motivo,
        id_usuario=usuario_actual.id_usuario,
        id_sucursal=id_sucursal,
    )
    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    return _envelope(
        _serializar_movimiento(movimiento),
        message="Movimiento registrado correctamente.",
    )
