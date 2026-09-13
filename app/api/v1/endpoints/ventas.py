# backend/app/api/v1/endpoints/ventas.py
# CU15+CU21 — Carrito y Checkout: procesar compra atómica, detalle de
# venta con comprobante e historial de ventas.
#
# TRANSACCIÓN ATÓMICA del checkout (todo o nada):
#   1. SELECT FOR UPDATE OF productos (lock row-level, lección CU14/CU22)
#   2. Validar existencia, estado Activo y stock suficiente (409/422)
#   3. Calcular subtotales/total/envío CON PRECIOS REALES DE LA DB
#      (nunca los que envía el cliente — anti-manipulación)
#   4. INSERT venta (PAGADO) + detalle_ventas
#   5. UPDATE productos.stock_total (descuento)
#   6. INSERT kardex SALIDA por producto (movimientos_inventario CU22)
#   7. COMMIT — o ROLLBACK completo si algo falla
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import MovimientoInventario, Producto
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import DetalleVenta, Venta
from app.schemas.venta import CheckoutPayload

router = APIRouter()

# Regla de envío del frontend (CarritoService): gratis >= Bs 300,
# si no Bs 25. Un solo criterio replicado server-side para que el
# total del backend coincida con lo que vio el cliente en el checkout.
ENVIO_GRATIS_DESDE = 300
COSTO_ENVIO = 25


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _generar_codigo() -> str:
    """Comprobante legible del ticket: ATT- + 6 dígitos (colisión mínima)."""
    return f"ATT-{secrets.randbelow(1000000):06d}"


def _serializar_venta(v: Venta) -> dict:
    """Arma la respuesta completa con items y datos de entrega."""
    return {
        "id_venta": v.id_venta,
        "codigo": v.codigo,
        "fecha_venta": v.fecha_venta,
        "total": v.total,
        "costo_envio": v.costo_envio,
        "metodo_pago": v.metodo_pago,
        "estado_pago": v.estado_pago,
        "comprobante_url": v.comprobante_url,
        "items": [
            {
                "id_detalle": d.id_detalle,
                "producto_id": d.id_producto,
                "nombre": d.producto.nombre if d.producto else f"Producto {d.id_producto}",
                "talla": d.talla,
                "color": d.color,
                "cantidad": d.cantidad,
                "precio_unitario": d.precio_unitario,
                "subtotal": d.subtotal,
            }
            for d in v.detalles
        ],
        "datos_entrega": {
            "nombre_cliente": v.nombre_cliente,
            "correo": v.correo,
            "telefono": v.telefono,
            "direccion": v.direccion,
            "ciudad": v.ciudad,
            "referencia": v.referencia,
        },
        "cliente_id": str(v.id_cliente),
    }


def _buscar_venta(db: Session, id_venta: int) -> Venta:
    """404 consistente para endpoints con path param."""
    venta = db.get(Venta, id_venta)
    if not venta:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la venta con id {id_venta}.",
        )
    return venta


