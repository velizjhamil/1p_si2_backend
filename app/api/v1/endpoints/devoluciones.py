# backend/app/api/v1/endpoints/devoluciones.py
# CU13 - Gestion de Devoluciones.
#
# Ciclo de vida: SOLICITADA -> APROBADA -> COMPLETADA, con RECHAZADA como
# salida desde SOLICITADA. La COMPLETADA dispara la ENTRADA del kardex
# (CU22) por cada linea devuelta, en la MISMA transaccion (atomicidad
# con el UPDATE de productos.stock_total).
#
# Reglas de autorizacion (forzadas server-side, no bypaseables):
# - POST (cliente solicita): rol C, y la venta debe ser SUYA.
# - GET (listado / detalle): C solo ve SUS devoluciones; V/GS/ASU ven todo.
# - PATCH /procesar: solo V/GS/ASU (los Clientes no procesan).
#
# Decisiones clave:
# - Cantidad maxima devuelta por linea: la cantidad original de la venta
#   MENOS lo ya devuelto en solicitudes previas (estado APROBADA o
#   COMPLETADA) para el mismo id_detalle_venta.
# - Ventana: por defecto 24h desde fecha_venta. Constante en el schema
#   (VENTANA_DEVOLUCION_HORAS); cambiable en un solo lugar.
# - Kardex al COMPLETAR: SELECT FOR UPDATE OF productos por linea,
#   escritura de MovimientoInventario tipo=ENTRADA, y UPDATE de
#   productos.stock_total. Mismo patron atomico que el checkout (CU15).
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.devoluciones.models import Devolucion, DetalleDevolucion
from app.modules.inventario.models import (
    InventarioSucursal,
    MovimientoInventario,
    Producto,
)
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import DetalleVenta, Venta
from app.schemas.devolucion import (
    ROLES_PROCESAMIENTO,
    VENTANA_DEVOLUCION_HORAS,
    DevolucionCreatePayload,
    DevolucionProcesarPayload,
    DetalleDevolucionResponse,
    DevolucionResponse,
)

router = APIRouter()


def _envelope(data, message: str = "Operacion exitosa", **extra) -> dict:
    """Envelope estandar del backend: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _rol_nombre(usuario: Usuario) -> str:
    """Acceso seguro al nombre del rol (None-safe)."""
    return usuario.rol.nombre_rol if usuario.rol else ""


def _serializar_devolucion(d: Devolucion) -> dict:
    """Convierte la devolucion a dict listo para el envelope."""
    items = []
    for det in d.detalles:
        items.append(
            {
                "id_detalle": det.id_detalle,
                "detalle_venta_id": det.id_detalle_venta,
                "id_producto": det.id_producto,
                "nombre_producto": (
                    det.producto.nombre if det.producto else None
                ),
                "cantidad_devuelta": det.cantidad_devuelta,
                "precio_unitario": float(det.precio_unitario),
                "subtotal": float(det.subtotal),
            }
        )
    return {
        "id_devolucion": d.id_devolucion,
        "id_venta": d.id_venta,
        "codigo_venta": d.venta.codigo if d.venta else None,
        "estado": d.estado,
        "motivo": d.motivo,
        "motivo_rechazo": d.motivo_rechazo,
        "fecha_solicitud": d.fecha_solicitud,
        "fecha_procesado": d.fecha_procesado,
        "monto_total_devuelto": float(d.monto_total_devuelto),
        "cliente_id": str(d.id_cliente),
        "cliente_nombre": (
            f"{d.cliente.nombre} {d.cliente.apellido or ''}".strip()
            if d.cliente else None
        ),
        "solicitante_id": str(d.id_solicitante),
        "procesador_id": str(d.id_procesador) if d.id_procesador else None,
        "procesador_nombre": (
            f"{d.procesador.nombre} {d.procesador.apellido or ''}".strip()
            if d.procesador else None
        ),
        "items": items,
    }


def _buscar_devolucion(db: Session, id_devolucion: int) -> Devolucion:
    """404 consistente para endpoints con path param."""
    d = db.get(Devolucion, id_devolucion)
    if not d:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la devolucion con id {id_devolucion}.",
        )
    return d


def _validar_acceso_devolucion(d: Devolucion, usuario: Usuario) -> None:
    """403 si el usuario no tiene visibilidad sobre la devolucion.

    - C: solo si id_cliente == usuario.id_usuario.
    - V/GS/ASU: ven todas.
    """
    if _rol_nombre(usuario) == "C" and str(d.id_cliente) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=403,
            detail="No tiene acceso a esta devolucion.",
        )


# ---------------------------------------------------------------------------
# POST / - cliente solicita devolucion
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def solicitar_devolucion(
    payload: DevolucionCreatePayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU13: El cliente (rol C) solicita una devolucion sobre una venta.

    Validaciones:
    - Venta existe y pertenece al cliente del token (403 si no).
    - Venta en estado_pago='PAGADO' (422 si no).
    - Dentro de la ventana VENTANA_DEVOLUCION_HORAS desde fecha_venta (422).
    - Cada detalle_venta_id pertenece a la venta y la cantidad_devuelta
      no supera (cantidad_original - ya_devuelto_en_APROBADA|COMPLETADA).
    """
    rol = _rol_nombre(usuario)
    if rol not in ("C", "V", "GS", "ASU"):
        raise HTTPException(
            status_code=403,
            detail="Rol no autorizado para registrar devoluciones.",
        )

    venta = db.get(Venta, payload.id_venta)
    if not venta:
        raise HTTPException(
            status_code=422,
            detail=f"No existe la venta con id {payload.id_venta}.",
        )
    if rol == "C" and str(venta.id_cliente) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=403,
            detail="La venta no pertenece al usuario autenticado.",
        )
    if rol in ("GS", "V") and usuario.id_sucursal is not None:
        if venta.id_sucursal is not None and venta.id_sucursal != usuario.id_sucursal:
            raise HTTPException(
                status_code=403,
                detail="No puede registrar devoluciones para ventas de otra sucursal.",
            )
    if venta.estado_pago != "PAGADO":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Solo se pueden devolver ventas PAGADO. "
                f"Estado actual: {venta.estado_pago}."
            ),
        )

    # Ventana de tiempo: para clientes online aplica la ventana estándar de 24h
    ahora = datetime.now(timezone.utc)
    hace_n = ahora - timedelta(hours=VENTANA_DEVOLUCION_HORAS)
    if rol == "C" and venta.fecha_venta < hace_n:
        horas_transcurridas = (ahora - venta.fecha_venta).total_seconds() / 3600
        raise HTTPException(
            status_code=422,
            detail=(
                f"La venta supera la ventana de devolucion de "
                f"{VENTANA_DEVOLUCION_HORAS}h. "
                f"Han transcurrido {horas_transcurridas:.1f}h."
            ),
        )

    # Verificar que no exista ya una devolucion SOLICITADA/RECHAZADA para
    # la misma venta (un cliente no puede 'spammear' solicitudes duplicadas).
    abierta = (
        db.query(Devolucion)
        .filter(Devolucion.id_venta == venta.id_venta)
        .filter(Devolucion.estado.in_(("SOLICITADA", "APROBADA", "COMPLETADA")))
        .first()
    )
    if abierta:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ya existe una devolucion {abierta.estado} para la venta "
                f"{venta.codigo} (id_devolucion={abierta.id_devolucion})."
            ),
        )

    # Validar items: cada detalle_venta_id pertenece a la venta, y la
    # cantidad_devuelta no supera (cantidad_original - ya_devuelto).
    ids_detalle_venta = [i.detalle_venta_id for i in payload.items]
    lineas_venta = (
        db.query(DetalleVenta)
        .filter(DetalleVenta.id_detalle.in_(ids_detalle_venta))
        .all()
    )
    lineas_por_id = {l.id_detalle: l for l in lineas_venta}
    faltantes = [i for i in set(ids_detalle_venta) if i not in lineas_por_id]
    if faltantes:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No existen lineas de venta con id(s): "
                f"{', '.join(map(str, faltantes))}."
            ),
        )

    # Calcular lo ya devuelto por linea (sobre devoluciones APROBADAS o
    # COMPLETADAS). SOLICITADA cuenta como pendiente y la cap actual la
    # evaluamos aparte al aprobar (si la quieren tratar como concurrencia,
    # la proteccion de doble devolucion esta en el PATCH /procesar).
    ya_devuelto = dict(
        db.query(DetalleDevolucion.id_detalle_venta, func.coalesce(func.sum(DetalleDevolucion.cantidad_devuelta), 0))
        .join(Devolucion, Devolucion.id_devolucion == DetalleDevolucion.id_devolucion)
        .filter(DetalleDevolucion.id_detalle_venta.in_(ids_detalle_venta))
        .filter(Devolucion.estado.in_(("APROBADA", "COMPLETADA")))
        .group_by(DetalleDevolucion.id_detalle_venta)
        .all()
    )

    errores = []
    for item in payload.items:
        linea = lineas_por_id[item.detalle_venta_id]
        if linea.id_venta != venta.id_venta:
            errores.append(
                f"La linea {linea.id_detalle} no pertenece a la venta {venta.id_venta}."
            )
            continue
        disponible = linea.cantidad - int(ya_devuelto.get(item.detalle_venta_id, 0))
        if item.cantidad_devuelta > disponible:
            errores.append(
                f"Linea {linea.id_detalle} (producto {linea.id_producto}): "
                f"disponible para devolver {disponible}, solicitado {item.cantidad_devuelta}."
            )
    if errores:
        raise HTTPException(status_code=409, detail="; ".join(errores))

    # Crear la devolucion + sus lineas.
    devolucion = Devolucion(
        id_venta=venta.id_venta,
        id_cliente=venta.id_cliente,
        id_solicitante=usuario.id_usuario,
        estado="SOLICITADA",
        motivo=payload.motivo.strip(),
        monto_total_devuelto=0,  # se llena al COMPLETAR (reembolso real)
    )
    db.add(devolucion)
    db.flush()  # para tener id_devolucion

    monto_total = 0.0
    for item in payload.items:
        linea = lineas_por_id[item.detalle_venta_id]
        subtotal = round(float(linea.precio_unitario) * item.cantidad_devuelta, 2)
        monto_total += subtotal
        db.add(
            DetalleDevolucion(
                id_devolucion=devolucion.id_devolucion,
                id_detalle_venta=linea.id_detalle,
                id_producto=linea.id_producto,
                cantidad_devuelta=item.cantidad_devuelta,
                precio_unitario=linea.precio_unitario,
                subtotal=subtotal,
            )
        )

    db.commit()
    db.refresh(devolucion)

    return _envelope(
        _serializar_devolucion(devolucion),
        message="Devolucion solicitada correctamente.",
    )


# ---------------------------------------------------------------------------
# GET / - listado con aislamiento por rol y filtros
# ---------------------------------------------------------------------------
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_devoluciones(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    estado: Optional[str] = Query(
        default=None,
        description="SOLICITADA | APROBADA | RECHAZADA | COMPLETADA",
    ),
    id_venta: Optional[int] = Query(default=None, ge=1),
    q: Optional[str] = Query(default=None, description="Búsqueda por código de venta, cliente o motivo"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU13: Listado paginado con visibilidad por rol.

    - C: solo SUS devoluciones.
    - GS/V: devoluciones de su sucursal (o todas si no tienen sucursal asignada).
    - ASU: todas.
    """
    if estado and estado not in ("SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Estado invalido '{estado}'. Valores permitidos: "
                "SOLICITADA, APROBADA, RECHAZADA, COMPLETADA."
            ),
        )

    query = db.query(Devolucion)
    rol = _rol_nombre(usuario)

    if rol == "C":
        query = query.filter(Devolucion.id_cliente == usuario.id_usuario)
    elif rol in ("GS", "V") and getattr(usuario, "id_sucursal", None) is not None:
        query = query.join(Venta, Devolucion.id_venta == Venta.id_venta).filter(
            or_(Venta.id_sucursal == usuario.id_sucursal, Venta.id_sucursal.is_(None))
        )

    if q and q.strip():
        term = f"%{q.strip()}%"
        # Si no se hizo join previamente con Venta
        if not (rol in ("GS", "V") and getattr(usuario, "id_sucursal", None) is not None):
            query = query.join(Venta, Devolucion.id_venta == Venta.id_venta)
        query = query.filter(
            or_(
                Venta.codigo.ilike(term),
                Venta.nombre_cliente.ilike(term),
                Devolucion.motivo.ilike(term),
            )
        )

    if estado:
        query = query.filter(Devolucion.estado == estado)
    if id_venta:
        query = query.filter(Devolucion.id_venta == id_venta)

    total = query.count()
    items = (
        query.order_by(Devolucion.fecha_solicitud.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_devolucion(d) for d in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


# ---------------------------------------------------------------------------
# GET /{id} - detalle
# ---------------------------------------------------------------------------
@router.get("/{id_devolucion}", response_model=None)
def obtener_devolucion(
    id_devolucion: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU13: Detalle de una devolucion con sus lineas."""
    devolucion = _buscar_devolucion(db, id_devolucion)
    _validar_acceso_devolucion(devolucion, usuario)
    return _envelope(_serializar_devolucion(devolucion))


# ---------------------------------------------------------------------------
# GET /venta/{id_venta}/elegibles - helper para la UI del cliente
# ---------------------------------------------------------------------------
@router.get("/venta/{id_venta}/elegibles", response_model=None)
def items_elegibles_para_devolucion(
    id_venta: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU13: Devuelve las lineas de la venta con la cantidad DISPONIBLE
    para devolver (original - ya devuelto en APROBADA|COMPLETADA).

    Util para que el front pinte el formulario de devolucion con los
    maximos que el cliente puede pedir sin que el backend le tire 409
    al enviar la solicitud.
    """
    venta = db.get(Venta, id_venta)
    if not venta:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la venta con id {id_venta}.",
        )
    if _rol_nombre(usuario) == "C" and str(venta.id_cliente) != str(usuario.id_usuario):
        raise HTTPException(status_code=403, detail="La venta no le pertenece.")
    if venta.estado_pago != "PAGADO":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Solo se pueden devolver ventas PAGADO. Estado actual: {venta.estado_pago}."
            ),
        )

    ahora = datetime.now(timezone.utc)
    hace_n = ahora - timedelta(hours=VENTANA_DEVOLUCION_HORAS)
    en_ventana = venta.fecha_venta >= hace_n

    ya_devuelto_q = (
        db.query(
            DetalleDevolucion.id_detalle_venta,
            func.coalesce(func.sum(DetalleDevolucion.cantidad_devuelta), 0).label("cant"),
        )
        .join(Devolucion, Devolucion.id_devolucion == DetalleDevolucion.id_devolucion)
        .filter(Devolucion.id_venta == venta.id_venta)
        .filter(Devolucion.estado.in_(("APROBADA", "COMPLETADA")))
        .group_by(DetalleDevolucion.id_detalle_venta)
        .all()
    )
    ya_devuelto = {row.id_detalle_venta: int(row.cant) for row in ya_devuelto_q}

    items = []
    for d in venta.detalles:
        disp = d.cantidad - ya_devuelto.get(d.id_detalle, 0)
        items.append(
            {
                "detalle_venta_id": d.id_detalle,
                "id_producto": d.id_producto,
                "nombre": d.producto.nombre if d.producto else f"Producto {d.id_producto}",
                "talla": d.talla,
                "color": d.color,
                "cantidad_original": d.cantidad,
                "ya_devuelto": ya_devuelto.get(d.id_detalle, 0),
                "disponible_para_devolver": max(disp, 0),
                "precio_unitario": float(d.precio_unitario),
            }
        )

    return _envelope(
        {
            "id_venta": venta.id_venta,
            "codigo": venta.codigo,
            "fecha_venta": venta.fecha_venta,
            "en_ventana": en_ventana,
            "items": items,
        }
    )


# ---------------------------------------------------------------------------
# PATCH /{id}/procesar - aprobacion / rechazo / completado
# ---------------------------------------------------------------------------
@router.patch("/{id_devolucion}/procesar", response_model=None)
def procesar_devolucion(
    id_devolucion: int,
    payload: DevolucionProcesarPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU13: V/GS/ASU procesan una devolucion SOLICITADA.

    Acciones validas (idempotencia de transicion: si el estado no es el
    esperado, 409):
    - APROBAR: SOLICITADA -> APROBADA. No mueve stock todavia.
    - RECHAZAR: SOLICITADA -> RECHAZADA. motivo_rechazo obligatorio.
    - COMPLETAR: APROBADA -> COMPLETADA. En la MISMA transaccion:
        * Bloquea productos con FOR UPDATE.
        * Inserta MovimientoInventario tipo=ENTRADA por linea (kardex).
        * Incrementa productos.stock_total.
        * Setea monto_total_devuelto = SUM(linea.subtotal).
    """
    if _rol_nombre(usuario) not in ROLES_PROCESAMIENTO:
        raise HTTPException(
            status_code=403,
            detail=(
                "Solo Vendedor, Gerente de Sucursal o Administrador pueden "
                "procesar devoluciones."
            ),
        )

    devolucion = _buscar_devolucion(db, id_devolucion)

    ahora = datetime.now(timezone.utc)

    if payload.accion == "APROBAR":
        if devolucion.estado != "SOLICITADA":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Solo se puede APROBAR una devolucion SOLICITADA. "
                    f"Estado actual: {devolucion.estado}."
                ),
            )
        devolucion.estado = "APROBADA"
        devolucion.id_procesador = usuario.id_usuario
        devolucion.fecha_procesado = ahora
        mensaje = "Devolucion aprobada. Pendiente de recepcion para completar."

    elif payload.accion == "RECHAZAR":
        if devolucion.estado != "SOLICITADA":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Solo se puede RECHAZAR una devolucion SOLICITADA. "
                    f"Estado actual: {devolucion.estado}."
                ),
            )
        devolucion.estado = "RECHAZADA"
        devolucion.id_procesador = usuario.id_usuario
        devolucion.fecha_procesado = ahora
        devolucion.motivo_rechazo = payload.motivo_rechazo.strip()
        mensaje = "Devolucion rechazada."

    elif payload.accion == "COMPLETAR":
        if devolucion.estado not in ("APROBADA", "SOLICITADA"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Solo se puede COMPLETAR una devolucion APROBADA o SOLICITADA. "
                    f"Estado actual: {devolucion.estado}."
                ),
            )

        # Bloquear TODOS los productos involucrados (FOR UPDATE OF productos)
        ids_producto = list({det.id_producto for det in devolucion.detalles})
        productos_bloq = (
            db.query(Producto)
            .filter(Producto.id_producto.in_(ids_producto))
            .with_for_update(of=Producto)
            .all()
        )
        productos_por_id = {p.id_producto: p for p in productos_bloq}

        motivo = (
            f"Devolucion #{devolucion.id_devolucion} de venta "
            f"{devolucion.venta.codigo if devolucion.venta else devolucion.id_venta}"
        )

        sucursal_id = devolucion.venta.id_sucursal if devolucion.venta else getattr(usuario, "id_sucursal", None)
        if not sucursal_id and getattr(usuario, "id_sucursal", None):
            sucursal_id = usuario.id_sucursal

        monto_total = 0.0
        for det in devolucion.detalles:
            producto = productos_por_id.get(det.id_producto)
            if not producto:
                # Caso raro: FK esta pero la fila no deberia faltar nunca;
                # si pasa, 422 para que se investigue en vez de fallar silencioso.
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"No existe el producto con id {det.id_producto} "
                        f"para la linea {det.id_detalle}."
                    ),
                )
            stock_anterior = producto.stock_total
            producto.stock_total = stock_anterior + det.cantidad_devuelta
            if producto.estado == "Agotado" and producto.stock_total > 0:
                producto.estado = "Activo"  # reingreso automatico

            if sucursal_id:
                inv_suc = (
                    db.query(InventarioSucursal)
                    .filter(
                        InventarioSucursal.id_producto == producto.id_producto,
                        InventarioSucursal.id_sucursal == sucursal_id,
                    )
                    .first()
                )
                if inv_suc:
                    inv_suc.stock += det.cantidad_devuelta
                else:
                    db.add(
                        InventarioSucursal(
                            id_producto=producto.id_producto,
                            id_sucursal=sucursal_id,
                            stock=det.cantidad_devuelta,
                        )
                    )

            db.add(
                MovimientoInventario(
                    id_producto=producto.id_producto,
                    tipo="ENTRADA",
                    cantidad=det.cantidad_devuelta,
                    stock_anterior=stock_anterior,
                    stock_nuevo=producto.stock_total,
                    motivo=motivo,
                    id_usuario=usuario.id_usuario,
                    id_sucursal=sucursal_id,
                )
            )
            monto_total += float(det.subtotal)

        devolucion.estado = "COMPLETADA"
        devolucion.id_procesador = usuario.id_usuario
        devolucion.fecha_procesado = ahora
        devolucion.monto_total_devuelto = round(monto_total, 2)
        mensaje = (
            f"Devolucion completada. Reintegro de stock aplicado "
            f"({len(devolucion.detalles)} linea(s))."
        )

    db.commit()
    db.refresh(devolucion)

    return _envelope(_serializar_devolucion(devolucion), message=mensaje)
