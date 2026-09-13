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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import MovimientoInventario, Producto
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


def _serializar_stock(p: Producto, umbral: int = STOCK_CRITICO) -> dict:
    """Fila del GET /stock: producto + stock + nivel de alerta."""
    return {
        "id_producto": p.id_producto,
        "nombre": p.nombre,
        "categoria": p.categoria.nombre if p.categoria else None,
        "estado": p.estado,
        "stock_total": p.stock_total,
        "umbral_minimo": umbral,
        "nivel": _nivel_stock(p.stock_total),
    }


def _serializar_movimiento(m: MovimientoInventario) -> dict:
    """Movimiento con producto y usuario embebidos (respuesta detallada)."""
    return {
        "id_movimiento": m.id_movimiento,
        "tipo": m.tipo,
        "cantidad": m.cantidad,
        "stock_anterior": m.stock_anterior,
        "stock_nuevo": m.stock_nuevo,
        "motivo": m.motivo,
        "fecha_movimiento": m.fecha_movimiento,
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
    if valor is not None and valor not in TIPOS_MOVIMIENTO:
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
    """CU22: Stock actual por producto con nivel de alerta (CRITICO/BAJO/OK).

    - `solo_alertas=true` + `umbral_minimo` => StockAlertResponse (productos
      con stock < umbral).
    - `q` busca por nombre de producto.
    """
    query = db.query(Producto)

    if q:
        term = f"%{q}%"
        query = query.filter(or_(Producto.nombre.ilike(term)))
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
    )


# ---------------------------------------------------------------------------
# GET /movimientos — historial (kardex) con filtro por fecha y tipo
# ---------------------------------------------------------------------------
@router.get("/movimientos", response_model=None)
def listar_movimientos(
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
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
    """CU22: Historial de movimientos (kardex) con filtros.

    - `tipo`: ENTRADA/SALIDA/AJUSTE (422 si es otro valor).
    - `fecha`: filtro por fecha exacta; `fecha_desde`/`fecha_hasta`: rango.
    - `id_producto`: kardex de UN producto.
    """
    _validar_tipo_filtro(tipo)

    query = db.query(MovimientoInventario)

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
    """CU22: Registra un movimiento de stock y actualiza productos.stock_total.

    Semántica por tipo (alineada con el mock del frontend):
    - ENTRADA: stock += cantidad (cantidad > 0).
    - SALIDA: stock -= cantidad; 409 si no alcanza (stock nunca negativo).
    - AJUSTE: stock = cantidad (fija el stock total; 0 permitido).

    Atomicidad: SELECT FOR UPDATE OF productos bloquea la fila del
    producto hasta el COMMIT; kardex y productos se escriben juntos.
    """
    # Bloquear la fila del producto (concurrencia con reservas CU14 y
    # otros movimientos CU22). of= obligatorio por los outer joins.
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

    stock_anterior = producto.stock_total
    tipo = payload.tipo
    cantidad = payload.cantidad

    # Calcular stock nuevo según semántica del tipo
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

    # Kardex: para ENTRADA/SALIDA la cantidad es lo movido; para AJUSTE
    # guardamos el valor exacto y la delta se deriva de stock_anterior/
    # stock_nuevo (mismo criterio del mock del frontend).
    movimiento = MovimientoInventario(
        id_producto=producto.id_producto,
        tipo=tipo,
        cantidad=cantidad,
        stock_anterior=stock_anterior,
        stock_nuevo=stock_nuevo,
        motivo=payload.motivo,
        id_usuario=usuario_actual.id_usuario,
    )
    db.add(movimiento)

    # Actualización atómica del stock en la misma transacción
    producto.stock_total = stock_nuevo
    if producto.estado == "Agotado" and stock_nuevo > 0:
        producto.estado = "Activo"  # reingreso automático al reponer stock

    db.commit()
    db.refresh(movimiento)

    return _envelope(
        _serializar_movimiento(movimiento),
        message="Movimiento registrado correctamente.",
    )