# ---------------------------------------------------------------------------
# POST /checkout — procesar la compra (transacción atómica)
# ---------------------------------------------------------------------------
@router.post(
    "/checkout", response_model=None, status_code=status.HTTP_201_CREATED
)
def procesar_checkout(
    payload: CheckoutPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU15+CU21: Procesa la compra del carrito en UNA transacción.

    - Cliente: del token de la sesión.
    - Precios: resueltos desde productos.precio_venta (DB) — el payload
      NO lleva precios (anti-manipulación).
    - Stock: FOR UPDATE OF productos + validación; 409 si no alcanza,
      422 si un producto no existe o está Inactivo.
    - Kardex: registra SALIDA por producto (CU22) en la misma tx.
    - Pasarela: mock — la venta queda PAGADA (la real llega con su CU).
    """
    # --- 1. Bloquear y resolver productos --------------------------------------
    # of=Producto obligatorio: las relaciones lazy="joined" generan outer
    # joins y PostgreSQL rechaza FOR UPDATE directo (lecciones CU14/CU22).
    ids = [i.producto_id for i in payload.items]
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

    # --- 2. Validar estado y stock; calcular subtotales con precio REAL --------
    lineas = []  # [(item, producto, precio_real, subtotal)]
    for item in payload.items:
        producto = encontrados[item.producto_id]
        if producto.estado != "Activo":
            raise HTTPException(
                status_code=409,
                detail=f"El producto '{producto.nombre}' no está disponible ({producto.estado}).",
            )
        if producto.stock_total < item.cantidad:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Stock insuficiente para '{producto.nombre}': "
                    f"disponible {producto.stock_total}, solicitado {item.cantidad}."
                ),
            )
        precio = float(producto.precio_venta)
        lineas.append((item, producto, precio, round(precio * item.cantidad, 2)))

    # --- 3. Calcular envío y total (misma regla del frontend) ------------------
    subtotal_general = round(sum(l[3] for l in lineas), 2)
    costo_envio = 0.0 if subtotal_general >= ENVIO_GRATIS_DESDE else float(COSTO_ENVIO)
    total = round(subtotal_general + costo_envio, 2)

    # --- 4. Registrar venta + detalles ------------------------------------------
    entrega = payload.datos_entrega
    venta = Venta(
        id_cliente=usuario_actual.id_usuario,
        total=total,
        costo_envio=costo_envio,
        metodo_pago=payload.metodo_pago,
        estado_pago="PAGADO",  # pasarela mock: siempre aprueba
        codigo=_generar_codigo(),
        nombre_cliente=entrega.nombre_cliente,
        correo=entrega.correo,
        telefono=entrega.telefono,
        direccion=entrega.direccion,
        ciudad=entrega.ciudad,
        referencia=entrega.referencia,
    )
    db.add(venta)
    db.flush()  # venta.id_venta disponible para los detalles

    for item, producto, precio, subtotal in lineas:
        db.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=item.cantidad,
                precio_unitario=precio,
                subtotal=subtotal,
                talla=item.talla,
                color=item.color,
            )
        )

    # --- 5. Descontar stock + kardex SALIDA (misma transacción) ----------------
    for item, producto, precio, subtotal in lineas:
        stock_anterior = producto.stock_total
        producto.stock_total = stock_anterior - item.cantidad
        if producto.stock_total == 0:
            producto.estado = "Agotado"  # agotamiento automático

        db.add(
            MovimientoInventario(
                id_producto=producto.id_producto,
                tipo="SALIDA",
                cantidad=item.cantidad,
                stock_anterior=stock_anterior,
                stock_nuevo=producto.stock_total,
                motivo=f"Venta {venta.codigo} (checkout online)",
                id_usuario=usuario_actual.id_usuario,
            )
        )

    db.commit()
    db.refresh(venta)

    return _envelope(
        _serializar_venta(venta),
        message="Compra procesada correctamente.",
    )


# ---------------------------------------------------------------------------
# GET /{id} — detalle y comprobante de una venta
# ---------------------------------------------------------------------------
@router.get("/{id_venta}", response_model=None)
def obtener_venta(
    id_venta: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU15+CU21: Detalle completo de la venta con su comprobante.

    Un Cliente (rol C) solo ve SUS ventas; ASU/GS/V ven cualquier venta.
    """
    venta = _buscar_venta(db, id_venta)

    es_cliente = usuario.rol and usuario.rol.nombre_rol == "C"
    if es_cliente and str(venta.id_cliente) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=403,
            detail="No tiene acceso a esta venta.",
        )

    return _envelope(_serializar_venta(venta))


# ---------------------------------------------------------------------------
# GET / — historial de ventas
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_ventas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    q: str | None = Query(
        default=None, description="Busca por código, cliente o correo"
    ),
    metodo_pago: str | None = Query(default=None, description="QR | EFECTIVO | TARJETA"),
    estado_pago: str | None = Query(
        default=None, description="PENDIENTE | PAGADO | RECHAZADO"
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU15+CU21: Historial de ventas (los Clientes solo ven las suyas).

    Filtros: búsqueda por código/nombre/correo, método de pago y estado.
    """
    query = db.query(Venta)

    # Un Cliente solo ve su propio historial de compras
    es_cliente = usuario.rol and usuario.rol.nombre_rol == "C"
    if es_cliente:
        query = query.filter(Venta.id_cliente == usuario.id_usuario)

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Venta.codigo.ilike(term),
                Venta.nombre_cliente.ilike(term),
                Venta.correo.ilike(term),
            )
        )
    if metodo_pago:
        query = query.filter(Venta.metodo_pago == metodo_pago)
    if estado_pago:
        query = query.filter(Venta.estado_pago == estado_pago)

    total = query.count()
    items = (
        query.order_by(Venta.fecha_venta.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_venta(v) for v in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )
