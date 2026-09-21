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
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Date, cast, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles
from app.modules.delivery.service import crear_envio_para_venta
from app.modules.inventario.models import InventarioSucursal, MovimientoInventario, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.notificaciones.service import emitir
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import (
    Carrito,
    DetalleReserva,
    DetalleVenta,
    Reserva,
    TransaccionPago,
    Venta,
)
from app.schemas.venta import CheckoutPayload, CobroEfectivoPayload

router = APIRouter()

# Regla de envío del frontend (CarritoService): gratis >= Bs 300,
# si no Bs 25. Un solo criterio replicado server-side para que el
# total del backend coincida con lo que vio el cliente en el checkout.
ENVIO_GRATIS_DESDE = 300
COSTO_ENVIO = 25

# Roles con permiso para registrar ventas POS (CU11). El Cliente (C)
# queda fuera: solo puede comprar por el flujo online (CU15+CU21).
ROLES_POS = ("ASU", "GS", "V")


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
        "id_sucursal": v.id_sucursal,
        "sucursal_nombre": v.sucursal.nombre if v.sucursal else None,
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
        "vendedor_id": str(v.id_vendedor) if v.id_vendedor else None,
        "tipo_venta": "POS" if v.id_vendedor else "ONLINE",
        "tipo_entrega": v.tipo_entrega,
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


def _resolver_sucursal_con_stock(
    db: Session,
    items: list,
    ciudad_entrega: str | None = None,
) -> int | None:
    """Selecciona la sucursal más idónea con stock disponible para los items.
    
    Prioriza sucursales en la misma ciudad de entrega (CU18 logística inteligente).
    Si ninguna coincide por ciudad, retorna la primera sucursal con stock disponible.
    """
    from app.modules.empresa.models import Sucursal

    pids = [getattr(it, "producto_id", None) or getattr(it, "id_producto", None) for it in items]
    pids = [pid for pid in pids if pid]
    if not pids:
        return None

    sucursales = db.query(Sucursal).filter(Sucursal.is_active.is_(True)).all()
    if not sucursales:
        return None

    if ciudad_entrega:
        c_term = ciudad_entrega.strip().lower()
        sucursales = sorted(
            sucursales,
            key=lambda s: 0 if (s.ciudad and c_term in s.ciudad.nombre.lower()) else 1,
        )

    for suc in sucursales:
        tiene_todo = True
        for it in items:
            pid = getattr(it, "producto_id", None) or getattr(it, "id_producto", None)
            cant = getattr(it, "cantidad", 1)
            inv = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == suc.codigo_sucursal,
                    InventarioSucursal.id_producto == pid,
                )
                .first()
            )
            if not inv or inv.stock < cant:
                tiene_todo = False
                break
        if tiene_todo:
            return suc.codigo_sucursal

    return sucursales[0].codigo_sucursal if sucursales else None


