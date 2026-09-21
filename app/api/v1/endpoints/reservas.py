# backend/app/api/v1/endpoints/reservas.py
# CU14 — Gestionar Reserva de Prendas: listar, crear (apartar stock),
# cambiar estado (confirmar/cancelar/completar) y anular.
#
# SEMÁNTICA DE STOCK: al crear la reserva el stock de cada producto se
# APARTA (descuento directo y atómico de productos.stock_total con
# SELECT ... FOR UPDATE). CANCELADA/DELETE lo DEVUELVEN; COMPLETADA lo
# mantiene consumido (derivará a la venta cuando llegue el CU de ventas).
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import DetalleReserva, Reserva
from app.schemas.reserva import (
    ESTADOS_RESERVA,
    ReservaCreatePayload,
    ReservaResponse,
    ReservaStatusUpdate,
)
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.notificaciones.service import emitir

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _buscar_reserva(db: Session, id_reserva: int) -> Reserva:
    """404 consistente para endpoints con path param."""
    reserva = db.get(Reserva, id_reserva)
    if not reserva:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la reserva con id {id_reserva}.",
        )
    return reserva


def _validar_estado_filtro(valor: str | None) -> None:
    """422 si el filtro de estado no es un estado válido."""
    if valor is not None and valor not in ESTADOS_RESERVA:
        raise HTTPException(
            status_code=422,
            detail=f"Estado inválido '{valor}'. Valores permitidos: {', '.join(ESTADOS_RESERVA)}.",
        )


def _serializar_reserva(r: Reserva) -> dict:
    """Arma la respuesta con cliente y productos reservados."""
    return {
        "id_reserva": r.id_reserva,
        "cliente": {
            "id_usuario": str(r.cliente.id_usuario),
            "nombre": r.cliente.nombre,
            "apellido": r.cliente.apellido,
            "correo": r.cliente.correo,
        },
        "fecha_reserva": r.fecha_reserva,
        "fecha_expiracion": r.fecha_expiracion,
        "estado": r.estado,
        "total_estimado": r.total_estimado,
        "motivo_cancelacion": r.motivo_cancelacion,
        "id_sucursal": r.id_sucursal,
        "sucursal_nombre": r.sucursal.nombre if r.sucursal else None,
        "productos": [
            {
                "id_detalle": d.id_detalle,
                "id_producto": d.id_producto,
                "nombre": d.producto.nombre if d.producto else f"Producto {d.id_producto}",
                "cantidad": d.cantidad,
                "precio_unitario": d.precio_unitario,
            }
            for d in r.detalles
        ],
    }


def _devolver_stock(db: Session, reserva: Reserva) -> None:
    """Devuelve el stock apartado por la reserva a los productos.

    Se asume que la reserva tenía stock apartado (estado PENDIENTE o
    CONFIRMADA); el SELECT FOR UPDATE de cada producto garantiza la
    atomicidad frente a movimientos concurrentes.
    """
    for detalle in reserva.detalles:
        producto = (
            db.query(Producto)
            .filter(Producto.id_producto == detalle.id_producto)
            # of=Producto => "FOR UPDATE OF productos": bloquea SOLO esa tabla
            # (las relaciones lazy="joined" generan outer joins a los que
            # PostgreSQL no permite aplicar FOR UPDATE directo).
            .with_for_update(of=Producto)
            .first()
        )
        if producto:  # el producto pudo haber sido eliminado (FK huérfana tolerada)
            producto.stock_total += detalle.cantidad


# ---------------------------------------------------------------------------
# GET — listado con filtro por estado y búsqueda por cliente
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_reservas(
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
    estado: str | None = Query(default=None, description="PENDIENTE | CONFIRMADA | CANCELADA | COMPLETADA"),
    q: str | None = Query(default=None, description="Busca por nombre o correo del cliente"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU14: Lista paginada de reservas con filtro de estado y búsqueda."""
    _validar_estado_filtro(estado)

    query = db.query(Reserva)

    if estado:
        query = query.filter(Reserva.estado == estado)
    if q:
        term = f"%{q}%"
        query = query.join(Reserva.cliente).filter(
            or_(
                Usuario.nombre.ilike(term),
                Usuario.apellido.ilike(term),
                Usuario.correo.ilike(term),
            )
        )

    total = query.count()
    items = (
        query.order_by(Reserva.fecha_reserva.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_reserva(r) for r in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


# ---------------------------------------------------------------------------
# POST — crear reserva apartando stock atómicamente
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_reserva(
    payload: ReservaCreatePayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU14: Registra la reserva y APARTA el stock de los productos.

    - Cliente: del token, salvo id_cliente explícito (vendedor a nombre de
      un cliente). Se valida existencia y rol C.
    - Stock: SELECT FOR UPDATE por producto (bloqueo a concurrencia);
      409 si algún producto no alcanza.
    - Total: calculado server-side (cantidad × precio_unitario).
    - fecha_expiracion: 422 si es anterior a hoy.
    """
    # --- cliente de la reserva ------------------------------------------------
    id_cliente = payload.id_cliente or usuario_actual.id_usuario
    cliente = db.get(Usuario, id_cliente)
    if not cliente:
        raise HTTPException(
            status_code=422,
            detail=f"No existe el cliente con id {id_cliente}.",
        )
    if cliente.rol and cliente.rol.nombre_rol != "C" and payload.id_cliente is not None:
        # Un vendedor puede reservar a nombre de cualquier usuario, pero si
        # apunta explícitamente a un no-cliente avisamos (422).
        raise HTTPException(
            status_code=422,
            detail="El usuario indicado no tiene el rol Cliente (C).",
        )

    # --- fecha de expiración ---------------------------------------------------
    if payload.fecha_expiracion < date.today():
        raise HTTPException(
            status_code=422,
            detail="La fecha de expiración no puede ser anterior a hoy.",
        )

    # --- productos: existencia + stock con lock row-level ----------------------
    # FOR UPDATE OF productos evita la race condition entre dos reservas
    # concurrentes sobre el mismo producto (inventario decreciente
    # consistente). `of=` es obligatorio: Producto tiene relaciones
    # lazy="joined" y PostgreSQL rechaza FOR UPDATE sobre outer joins.
    ids = [i.id_producto for i in payload.items]
    productos = (
        db.query(Producto)
        .filter(Producto.id_producto.in_(ids))
        .with_for_update(of=Producto)
        .all()
    )
    encontrados = {p.id_producto: p for p in productos}
    faltantes = [i for i in set(ids) if i not in encontrados]
    if faltantes:
        raise HTTPException(
            status_code=422,
            detail=f"No existen productos con id(s): {', '.join(map(str, faltantes))}.",
        )

    # Validar stock suficiente y estado Activo de cada producto
    for item in payload.items:
        producto = encontrados[item.id_producto]
        if producto.estado != "Activo":
            raise HTTPException(
                status_code=409,
                detail=f"El producto '{producto.nombre}' no está activo ({producto.estado}).",
            )
        if producto.stock_total < item.cantidad:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Stock insuficiente para '{producto.nombre}': "
                    f"disponible {producto.stock_total}, solicitado {item.cantidad}."
                ),
            )

    # --- crear reserva + detalles + apartar stock ------------------------------
    total = sum(i.cantidad * i.precio_unitario for i in payload.items)

    id_sucursal_reserva = payload.id_sucursal or getattr(usuario_actual, "id_sucursal", None)

    reserva = Reserva(
        id_cliente=id_cliente,
        id_sucursal=id_sucursal_reserva,
        fecha_expiracion=payload.fecha_expiracion,
        estado="PENDIENTE",
        total_estimado=total,
    )
    db.add(reserva)
    db.flush()  # reserva.id_reserva disponible para los detalles

    for item in payload.items:
        db.add(
            DetalleReserva(
                id_reserva=reserva.id_reserva,
                id_producto=item.id_producto,
                cantidad=item.cantidad,
                precio_unitario=item.precio_unitario,
            )
        )
        encontrados[item.id_producto].stock_total -= item.cantidad

        if id_sucursal_reserva:
            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == id_sucursal_reserva,
                    InventarioSucursal.id_producto == item.id_producto,
                )
                .with_for_update(of=InventarioSucursal)
                .first()
            )
            if inv_suc:
                inv_suc.stock = max(0, inv_suc.stock - item.cantidad)
                verificar_y_notificar_stock_critico(
                    db,
                    id_producto=item.id_producto,
                    id_sucursal=id_sucursal_reserva,
                    stock_nuevo=inv_suc.stock,
                    commit=False,
                )

    # CU10: Notificar al personal operativo (Vendedores y Gerente) de la sucursal sobre la nueva reserva
    if id_sucursal_reserva:
        personal_sucursal = (
            db.query(Usuario)
            .join(Usuario.rol)
            .filter(
                Usuario.id_sucursal == id_sucursal_reserva,
                Usuario.estado.is_(True),
                Rol.nombre_rol.in_(["V", "GS"]),
            )
            .all()
        )
        cant_prendas = sum(i.cantidad for i in payload.items)
        nom_cliente = f"{cliente.nombre} {cliente.apellido or ''}".strip()
        for empleado in personal_sucursal:
            emitir(
                db,
                id_usuario=empleado.id_usuario,
                titulo=f"Nueva Reserva #{reserva.id_reserva}",
                mensaje=(
                    f"El cliente {nom_cliente} ha apartado {cant_prendas} prenda(s) "
                    f"en su sucursal (Total est: Bs. {total:.2f}). Fecha límite: {payload.fecha_expiracion}."
                ),
                tipo="PEDIDO",
                referencia_tipo="reserva",
                referencia_id=str(reserva.id_reserva),
                commit=False,
            )

    db.commit()
    db.refresh(reserva)

    return _envelope(
        _serializar_reserva(reserva),
        message="Reserva registrada correctamente. Stock apartado.",
    )


