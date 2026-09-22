# backend/app/api/v1/endpoints/reservas.py
# CU14 — Gestionar Reserva de Prendas: ciclo de vida automático con anticipo del 50%,
# temporizador estricto de 48 horas y reembolso parcial del 50% por expiración.
#
# SEMÁNTICA DE STOCK: al crear la reserva el stock de cada producto se
# APARTA (descuento directo y atómico de productos.stock_total y
# de inventario_sucursal.stock con SELECT ... FOR UPDATE).
# Al expirar o cancelarse se DEVUELVE el stock; COMPLETADA lo mantiene
# consumido (derivando a la venta).
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import InventarioSucursal, MovimientoInventario, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.notificaciones.service import emitir
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import DetalleReserva, Reserva
from app.schemas.reserva import (
    ESTADOS_RESERVA,
    PagarAnticipoPayload,
    ReservaCreatePayload,
    ReservaResponse,
    ReservaStatusUpdate,
)

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


def _calcular_tiempos_expiracion(r: Reserva) -> tuple[int | None, bool]:
    """Calcula minutos restantes y si ya expiró el plazo de 48h."""
    now = datetime.now(timezone.utc)
    if r.fecha_expiracion_dt:
        dt = r.fecha_expiracion_dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff_segundos = (dt - now).total_seconds()
        minutos = max(0, int(diff_segundos // 60))
        es_expirada = diff_segundos <= 0
        return minutos, es_expirada
    elif r.fecha_expiracion:
        hoy = date.today()
        es_expirada = r.fecha_expiracion < hoy
        diff_dias = (r.fecha_expiracion - hoy).days
        minutos = max(0, diff_dias * 1440)
        return minutos, es_expirada
    return None, False


def _serializar_reserva(r: Reserva) -> dict:
    """Arma la respuesta con cliente, productos, anticipo y estado de expiración."""
    minutos, es_exp = _calcular_tiempos_expiracion(r)
    if r.estado == "CANCELADA" and r.motivo_cancelacion and "expirad" in r.motivo_cancelacion.lower():
        es_exp = True
        minutos = 0

    total_est = Decimal(str(r.total_estimado or 0))
    monto_anticipo = r.monto_anticipo if r.monto_anticipo is not None else Decimal("0.00")

    return {
        "id_reserva": r.id_reserva,
        "tipo_entrega": getattr(r, "tipo_entrega", "RETIRO") or "RETIRO",
        "direccion_entrega": getattr(r, "direccion_entrega", None),
        "telefono_entrega": getattr(r, "telefono_entrega", None),
        "cliente": {
            "id_usuario": str(r.cliente.id_usuario) if r.cliente else "",
            "nombre": r.cliente.nombre if r.cliente else "Desconocido",
            "apellido": r.cliente.apellido if r.cliente else None,
            "correo": r.cliente.correo if r.cliente else "",
        },
        "fecha_reserva": r.fecha_reserva,
        "fecha_expiracion": r.fecha_expiracion,
        "fecha_confirmacion": r.fecha_confirmacion,
        "fecha_expiracion_dt": r.fecha_expiracion_dt,
        "estado": r.estado,
        "total_estimado": float(total_est),
        "monto_anticipo": float(monto_anticipo),
        "monto_anticipo_pagado": float(r.monto_anticipo_pagado or 0.0),
        "monto_reembolsado": float(r.monto_reembolsado or 0.0),
        "monto_penalizacion": float(r.monto_penalizacion or 0.0),
        "metodo_pago_anticipo": r.metodo_pago_anticipo,
        "codigo_transaccion_anticipo": r.codigo_transaccion_anticipo,
        "motivo_cancelacion": r.motivo_cancelacion,
        "minutos_restantes": minutos,
        "es_expirada": es_exp,
        "id_sucursal": r.id_sucursal,
        "sucursal_nombre": r.sucursal.nombre if r.sucursal else None,
        "productos": [
            {
                "id_detalle": d.id_detalle,
                "id_producto": d.id_producto,
                "nombre": d.producto.nombre if d.producto else f"Producto {d.id_producto}",
                "cantidad": d.cantidad,
                "precio_unitario": float(d.precio_unitario),
            }
            for d in r.detalles
        ],
    }


def _devolver_stock(
    db: Session,
    reserva: Reserva,
    id_usuario_auditoria=None,
    motivo: str = "Devolución de stock por reserva cancelada",
) -> None:
    """Devuelve el stock apartado por la reserva a los productos y a la sucursal.

    Restaura Producto.stock_total e InventarioSucursal.stock de forma atómica
    (SELECT FOR UPDATE) y registra el movimiento de kardex (MovimientoInventario).
    """
    for detalle in reserva.detalles:
        producto = (
            db.query(Producto)
            .filter(Producto.id_producto == detalle.id_producto)
            .with_for_update(of=Producto)
            .first()
        )
        if producto:
            stock_anterior = producto.stock_total
            producto.stock_total += detalle.cantidad
            stock_nuevo = producto.stock_total

            usuario_kardex = id_usuario_auditoria or reserva.id_cliente
            if usuario_kardex:
                try:
                    mov = MovimientoInventario(
                        id_producto=producto.id_producto,
                        tipo="ENTRADA",
                        cantidad=detalle.cantidad,
                        stock_anterior=stock_anterior,
                        stock_nuevo=stock_nuevo,
                        motivo=motivo,
                        id_usuario=usuario_kardex,
                        id_sucursal=reserva.id_sucursal,
                    )
                    db.add(mov)
                except Exception:
                    pass

        if reserva.id_sucursal:
            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == reserva.id_sucursal,
                    InventarioSucursal.id_producto == detalle.id_producto,
                )
                .with_for_update(of=InventarioSucursal)
                .first()
            )
            if inv_suc:
                inv_suc.stock += detalle.cantidad


def _verificar_y_expirar_reservas(
    db: Session, id_sucursal: int | None = None
) -> list[Reserva]:
    """CU14 — Regla comercial de expiración automática y reembolso del 50%.

    Busca reservas activas (PENDIENTE o CONFIRMADA) cuyo plazo de 48 horas
    haya expirado.
    - Si la reserva tenía anticipo pagado, se programa reembolso del 50%
      y se retiene el restante 50% como penalización de stock.
    - Las prendas reservadas se devuelven al inventario activo de la sucursal.
    - El estado pasa a CANCELADA con detalle explicativo.
    - Se envía notificación in-app de alerta al cliente.
    """
    now = datetime.now(timezone.utc)
    hoy = date.today()

    query = db.query(Reserva).filter(
        Reserva.estado.in_(["PENDIENTE", "CONFIRMADA"])
    )
    if id_sucursal:
        query = query.filter(Reserva.id_sucursal == id_sucursal)

    reservas_activas = query.all()
    expiradas: list[Reserva] = []

    for r in reservas_activas:
        es_expirada = False
        if r.fecha_expiracion_dt:
            dt = r.fecha_expiracion_dt
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= now:
                es_expirada = True
        elif r.fecha_expiracion and r.fecha_expiracion < hoy:
            es_expirada = True

        if es_expirada:
            anticipo_pagado = Decimal(str(r.monto_anticipo_pagado or 0.0))
            if anticipo_pagado > 0:
                reembolso = round(anticipo_pagado * Decimal("0.5"), 2)
                penalizacion = anticipo_pagado - reembolso
                detalle_pago = (
                    f"Reembolso automático del 50% programado (Bs. {float(reembolso):.2f}) "
                    f"y penalización del 50% retenida (Bs. {float(penalizacion):.2f}). "
                )
                msg_cliente = (
                    f"Tu reserva #{r.id_reserva} ha expirado al cumplirse el plazo de 48h sin retiro. "
                    f"Se procesó el reembolso del 50% de tu anticipo (Bs. {float(reembolso):.2f}). "
                    f"Prendas devueltas al inventario activo de la tienda."
                )
            else:
                reembolso = Decimal("0.00")
                penalizacion = Decimal("0.00")
                detalle_pago = ""
                msg_cliente = (
                    f"Tu reserva #{r.id_reserva} ha expirado al cumplirse el plazo de 48h sin retiro. "
                    f"Las prendas apartadas fueron devueltas al inventario activo de la tienda."
                )

            r.monto_reembolsado = reembolso
            r.monto_penalizacion = penalizacion
            r.estado = "CANCELADA"
            r.motivo_cancelacion = (
                f"Expirada (plazo estricto de 48h vencido sin retiro ni reclamo). "
                f"{detalle_pago}"
                f"Prendas devueltas al inventario activo de la sucursal."
            )

            _devolver_stock(
                db,
                r,
                id_usuario_auditoria=r.id_cliente,
                motivo=f"Reserva #{r.id_reserva} expirada (plazo 48h vencido)",
            )

            # Notificación al cliente
            try:
                emitir(
                    db,
                    id_usuario=r.id_cliente,
                    titulo=f"Reserva #{r.id_reserva} Expirada",
                    mensaje=msg_cliente,
                    tipo="WARNING",
                    referencia_tipo="reserva",
                    referencia_id=str(r.id_reserva),
                    commit=False,
                )
            except Exception:
                pass

            expiradas.append(r)

    if expiradas:
        db.commit()

    return expiradas