# ---------------------------------------------------------------------------
# Lógica común de venta (transacción atómica). NO es una ruta: la invocan
#   POST /checkout  (solo Cliente, flujo ONLINE)   y
#   POST /pos       (solo V/GS/ASU, venta de mostrador).
# El rol se autoriza en la DEPENDENCIA de cada ruta (require_roles); aquí
# `modo` decide el flujo, no el `tipo_venta` que mande el payload.
# ---------------------------------------------------------------------------
def _procesar_venta(
    payload: CheckoutPayload,
    db: Session,
    usuario_actual: Usuario,
    modo: str,
):
    """CU15+CU21 (digital) + CU11 (POS): Procesa la venta en UNA transacción.

    Flujos soportados (`modo`):
    - ONLINE: Cliente compra desde el e-commerce. El id_cliente se toma
      del token. El Vendedor queda NULL.
    - POS: Vendedor/GS/ASU cobra en mostrador. El id_cliente debe venir
      en payload.id_cliente_override (debe existir y tener rol C). El
      id_vendedor se setea con el id del token.

    Restricciones:
    - modo=POS sin id_cliente_override -> 422.
    - id_cliente_override con un usuario que no es rol C -> 422.
    - Precios: resueltos desde productos.precio_venta (DB) — el payload
      NO lleva precios (anti-manipulación).
    - Stock: FOR UPDATE OF productos + validación; 409 si no alcanza,
      422 si un producto no existe o está Inactivo.
    - Kardex: registra SALIDA por producto (CU22) en la misma tx.
    - Pasarela: mock — la venta queda PAGADA (la real llega con su CU).
    """
    # --- 0. Resolver tipo de venta y validar coherencia con el rol ------------
    rol_nombre = usuario_actual.rol.nombre_rol if usuario_actual.rol else ""

    if modo == "POS":
        # Defensa en profundidad: la ruta /pos ya exige V/GS/ASU (require_roles).
        if rol_nombre not in ROLES_POS:
            raise HTTPException(
                status_code=403,
                detail=(
                    "El modo POS solo está disponible para Vendedor, "
                    "Gerente de Sucursal o Administrador."
                ),
            )
        if payload.id_cliente_override is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Para registrar una venta POS debe indicar el cliente "
                    "(id_cliente_override)."
                ),
            )
        # Verificar que el cliente existe y tiene rol C.
        cliente = db.get(Usuario, payload.id_cliente_override)
        if not cliente:
            raise HTTPException(
                status_code=422,
                detail=f"No existe el cliente con id {payload.id_cliente_override}.",
            )
        if not cliente.rol or cliente.rol.nombre_rol != "C":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"El usuario con id {payload.id_cliente_override} no es un "
                    "cliente (rol C)."
                ),
            )
        id_cliente_final = cliente.id_usuario
        id_vendedor_final = usuario_actual.id_usuario
    else:
        # ONLINE: el cliente SIEMPRE es el usuario del token, sin importar
        # qué manden en el payload. Esto previene que un Cliente autenticado
        # compre a nombre de otro.
        id_cliente_final = usuario_actual.id_usuario
        id_vendedor_final = None

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
    # CU18: ONLINE => DOMICILIO y POS (mostrador) => RETIRO, salvo que el
    # payload indique otra cosa explícitamente.
    tipo_entrega = payload.tipo_entrega or (
        "RETIRO" if id_vendedor_final else "DOMICILIO"
    )
    id_sucursal_final = payload.id_sucursal or (usuario_actual.id_sucursal if usuario_actual else None)
    if not id_sucursal_final and tipo_entrega == "DOMICILIO":
        id_sucursal_final = _resolver_sucursal_con_stock(db, payload.items, entrega.ciudad)

    venta = Venta(
        id_cliente=id_cliente_final,
        id_vendedor=id_vendedor_final,
        id_sucursal=id_sucursal_final,
        tipo_entrega=tipo_entrega,
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
    motivo_venta = (
        f"Venta POS {venta.codigo} (vendedor: {usuario_actual.correo})"
        if id_vendedor_final
        else f"Venta {venta.codigo} (checkout online)"
    )
    for item, producto, precio, subtotal in lineas:
        stock_anterior = producto.stock_total
        producto.stock_total = stock_anterior - item.cantidad
        if producto.stock_total == 0:
            producto.estado = "Agotado"  # agotamiento automático

        # Descontar stock físico en la sucursal correspondiente
        if id_sucursal_final:
            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == id_sucursal_final,
                    InventarioSucursal.id_producto == producto.id_producto,
                )
                .with_for_update(of=InventarioSucursal)
                .first()
            )
            if inv_suc:
                inv_suc.stock = max(0, inv_suc.stock - item.cantidad)
                verificar_y_notificar_stock_critico(
                    db,
                    id_producto=producto.id_producto,
                    id_sucursal=id_sucursal_final,
                    stock_nuevo=inv_suc.stock,
                    commit=False,
                )

        db.add(
            MovimientoInventario(
                id_producto=producto.id_producto,
                tipo="SALIDA",
                cantidad=item.cantidad,
                stock_anterior=stock_anterior,
                stock_nuevo=producto.stock_total,
                motivo=motivo_venta,
                id_usuario=usuario_actual.id_usuario,
                id_sucursal=id_sucursal_final,
            )
        )

    # --- 6. CU18: envío a domicilio (misma transacción, sin commit propio) -----
    if tipo_entrega == "DOMICILIO":
        crear_envio_para_venta(db, venta, usuario_actual)

    # CU15: Vaciar carrito persistente en base de datos si el cliente tenía ítems allí
    carrito_db = db.query(Carrito).filter(Carrito.id_usuario == id_cliente_final).first()
    if carrito_db:
        for it in list(carrito_db.items):
            db.delete(it)

    db.commit()
    db.refresh(venta)

    return _envelope(
        _serializar_venta(venta),
        message=(
            "Venta POS registrada correctamente."
            if id_vendedor_final
            else "Compra procesada correctamente."
        ),
    )