# ---------------------------------------------------------------------------
# PATCH — cambiar estado con reglas de negocio y devolución de stock
# ---------------------------------------------------------------------------
@router.patch("/{id_reserva}/estado", response_model=None)
def cambiar_estado(
    id_reserva: int,
    payload: ReservaStatusUpdate,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
):
    """CU14: Transiciones de estado con efectos sobre el stock.

    - PENDIENTE -> CONFIRMADA: apartado sigue bloqueado.
    - PENDIENTE/CONFIRMADA -> CANCELADA: el stock apartado se DEVUELVE.
    - CONFIRMADA -> COMPLETADA: el stock permanece consumido (la venta
      se materializará en el módulo de ventas cuando llegue su CU).
    """
    reserva = _buscar_reserva(db, id_reserva)

    actual, destino = reserva.estado, payload.estado

    # Matriz de transiciones válidas del ciclo de vida
    transiciones = {
        ("PENDIENTE", "CONFIRMADA"),
        ("PENDIENTE", "CANCELADA"),
        ("CONFIRMADA", "CANCELADA"),
        ("CONFIRMADA", "COMPLETADA"),
    }
    if (actual, destino) not in transiciones:
        raise HTTPException(
            status_code=409,
            detail=f"No se puede pasar de {actual} a {destino}.",
        )

    if destino == "CANCELADA":
        _devolver_stock(db, reserva)
        reserva.motivo_cancelacion = (
            payload.motivo_cancelacion or "Cancelada por el usuario."
        )

    reserva.estado = destino
    db.commit()
    db.refresh(reserva)

    mensaje = {
        "CONFIRMADA": "Reserva confirmada. El stock sigue apartado.",
        "CANCELADA": "Reserva cancelada. El stock apartado fue devuelto al inventario.",
        "COMPLETADA": "Reserva completada. La venta fue derivada al módulo de ventas.",
    }[destino]

    return _envelope(_serializar_reserva(reserva), message=mensaje)


# ---------------------------------------------------------------------------
# DELETE — anular la reserva (devuelve stock si estaba apartado)
# ---------------------------------------------------------------------------
@router.delete("/{id_reserva}", response_model=None)
def eliminar_reserva(
    id_reserva: int,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
):
    """CU14: Anula la reserva físicamente.

    - PENDIENTE/CONFIRMADA: devuelve el stock apartado antes de borrar.
    - CANCELADA: se borra sin tocar stock (ya fue devuelto al cancelar).
    - COMPLETADA: 409 — el historial de la reserva-venta se conserva.
    """
    reserva = _buscar_reserva(db, id_reserva)

    if reserva.estado == "COMPLETADA":
        raise HTTPException(
            status_code=409,
            detail=(
                "No se puede eliminar una reserva COMPLETADA: el historial "
                "de la venta se conserva."
            ),
        )

    if reserva.estado in ("PENDIENTE", "CONFIRMADA"):
        _devolver_stock(db, reserva)

    id_borrado = reserva.id_reserva
    db.delete(reserva)
    db.commit()

    return _envelope(
        None,
        message=f"Reserva {id_borrado} anulada correctamente.",
    )