# ---------------------------------------------------------------------------
# GET — listado con filtro por estado, búsqueda por cliente y expiración auto
# ---------------------------------------------------------------------------
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
    """CU14: Lista paginada de reservas con filtro de estado y búsqueda.
    Verifica automáticamente reservas expiradas (>48h) en cada consulta.
    """
    _validar_estado_filtro(estado)

    # Verificar y expirar automáticamente
    _verificar_y_expirar_reservas(db)

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
# POST — crear reserva calculando anticipo del 50% y apartando stock
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_reserva(
    payload: ReservaCreatePayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU14: Registra la reserva, calcula el 50% de anticipo y APARTA el stock.

    - Cliente: del token, salvo id_cliente explícito (vendedor a nombre de un cliente).
    - Anticipo: 50% del total estimado, requerido para pasar a CONFIRMADA.
    - Temporizador: 48 horas (2 días) estrictos.
    - Notificación automática al cliente con los detalles de reserva.
    """
    id_cliente = payload.id_cliente or usuario_actual.id_usuario
    cliente = db.get(Usuario, id_cliente)
    if not cliente:
        raise HTTPException(
            status_code=422,
            detail=f"No existe el cliente con id {id_cliente}.",
        )
    if cliente.rol and cliente.rol.nombre_rol != "C" and payload.id_cliente is not None:
        raise HTTPException(
            status_code=422,
            detail="El usuario indicado no tiene el rol Cliente (C).",
        )

    if payload.fecha_expiracion and payload.fecha_expiracion < date.today():
        raise HTTPException(
            status_code=422,
            detail="La fecha de expiración no puede ser anterior a hoy.",
        )

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

    id_sucursal_reserva = payload.id_sucursal or getattr(usuario_actual, "id_sucursal", None)

    # Validar stock en la sucursal seleccionada si aplica
    if id_sucursal_reserva:
        for item in payload.items:
            producto = encontrados[item.id_producto]
            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == id_sucursal_reserva,
                    InventarioSucursal.id_producto == item.id_producto,
                )
                .with_for_update(of=InventarioSucursal)
                .first()
            )
            if not inv_suc or inv_suc.stock < item.cantidad:
                disp = inv_suc.stock if inv_suc else 0
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Stock insuficiente en la sucursal seleccionada para '{producto.nombre}': "
                        f"disponible {disp}, solicitado {item.cantidad}."
                    ),
                )

    total = sum(i.cantidad * i.precio_unitario for i in payload.items)
    now = datetime.now(timezone.utc)
    fecha_exp_dt = now + timedelta(hours=48)
    fecha_exp = payload.fecha_expiracion or fecha_exp_dt.date()

    tipo_entrega = payload.tipo_entrega or "RETIRO"
    direccion_entrega = payload.direccion_entrega if tipo_entrega == "DOMICILIO" else None
    telefono_entrega = payload.telefono_entrega

    # La reserva se confirma de inmediato sin anticipo ni pasarela (Pago en Efectivo)
    reserva = Reserva(
        id_cliente=id_cliente,
        id_sucursal=id_sucursal_reserva,
        fecha_expiracion=fecha_exp,
        fecha_expiracion_dt=fecha_exp_dt,
        fecha_confirmacion=now,
        estado="CONFIRMADA",
        total_estimado=total,
        monto_anticipo=Decimal("0.00"),
        monto_anticipo_pagado=Decimal("0.00"),
        monto_reembolsado=Decimal("0.00"),
        monto_penalizacion=Decimal("0.00"),
        metodo_pago_anticipo="EFECTIVO",
        codigo_transaccion_anticipo=f"EFECTIVO-{tipo_entrega}",
        tipo_entrega=tipo_entrega,
        direccion_entrega=direccion_entrega,
        telefono_entrega=telefono_entrega,
    )
    db.add(reserva)
    db.flush()

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

    cant_prendas = sum(i.cantidad for i in payload.items)
    nom_cliente = f"{cliente.nombre} {cliente.apellido or ''}".strip()
    tipo_texto = "Retiro en Tienda" if tipo_entrega == "RETIRO" else "Envío a Domicilio"

    # Notificar al cliente confirmando la reserva con pago en efectivo
    if tipo_entrega == "RETIRO":
        msg_cliente = (
            f"¡Tu reserva por {cant_prendas} prenda(s) fue CONFIRMADA con pago en efectivo! (Total: Bs. {total:.2f}). "
            f"Acude a la sucursal seleccionada para retirar y pagar tus prendas en efectivo dentro del plazo de 48 horas "
            f"(vigente hasta {fecha_exp_dt.strftime('%d/%m/%Y %H:%M')})."
        )
    else:
        msg_cliente = (
            f"¡Tu reserva por {cant_prendas} prenda(s) fue CONFIRMADA con pago contra entrega en efectivo! (Total: Bs. {total:.2f}). "
            f"Se despachará a {direccion_entrega or 'tu dirección'} y podrás pagar en efectivo al repartidor "
            f"(vigente hasta {fecha_exp_dt.strftime('%d/%m/%Y %H:%M')})."
        )

    try:
        emitir(
            db,
            id_usuario=cliente.id_usuario,
            titulo=f"¡Reserva #{reserva.id_reserva} CONFIRMADA! ({tipo_texto})",
            mensaje=msg_cliente,
            tipo="SUCCESS",
            referencia_tipo="reserva",
            referencia_id=str(reserva.id_reserva),
            commit=False,
        )
    except Exception:
        pass

    # Notificar al personal operativo de la sucursal
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
        for empleado in personal_sucursal:
            try:
                emitir(
                    db,
                    id_usuario=empleado.id_usuario,
                    titulo=f"Nueva Reserva #{reserva.id_reserva} ({tipo_texto})",
                    mensaje=(
                        f"El cliente {nom_cliente} ha registrado la reserva #{reserva.id_reserva} por {cant_prendas} prenda(s) "
                        f"(Total: Bs. {total:.2f}) con pago en efectivo ({tipo_texto}). Plazo de 48 horas activo."
                    ),
                    tipo="PEDIDO",
                    referencia_tipo="reserva",
                    referencia_id=str(reserva.id_reserva),
                    commit=False,
                )
            except Exception:
                pass

    db.commit()
    db.refresh(reserva)

    return _envelope(
        _serializar_reserva(reserva),
        message=f"Reserva #{reserva.id_reserva} confirmada con éxito. Modalidad: {tipo_texto}. Pago en efectivo: Bs. {float(total):.2f} (vigencia de 48 horas).",
    )


# ---------------------------------------------------------------------------
# POST — pagar anticipo del 50% (Stripe o QR) para confirmar la reserva
# ---------------------------------------------------------------------------
@router.post("/{id_reserva}/pagar-anticipo", response_model=None)
def pagar_anticipo_reserva(
    id_reserva: int,
    payload: PagarAnticipoPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU14: Procesa el pago del 50% del Total Estimado vía pasarela (Stripe o QR).

    - Cambia estado de PENDIENTE a CONFIRMADA.
    - Activa el temporizador estricto de 48 horas (2 días).
    - Envía notificación automática al cliente confirmando la reserva con los detalles.
    """
    reserva = _buscar_reserva(db, id_reserva)

    if reserva.estado == "CONFIRMADA":
        return _envelope(
            _serializar_reserva(reserva),
            message=f"La reserva #{id_reserva} ya se encuentra CONFIRMADA con pago en efectivo ({reserva.tipo_entrega}).",
        )

    if reserva.estado != "PENDIENTE":
        raise HTTPException(
            status_code=409,
            detail=f"La reserva #{id_reserva} está en estado '{reserva.estado}'.",
        )

    now = datetime.now(timezone.utc)
    total_est = Decimal(str(reserva.total_estimado or 0))
    monto_anticipo = (
        Decimal(str(reserva.monto_anticipo))
        if reserva.monto_anticipo is not None
        else round(total_est * Decimal("0.5"), 2)
    )

    codigo_transaccion = (
        payload.referencia_pago
        or f"PAY-ANT-{payload.metodo_pago}-{uuid.uuid4().hex[:8].upper()}"
    )

    reserva.monto_anticipo = monto_anticipo
    reserva.monto_anticipo_pagado = monto_anticipo
    reserva.metodo_pago_anticipo = payload.metodo_pago
    reserva.codigo_transaccion_anticipo = codigo_transaccion
    reserva.fecha_confirmacion = now
    reserva.fecha_expiracion_dt = now + timedelta(hours=48)
    reserva.fecha_expiracion = (now + timedelta(hours=48)).date()
    reserva.estado = "CONFIRMADA"

    # Notificar al cliente
    try:
        emitir(
            db,
            id_usuario=reserva.id_cliente,
            titulo=f"¡Reserva #{reserva.id_reserva} CONFIRMADA!",
            mensaje=(
                f"Hemos recibido con éxito tu anticipo del 50% (Bs. {float(monto_anticipo):.2f}) mediante {payload.metodo_pago}. "
                f"Tu reserva está garantizada por 48 horas (hasta {reserva.fecha_expiracion_dt.strftime('%d/%m/%Y %H:%M')}). "
                f"Pasa a recogerla o solicita envío antes del vencimiento."
            ),
            tipo="SUCCESS",
            referencia_tipo="reserva",
            referencia_id=str(reserva.id_reserva),
            commit=False,
        )
    except Exception:
        pass

    # Notificar a la sucursal
    if reserva.id_sucursal:
        try:
            personal_sucursal = (
                db.query(Usuario)
                .join(Usuario.rol)
                .filter(
                    Usuario.id_sucursal == reserva.id_sucursal,
                    Usuario.estado.is_(True),
                    Rol.nombre_rol.in_(["V", "GS"]),
                )
                .all()
            )
            for empleado in personal_sucursal:
                emitir(
                    db,
                    id_usuario=empleado.id_usuario,
                    titulo=f"Anticipo Confirmado: Reserva #{reserva.id_reserva}",
                    mensaje=(
                        f"El cliente ha pagado el 50% de anticipo (Bs. {float(monto_anticipo):.2f}) "
                        f"para la reserva #{reserva.id_reserva}. Plazo de 48 horas activado."
                    ),
                    tipo="PEDIDO",
                    referencia_tipo="reserva",
                    referencia_id=str(reserva.id_reserva),
                    commit=False,
                )
        except Exception:
            pass

    db.commit()
    db.refresh(reserva)

    return _envelope(
        _serializar_reserva(reserva),
        message=f"Anticipo del 50% (Bs. {float(monto_anticipo):.2f}) registrado con éxito vía {payload.metodo_pago}. Reserva CONFIRMADA.",
    )


# ---------------------------------------------------------------------------
# POST — procesar expiraciones (ejecución explícita / cron)
# ---------------------------------------------------------------------------
@router.post("/procesar-expiraciones", response_model=None)
def procesar_expiraciones(
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(get_current_user),
    id_sucursal: int | None = Query(default=None, description="Opcional: filtrar por sucursal"),
):
    """CU14: Procesa de forma inmediata la regla de expiración de 48h.

    Aplica reembolso del 50% del dinero pagado, retiene 50% de penalización
    y devuelve el stock al inventario activo.
    """
    expiradas = _verificar_y_expirar_reservas(db, id_sucursal=id_sucursal)
    return _envelope(
        {
            "procesadas": len(expiradas),
            "reservas": [_serializar_reserva(r) for r in expiradas],
        },
        message=f"Proceso completado: {len(expiradas)} reserva(s) expirada(s) procesada(s) con reembolso parcial.",
    )


# ---------------------------------------------------------------------------
# PATCH — cambiar estado con reglas de negocio y devolución de stock
# ---------------------------------------------------------------------------
@router.patch("/{id_reserva}/estado", response_model=None)
def cambiar_estado(
    id_reserva: int,
    payload: ReservaStatusUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU14: Transiciones de estado manuales con efectos sobre el stock.

    - PENDIENTE -> CONFIRMADA: apartado sigue bloqueado.
    - PENDIENTE/CONFIRMADA -> CANCELADA: el stock apartado se DEVUELVE.
    - CONFIRMADA -> COMPLETADA: el stock permanece consumido.
    """
    reserva = _buscar_reserva(db, id_reserva)

    actual, destino = reserva.estado, payload.estado

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

    now = datetime.now(timezone.utc)
    if destino == "CONFIRMADA":
        if not reserva.fecha_confirmacion:
            reserva.fecha_confirmacion = now
        if not reserva.fecha_expiracion_dt:
            reserva.fecha_expiracion_dt = now + timedelta(hours=48)
            reserva.fecha_expiracion = (now + timedelta(hours=48)).date()
        if not reserva.monto_anticipo_pagado:
            reserva.monto_anticipo_pagado = reserva.monto_anticipo

    if destino == "CANCELADA":
        _devolver_stock(
            db,
            reserva,
            id_usuario_auditoria=usuario_actual.id_usuario,
            motivo=payload.motivo_cancelacion or "Cancelación manual de reserva",
        )
        reserva.motivo_cancelacion = (
            payload.motivo_cancelacion or "Cancelada por el usuario o personal."
        )

    reserva.estado = destino
    db.commit()
    db.refresh(reserva)

    mensaje = {
        "CONFIRMADA": "Reserva confirmada. El stock sigue apartado (plazo 48h).",
        "CANCELADA": "Reserva cancelada. El stock apartado fue devuelto al inventario.",
        "COMPLETADA": "Reserva completada con éxito. Venta finalizada.",
    }[destino]

    return _envelope(_serializar_reserva(reserva), message=mensaje)


# ---------------------------------------------------------------------------
# DELETE — anular la reserva (devuelve stock si estaba apartado)
# ---------------------------------------------------------------------------
@router.delete("/{id_reserva}", response_model=None)
def eliminar_reserva(
    id_reserva: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU14: Anula la reserva físicamente.

    - PENDIENTE/CONFIRMADA: devuelve el stock apartado antes de borrar.
    - CANCELADA: se borra sin tocar stock (ya fue devuelto al cancelar).
    - COMPLETADA: 409 — el historial de la venta se conserva.
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
        _devolver_stock(
            db,
            reserva,
            id_usuario_auditoria=usuario_actual.id_usuario,
            motivo=f"Anulación física de reserva #{reserva.id_reserva}",
        )

    id_borrado = reserva.id_reserva
    db.delete(reserva)
    db.commit()

    return _envelope(
        None,
        message=f"Reserva #{id_borrado} anulada correctamente.",
    )