# ---------------------------------------------------------------------------
# POST /checkout — compra ONLINE: SOLO rol Cliente (C)
# ---------------------------------------------------------------------------
@router.post(
    "/checkout", response_model=None, status_code=status.HTTP_201_CREATED
)
def procesar_checkout(
    payload: CheckoutPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles(
            "C",
            detail=(
                "Solo el rol Cliente puede comprar desde el carrito/checkout. "
                "Las ventas de mostrador (POS) usan POST /api/v1/ventas/pos."
            ),
        )
    ),
):
    """CU15+CU21: compra online del Cliente (carrito -> checkout).

    - 401 sin token; 403 para cualquier rol distinto de C (ASU, GS, V, D).
    - El id_cliente SIEMPRE es el del token; un Cliente que mande
      tipo_venta='POS' recibe 403 (el mostrador es otro flujo: /pos).
    """
    if payload.tipo_venta == "POS":
        raise HTTPException(
            status_code=403,
            detail=(
                "El modo POS solo está disponible para Vendedor, "
                "Gerente de Sucursal o Administrador (POST /api/v1/ventas/pos)."
            ),
        )
    return _procesar_venta(payload, db, usuario_actual, "ONLINE")


# ---------------------------------------------------------------------------
# POST /pos — venta de mostrador (CU11): SOLO V / GS / ASU
# ---------------------------------------------------------------------------
@router.post("/pos", response_model=None, status_code=status.HTTP_201_CREATED)
def procesar_venta_pos(
    payload: CheckoutPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles(
            *ROLES_POS,
            detail=(
                "El modo POS solo está disponible para Vendedor, "
                "Gerente de Sucursal o Administrador."
            ),
        )
    ),
):
    """CU11: el Vendedor/GS/ASU registra una venta en mostrador a nombre de
    un Cliente (id_cliente_override obligatorio). Es "registrar una venta",
    no comprar: por eso queda fuera de la regla "solo Cliente compra".
    El Cliente (C) y el Delivery (D) reciben 403.
    """
    return _procesar_venta(payload, db, usuario_actual, "POS")


# ---------------------------------------------------------------------------
# POST /cobrar-efectivo — cobro presencial en sucursal (CU21): V / GS / ASU
# ---------------------------------------------------------------------------
@router.post("/cobrar-efectivo", response_model=None, status_code=status.HTTP_200_OK)
def cobrar_en_efectivo(
    payload: CobroEfectivoPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles(
            *ROLES_POS,
            detail="Solo personal de tienda (Vendedor, Gerente o Administrador) puede registrar pagos en efectivo.",
        )
    ),
):
    """CU21: Registra el cobro en efectivo en mostrador y emite el comprobante.

    Permite liquidar:
    1. Una RESERVA pendiente (CU14 -> CU21):
       - Convierte la reserva en una Venta confirmada (PAGADA en EFECTIVO).
       - Marca la reserva como COMPLETADA (el stock ya estaba apartado).
       - Emite el comprobante de caja y registra el movimiento de inventario formal.
    2. Una VENTA pendiente de pago:
       - Cambia el estado a PAGADO con método EFECTIVO y asigna el vendedor en caja.
    """
    rol_nombre = (usuario_actual.rol.nombre_rol if usuario_actual.rol else "").upper()
    id_sucursal_vendedor = getattr(usuario_actual, "id_sucursal", None)

    if rol_nombre in ("V", "GS") and not id_sucursal_vendedor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El empleado no tiene una sucursal asignada para realizar cobros en caja.",
        )

    reserva_liquidada_id: int | None = None

    if payload.id_reserva:
        reserva = db.get(Reserva, payload.id_reserva)
        if not reserva:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No existe la reserva con ID {payload.id_reserva}.",
            )
        if reserva.estado not in ("PENDIENTE", "CONFIRMADA"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La reserva se encuentra en estado '{reserva.estado}' y no puede liquidarse.",
            )

        # Validar aislamiento de sucursal para V y GS
        if rol_nombre in ("V", "GS") and reserva.id_sucursal:
            if reserva.id_sucursal != id_sucursal_vendedor:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"No puede cobrar una reserva de otra sucursal. Asignada: {reserva.id_sucursal}, su tienda: {id_sucursal_vendedor}.",
                )

        id_sucursal_final = reserva.id_sucursal or id_sucursal_vendedor
        cliente = reserva.cliente
        nom_cliente = f"{cliente.nombre} {cliente.apellido or ''}".strip() if cliente else "Cliente Mostrador"

        total_venta = float(reserva.total_estimado)
        codigo_ticket = _generar_codigo()

        # Crear la venta física en estado PAGADO
        venta = Venta(
            codigo=codigo_ticket,
            id_cliente=reserva.id_cliente,
            id_vendedor=usuario_actual.id_usuario,
            id_sucursal=id_sucursal_final,
            tipo_entrega="RETIRO",
            total=total_venta,
            costo_envio=0.0,
            metodo_pago="EFECTIVO",
            estado_pago="PAGADO",
            nombre_cliente=nom_cliente,
            correo=cliente.correo if cliente else "sin_correo@attention.com",
            telefono=getattr(cliente, "telefono", "") or "",
            direccion="Retiro en mostrador / Liquidación Reserva",
            ciudad=reserva.sucursal.nombre if reserva.sucursal else "Local",
        )
        db.add(venta)
        db.flush()

        for d in reserva.detalles:
            db.add(
                DetalleVenta(
                    id_venta=venta.id_venta,
                    id_producto=d.id_producto,
                    cantidad=d.cantidad,
                    precio_unitario=float(d.precio_unitario),
                    subtotal=round(d.cantidad * float(d.precio_unitario), 2),
                )
            )
            prod = d.producto
            stock_actual = prod.stock_total if prod else 0
            db.add(
                MovimientoInventario(
                    id_producto=d.id_producto,
                    tipo="SALIDA",
                    cantidad=d.cantidad,
                    stock_anterior=stock_actual,
                    stock_nuevo=stock_actual,
                    motivo=f"Cobro en efectivo y entrega de Reserva #{reserva.id_reserva} (Ticket {codigo_ticket})",
                    id_usuario=usuario_actual.id_usuario,
                    id_sucursal=id_sucursal_final,
                )
            )

        # Completar la reserva
        reserva.estado = "COMPLETADA"
        reserva_liquidada_id = reserva.id_reserva

        # Registrar transacción de pago en efectivo
        codigo_txn = f"TXN-CASH-{codigo_ticket}"
        db.add(
            TransaccionPago(
                id_venta=venta.id_venta,
                pasarela="CajaSucursal",
                codigo_transaccion=codigo_txn,
                monto=total_venta,
                metodo_pago="EFECTIVO",
                estado="PAGADO",
                detalles_pago=f"Cobro en efectivo realizado por {usuario_actual.nombre} ({rol_nombre})",
            )
        )

        # Notificar al cliente (CU10)
        emitir(
            db,
            id_usuario=reserva.id_cliente,
            titulo="Reserva Entregada y Pagada",
            mensaje=f"Su reserva #{reserva.id_reserva} ha sido cobrada y entregada con éxito. Ticket: {codigo_ticket}.",
            tipo="SUCCESS",
            referencia_tipo="venta",
            referencia_id=str(venta.id_venta),
            commit=False,
        )

    else:
        # Liquidar Venta existente pendiente
        venta = _buscar_venta(db, payload.id_venta)
        if venta.estado_pago == "PAGADO":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La venta {venta.codigo} ya se encuentra registrada como PAGADA.",
            )

        if rol_nombre in ("V", "GS") and venta.id_sucursal:
            if venta.id_sucursal != id_sucursal_vendedor:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"No puede cobrar una venta de otra sucursal. Asignada: {venta.id_sucursal}, su tienda: {id_sucursal_vendedor}.",
                )

        venta.estado_pago = "PAGADO"
        venta.metodo_pago = "EFECTIVO"
        venta.id_vendedor = usuario_actual.id_usuario
        total_venta = float(venta.total)
        codigo_ticket = venta.codigo

        txn = db.query(TransaccionPago).filter(TransaccionPago.id_venta == venta.id_venta).first()
        if txn:
            txn.estado = "PAGADO"
            txn.metodo_pago = "EFECTIVO"
            txn.detalles_pago = f"Liquidado en mostrador por {usuario_actual.nombre}"
        else:
            db.add(
                TransaccionPago(
                    id_venta=venta.id_venta,
                    pasarela="CajaSucursal",
                    codigo_transaccion=f"TXN-CASH-{codigo_ticket}",
                    monto=total_venta,
                    metodo_pago="EFECTIVO",
                    estado="PAGADO",
                )
            )

    monto_recibido = payload.monto_recibido if payload.monto_recibido is not None else total_venta
    if monto_recibido < total_venta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El monto recibido (Bs. {monto_recibido:.2f}) es inferior al total a pagar (Bs. {total_venta:.2f}).",
        )
    cambio = round(monto_recibido - total_venta, 2)

    db.commit()
    db.refresh(venta)

    nombre_suc = venta.sucursal.nombre if venta.sucursal else (f"Sucursal {id_sucursal_vendedor}" if id_sucursal_vendedor else "Sucursal Central")

    comprobante = {
        "venta": _serializar_venta(venta),
        "reserva_liquidada_id": reserva_liquidada_id,
        "monto_total": total_venta,
        "monto_recibido": monto_recibido,
        "cambio_devuelto": cambio,
        "fecha_cobro": datetime.now(timezone.utc),
        "codigo_comprobante": f"RECIBO-{venta.codigo}",
        "vendedor_nombre": f"{usuario_actual.nombre} {usuario_actual.apellido or ''}".strip(),
        "sucursal_nombre": nombre_suc,
    }

    return _envelope(
        comprobante,
        message="Cobro en efectivo procesado y comprobante generado exitosamente.",
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
# GET / — historial de ventas (con alias /mis-compras para rol Cliente)
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
# /mis-compras provee compatibilidad explícita para clientes (CU11/CU13).
@router.get("/mis-compras", response_model=None)
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_ventas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    sucursal_id: int | None = Query(default=None, description="Filtrar por sucursal"),
    q: str | None = Query(
        default=None, description="Busca por código, cliente o correo"
    ),
    metodo_pago: str | None = Query(default=None, description="QR | EFECTIVO | TARJETA"),
    estado_pago: str | None = Query(
        default=None, description="PENDIENTE | PAGADO | RECHAZADO"
    ),
    tipo_venta: str | None = Query(
        default=None, description="ONLINE | POS"
    ),
    fecha_desde: date | None = Query(
        default=None, description="Filtra ventas con fecha_venta >= fecha_desde (YYYY-MM-DD)"
    ),
    fecha_hasta: date | None = Query(
        default=None, description="Filtra ventas con fecha_venta <= fecha_hasta (YYYY-MM-DD)"
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU15+CU21 + CU11: Historial de ventas con aislamiento por rol y sucursal.

    Aislamiento de visibilidad (forzado por rol del token, no bypaseable desde el frontend):
    - C (Cliente): solo ve SUS compras (id_cliente == user.id_usuario).
    - V (Vendedor): solo ve las ventas POS que ÉL registró (id_vendedor == user.id_usuario).
    - GS (Gerente de Sucursal): solo ve las ventas de SU sucursal (id_sucursal == user.id_sucursal).
    - ASU (Administrador): ve todas las ventas (puede filtrar opcionalmente por sucursal_id).
    """
    query = db.query(Venta)

    rol_nombre = usuario.rol.nombre_rol.upper() if usuario.rol and usuario.rol.nombre_rol else ""

    # Aislamiento por rol (filtros WHERE no bypaseables desde frontend)
    if rol_nombre == "C":
        query = query.filter(Venta.id_cliente == usuario.id_usuario)
    elif rol_nombre == "V":
        query = query.filter(Venta.id_vendedor == usuario.id_usuario)
        if usuario.id_sucursal is not None:
            query = query.filter(Venta.id_sucursal == usuario.id_sucursal)
    elif rol_nombre == "GS":
        if usuario.id_sucursal is not None:
            query = query.filter(Venta.id_sucursal == usuario.id_sucursal)
    elif sucursal_id is not None:
        query = query.filter(Venta.id_sucursal == sucursal_id)

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
    # tipo_venta: filtro derivado de id_vendedor IS NULL/NOT NULL
    if tipo_venta == "ONLINE":
        query = query.filter(Venta.id_vendedor.is_(None))
    elif tipo_venta == "POS":
        query = query.filter(Venta.id_vendedor.is_not(None))
    if fecha_desde:
        query = query.filter(
            cast(Venta.fecha_venta, Date) >= fecha_desde
        )
    if fecha_hasta:
        query = query.filter(
            cast(Venta.fecha_venta, Date) <= fecha_hasta
        )

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
        sucursal_id=usuario.id_sucursal if rol_nombre == "GS" else sucursal_id,
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

    Un Cliente (rol C) solo ve SUS ventas; GS solo ve las de su sucursal; ASU/V según permisos.
    """
    venta = _buscar_venta(db, id_venta)

    rol_nombre = usuario.rol.nombre_rol.upper() if usuario.rol and usuario.rol.nombre_rol else ""
    if rol_nombre == "C" and str(venta.id_cliente) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=403,
            detail="No tiene acceso a esta venta.",
        )
    if rol_nombre == "GS" and usuario.id_sucursal is not None:
        if venta.id_sucursal is not None and venta.id_sucursal != usuario.id_sucursal:
            raise HTTPException(
                status_code=403,
                detail="No tiene acceso a ventas de otra sucursal.",
            )

    return _envelope(_serializar_venta(venta))
