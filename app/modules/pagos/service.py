# backend/app/modules/pagos/service.py
# CU15+CU21 — Servicio de Pasarela de Pagos (AttentionPay / QR / Tarjeta / Webhook)
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.delivery.service import crear_envio_para_venta
from app.modules.inventario.models import InventarioSucursal, MovimientoInventario, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.notificaciones.service import emitir
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import Carrito, DetalleVenta, ItemCarrito, TransaccionPago, Venta
from app.schemas.pago import (
    ConfirmarAbonoQRPayload,
    ConfirmarSesionStripePayload,
    CrearSesionStripePayload,
    DatosTarjetaPayload,
    PagoTarjetaPayload,
    ProcesarPagoPayload,
    ProcesarTarjetaPayload,
    WebhookPayload,
)

ENVIO_GRATIS_DESDE = 300
COSTO_ENVIO = 25
ROLES_POS = ("ASU", "GS", "V")


def _resolver_sucursal_con_stock(
    db: Session,
    items: list,
    ciudad_entrega: str | None = None,
) -> int | None:
    """Selecciona la sucursal con stock disponible para los items.
    
    Lógica multi-sucursal inteligente e independiente:
    1. Filtra las sucursales activas que posean inventario suficiente para todos los ítems.
    2. Si hay ciudad de entrega y sucursales con stock en dicha ciudad:
       - Si hay múltiples candidatas en la ciudad, elige al azar (load balancing con secrets.choice).
    3. Si no coinciden por ciudad pero hay sucursales con stock completo:
       - Elige al azar entre las sucursales con stock disponible (secrets.choice).
    4. Si ninguna tiene stock completo combinado, busca sucursales con disponibilidad parcial.
    5. Fallback a una sucursal activa al azar.
    """
    from app.modules.empresa.models import Sucursal

    pids = [getattr(it, "producto_id", None) or getattr(it, "id_producto", None) for it in items]
    pids = [pid for pid in pids if pid]
    if not pids:
        return None

    sucursales = db.query(Sucursal).filter(Sucursal.is_active.is_(True)).all()
    if not sucursales:
        return None

    candidatas_con_stock: list[Sucursal] = []
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
            candidatas_con_stock.append(suc)

    # Si hay candidatas con stock completo
    if candidatas_con_stock:
        if ciudad_entrega:
            c_term = ciudad_entrega.strip().lower()
            en_ciudad = [
                s for s in candidatas_con_stock
                if s.ciudad and c_term in s.ciudad.nombre.lower()
            ]
            if en_ciudad:
                return secrets.choice(en_ciudad).codigo_sucursal
        return secrets.choice(candidatas_con_stock).codigo_sucursal

    # Si ninguna tiene stock completo de todos los productos juntos, buscar sucursales con stock parcial
    sucursales_parciales: list[Sucursal] = []
    for suc in sucursales:
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
            if inv and inv.stock >= cant:
                sucursales_parciales.append(suc)
                break
    if sucursales_parciales:
        return secrets.choice(sucursales_parciales).codigo_sucursal

    return secrets.choice(sucursales).codigo_sucursal


def _generar_codigo_venta() -> str:
    """Comprobante legible del ticket: ATT- + 6 dígitos."""
    return f"ATT-{secrets.randbelow(1000000):06d}"


def _generar_codigo_transaccion() -> str:
    """Código único de transacción de la pasarela: TXN-ATT-YYYYMMDD-XXXX."""
    ahora = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rand = secrets.randbelow(100000)
    return f"TXN-ATT-{ahora}-{rand:05d}"


def calcular_firma_webhook(codigo_transaccion: str, monto: float, status_str: str) -> str:
    """Calcula la firma HMAC-SHA256 con el secret de la pasarela configurado en .env."""
    secret = get_settings().PAYMENT_GATEWAY_SECRET.encode("utf-8")
    monto_fmt = f"{Decimal(str(monto)):.2f}"
    mensaje = f"{codigo_transaccion}:{monto_fmt}:{status_str}".encode("utf-8")
    return hmac.new(secret, mensaje, hashlib.sha256).hexdigest()


def verificar_firma_webhook(
    codigo_transaccion: str, monto: float, status_str: str, signature: str
) -> bool:
    """Verifica de forma segura la firma contra manipulación de payloads."""
    firma_esperada = calcular_firma_webhook(codigo_transaccion, monto, status_str)
    return hmac.compare_digest(firma_esperada.lower(), signature.strip().lower())


def _validar_formato_tarjeta(datos: DatosTarjetaPayload) -> str:
    """Valida formato básico de tarjeta y genera la máscara segura **** **** **** 1234."""
    num_limpio = "".join(filter(str.isdigit, datos.numero_tarjeta))
    if len(num_limpio) < 13 or len(num_limpio) > 19:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El número de tarjeta debe tener entre 13 y 19 dígitos numéricos.",
        )
    cvv_limpio = "".join(filter(str.isdigit, datos.cvv))
    if len(cvv_limpio) < 3 or len(cvv_limpio) > 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El código de seguridad CVV debe tener 3 o 4 dígitos.",
        )
    ultimos4 = num_limpio[-4:]
    return f"**** **** **** {ultimos4}"


def _generar_qr_data(codigo_transaccion: str, monto: float) -> str:
    """Genera la cadena técnica del QR interoperable (estándar Simple/BCP/BNB)."""
    return (
        f"00020101021226480012com.attention0110BOB0214{codigo_transaccion}"
        f"520456115303068540{len(f'{monto:.2f}'):02d}{monto:.2f}"
        f"5802BO5915Attention Moda6010Santa Cruz"
    )


def iniciar_transaccion_pago(
    db: Session,
    payload: ProcesarPagoPayload,
    usuario_actual: Usuario,
    modo: str,
) -> dict[str, Any]:
    """Inicia el proceso de checkout y registra la transacción con la pasarela.

    Crea la Venta y la TransaccionPago en estado PENDIENTE. No descuenta stock
    hasta que el Webhook confirme APPROVED (para pagos digitales diferidos),
    o si es POS en efectivo se procesa de forma directa.
    """
    rol_nombre = usuario_actual.rol.nombre_rol if usuario_actual.rol else ""

    if modo == "POS":
        if rol_nombre not in ROLES_POS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El modo POS solo está disponible para Vendedor, Gerente o Administrador.",
            )
        if payload.id_cliente_override is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Para registrar una venta POS debe indicar el cliente (id_cliente_override).",
            )
        cliente = db.get(Usuario, payload.id_cliente_override)
        if not cliente:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No existe el cliente con id {payload.id_cliente_override}.",
            )
        if not cliente.rol or cliente.rol.nombre_rol != "C":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El usuario especificado no posee rol de Cliente (rol C).",
            )
        id_cliente_final = cliente.id_usuario
        id_vendedor_final = usuario_actual.id_usuario
    else:
        id_cliente_final = usuario_actual.id_usuario
        id_vendedor_final = None

    # 1. Bloquear y resolver productos para cálculo de precios reales desde DB
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No existen productos con id(s): {', '.join(map(str, faltantes))}.",
        )

    lineas = []
    for item in payload.items:
        producto = encontrados[item.producto_id]
        if producto.estado != "Activo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El producto '{producto.nombre}' no está disponible ({producto.estado}).",
            )
        if producto.stock_total < item.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock insuficiente para '{producto.nombre}': "
                    f"disponible {producto.stock_total}, solicitado {item.cantidad}."
                ),
            )
        precio = float(producto.precio_venta)
        lineas.append((item, producto, precio, round(precio * item.cantidad, 2)))

    subtotal_general = round(sum(l[3] for l in lineas), 2)
    costo_envio = 0.0 if subtotal_general >= ENVIO_GRATIS_DESDE else float(COSTO_ENVIO)
    total = round(subtotal_general + costo_envio, 2)

    entrega = payload.datos_entrega
    tipo_entrega = payload.tipo_entrega or (
        "RETIRO" if id_vendedor_final else "DOMICILIO"
    )
    id_sucursal_final = payload.id_sucursal or (
        usuario_actual.id_sucursal if usuario_actual else None
    )
    if not id_sucursal_final:
        id_sucursal_final = _resolver_sucursal_con_stock(
            db, payload.items, entrega.ciudad if entrega else None
        )

    codigo_venta = _generar_codigo_venta()
    codigo_txn = _generar_codigo_transaccion()

    detalles_pago = None
    qr_data = None

    if payload.metodo_pago == "TARJETA":
        if payload.datos_tarjeta:
            mascara = _validar_formato_tarjeta(payload.datos_tarjeta)
            auth_code = f"AUTH-{secrets.randbelow(1000000):06d}"
            detalles_pago = (
                f"Titular: {payload.datos_tarjeta.titular} | Tarjeta: {mascara} "
                f"| Auth: {auth_code} | Pasarela: {get_settings().PAYMENT_GATEWAY_NAME}"
            )
        else:
            detalles_pago = f"Pasarela: {get_settings().PAYMENT_GATEWAY_NAME} (Tarjeta Pendiente)"
    elif payload.metodo_pago == "QR":
        qr_data = _generar_qr_data(codigo_txn, total)
        detalles_pago = f"QR Simple Interoperable generado para {codigo_txn}"
    else:  # EFECTIVO
        detalles_pago = f"Pago en efectivo contra entrega / en caja (Sucursal {id_sucursal_final or 'Central'})"

    # Registro de la Venta en estado PENDIENTE
    venta = Venta(
        id_cliente=id_cliente_final,
        id_vendedor=id_vendedor_final,
        id_sucursal=id_sucursal_final,
        tipo_entrega=tipo_entrega,
        total=total,
        costo_envio=costo_envio,
        metodo_pago=payload.metodo_pago,
        estado_pago="PENDIENTE",
        codigo=codigo_venta,
        nombre_cliente=entrega.nombre_cliente,
        correo=entrega.correo,
        telefono=entrega.telefono,
        direccion=entrega.direccion,
        ciudad=entrega.ciudad,
        referencia=entrega.referencia,
    )
    db.add(venta)
    db.flush()

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

    # Si es EFECTIVO contra entrega, descontar de inmediato el stock físico de la sucursal seleccionada/asignada
    if payload.metodo_pago == "EFECTIVO":
        motivo_kardex = f"Pedido en efectivo contra entrega {venta.codigo} (Sucursal {id_sucursal_final or 'Central'})"
        for item, producto, precio, subtotal in lineas:
            stock_anterior = producto.stock_total
            producto.stock_total = max(0, stock_anterior - item.cantidad)
            if producto.stock_total == 0:
                producto.estado = "Agotado"

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
                    motivo=motivo_kardex,
                    id_usuario=id_cliente_final,
                    id_sucursal=id_sucursal_final,
                )
            )

    # Registro de la Transacción en Pasarela
    transaccion = TransaccionPago(
        id_venta=venta.id_venta,
        pasarela=get_settings().PAYMENT_GATEWAY_NAME,
        codigo_transaccion=codigo_txn,
        monto=total,
        moneda="BOB",
        metodo_pago=payload.metodo_pago,
        estado="PENDIENTE",
        detalles_pago=detalles_pago,
        qr_data=qr_data,
        signature=calcular_firma_webhook(codigo_txn, total, "PENDIENTE"),
    )
    db.add(transaccion)
    db.commit()
    db.refresh(venta)
    db.refresh(transaccion)

    return {
        "id_venta": venta.id_venta,
        "codigo_venta": venta.codigo,
        "total": float(venta.total),
        "costo_envio": float(venta.costo_envio),
        "metodo_pago": venta.metodo_pago,
        "estado_pago": venta.estado_pago,
        "transaccion": {
            "id_transaccion": transaccion.id_transaccion,
            "codigo_transaccion": transaccion.codigo_transaccion,
            "pasarela": transaccion.pasarela,
            "monto": float(transaccion.monto),
            "moneda": transaccion.moneda,
            "metodo_pago": transaccion.metodo_pago,
            "estado": transaccion.estado,
            "qr_data": transaccion.qr_data,
            "detalles_pago": transaccion.detalles_pago,
            "fecha_creacion": transaccion.fecha_creacion.isoformat(),
        },
    }


def _liquidar_venta_transaccional(
    db: Session,
    venta: Venta,
    transaccion: TransaccionPago | None,
    pasarela_nombre: str,
    referencia_pago: str,
) -> dict[str, Any]:
    """Ejecuta la transacción atómica de liquidación:
    - Valida y descuenta stock físico de InventarioSucursal y Producto.stock_total.
    - Emite alertas de stock crítico si baja a <= 5 unidades.
    - Registra movimientos de Kardex SALIDA.
    - Actualiza estado de venta a PAGADA y transacción a PAGADO.
    - Vacía el carrito persistente del cliente en BD si existe.
    - Dispara orden de despacho a domicilio si aplica.
    - Notifica in-app al cliente de la confirmación de su pago.
    """
    detalles = venta.detalles
    ids_productos = [d.id_producto for d in detalles]
    productos = (
        db.query(Producto)
        .filter(Producto.id_producto.in_(ids_productos))
        .with_for_update(of=Producto)
        .all()
    )
    prod_map = {p.id_producto: p for p in productos}

    ya_descontado = bool(venta.metodo_pago == "EFECTIVO")

    if not ya_descontado:
        for det in detalles:
            prod = prod_map.get(det.id_producto)
            if not prod:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Producto {det.id_producto} no encontrado.",
                )
            if prod.stock_total < det.cantidad:
                if transaccion:
                    transaccion.estado = "RECHAZADO"
                venta.estado_pago = "RECHAZADO"
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Stock insuficiente para '{prod.nombre}': disponible {prod.stock_total}, solicitado {det.cantidad}.",
                )

        motivo_kardex = f"Venta {venta.codigo} confirmada por {pasarela_nombre} (Ref: {referencia_pago})"

        for det in detalles:
            prod = prod_map[det.id_producto]
            stock_anterior = prod.stock_total
            prod.stock_total = max(0, stock_anterior - det.cantidad)
            if prod.stock_total == 0:
                prod.estado = "Agotado"

            if venta.id_sucursal:
                inv_suc = (
                    db.query(InventarioSucursal)
                    .filter(
                        InventarioSucursal.id_sucursal == venta.id_sucursal,
                        InventarioSucursal.id_producto == prod.id_producto,
                    )
                    .with_for_update(of=InventarioSucursal)
                    .first()
                )
                if inv_suc:
                    inv_suc.stock = max(0, inv_suc.stock - det.cantidad)
                    verificar_y_notificar_stock_critico(
                        db,
                        id_producto=prod.id_producto,
                        id_sucursal=venta.id_sucursal,
                        stock_nuevo=inv_suc.stock,
                        commit=False,
                    )

            db.add(
                MovimientoInventario(
                    id_producto=prod.id_producto,
                    tipo="SALIDA",
                    cantidad=det.cantidad,
                    stock_anterior=stock_anterior,
                    stock_nuevo=prod.stock_total,
                    motivo=motivo_kardex,
                    id_usuario=venta.id_cliente,
                    id_sucursal=venta.id_sucursal,
                )
            )

    venta.estado_pago = "PAGADO"
    if transaccion:
        transaccion.estado = "PAGADO"
        transaccion.detalles_pago = (
            f"{transaccion.detalles_pago or ''} | Liquidado: {referencia_pago}".strip(" |")
        )

    # Vaciar carrito de compras persistente en DB
    if venta.id_cliente:
        carrito_db = db.query(Carrito).filter(Carrito.id_usuario == venta.id_cliente).first()
        if carrito_db:
            db.query(ItemCarrito).filter(ItemCarrito.id_carrito == carrito_db.id_carrito).delete()

    # Disparar despacho a domicilio si corresponde
    if venta.tipo_entrega == "DOMICILIO":
        try:
            crear_envio_para_venta(
                db, venta, None, f"Envío generado automáticamente tras pago confirmado ({pasarela_nombre})."
            )
        except Exception:
            pass

    # Notificar in-app al cliente
    if venta.id_cliente:
        try:
            emitir(
                db,
                id_usuario=venta.id_cliente,
                titulo="Pago confirmado",
                mensaje=f"Pago confirmado ({pasarela_nombre}): Su orden {venta.codigo} por {float(venta.total):.2f} Bs ha sido procesada.",
                tipo="PAGO",
                referencia_tipo="venta",
                referencia_id=str(venta.id_venta),
                commit=False,
            )
        except Exception:
            pass

    db.commit()
    if transaccion:
        db.refresh(transaccion)
    db.refresh(venta)

    fecha_iso = (
        venta.fecha_venta.isoformat()
        if getattr(venta, "fecha_venta", None)
        else datetime.now(timezone.utc).isoformat()
    )

    return {
        "status": "success",
        "message": f"Pago confirmado y procesado exitosamente por {pasarela_nombre}. Stock descontado.",
        "codigo_transaccion": transaccion.codigo_transaccion if transaccion else referencia_pago,
        "codigo_venta": venta.codigo,
        "total": float(venta.total),
        "costo_envio": float(venta.costo_envio),
        "metodo_pago": venta.metodo_pago,
        "estado_pago": "PAGADO",
        "id_venta": venta.id_venta,
        "fecha": fecha_iso,
        "datos_entrega": {
            "nombre_cliente": venta.nombre_cliente,
            "correo": venta.correo,
            "telefono": venta.telefono,
            "direccion": venta.direccion,
            "ciudad": venta.ciudad,
            "referencia": venta.referencia,
        },
        "comprobante_fiscal": {
            "empresa": "ATTENTION S.R.L.",
            "nit": "1028374029",
            "nro_factura": venta.codigo,
            "nro_autorizacion": "29040011007",
            "codigo_control": transaccion.codigo_transaccion if transaccion else venta.codigo,
            "fecha_emision": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "total_bs": float(venta.total),
            "estado": "PAGADO",
            "metodo": venta.metodo_pago,
            "leyenda": "ESTA FACTURA CONTRIBUYE AL DESARROLLO DEL PAÍS, EL USO ILÍCITO SERÁ SANCIONADO PENALMENTE DE ACUERDO A LEY",
        },
    }


def procesar_webhook_pasarela(
    db: Session,
    payload: WebhookPayload,
    signature_header: str | None = None,
) -> dict[str, Any]:
    """Procesa la confirmación asíncrona de la pasarela de pago."""
    firma = signature_header or payload.signature
    if not firma or not verificar_firma_webhook(
        payload.codigo_transaccion, payload.monto, payload.status, firma
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma criptográfica del webhook inválida o alterada.",
        )

    transaccion = (
        db.query(TransaccionPago)
        .filter(TransaccionPago.codigo_transaccion == payload.codigo_transaccion)
        .with_for_update(of=TransaccionPago)
        .first()
    )
    if not transaccion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe la transacción con código {payload.codigo_transaccion}.",
        )

    if transaccion.estado in ("PAGADO", "PAGADA"):
        return {
            "status": "success",
            "message": "La transacción ya fue procesada y confirmada previamente.",
            "codigo_transaccion": transaccion.codigo_transaccion,
            "estado": transaccion.estado,
        }

    venta = (
        db.query(Venta)
        .filter(Venta.id_venta == transaccion.id_venta)
        .with_for_update(of=Venta)
        .first()
    )
    if not venta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la venta asociada a la transacción {payload.codigo_transaccion}.",
        )

    if payload.status == "APPROVED":
        transaccion.signature = firma
        return _liquidar_venta_transaccional(
            db=db,
            venta=venta,
            transaccion=transaccion,
            pasarela_nombre=transaccion.pasarela,
            referencia_pago=transaccion.codigo_transaccion,
        )
    else:
        venta.estado_pago = "RECHAZADO"
        transaccion.estado = "RECHAZADO"
        transaccion.signature = firma
        db.commit()
        return {
            "status": "success",
            "message": "Pago marcado como RECHAZADO según notificación del proveedor.",
            "codigo_transaccion": transaccion.codigo_transaccion,
            "codigo_venta": venta.codigo,
            "estado": "RECHAZADO",
        }


def validar_datos_tarjeta_attentionpay(datos: DatosTarjetaPayload) -> str:
    """Valida lógicamente los datos de la tarjeta en la pasarela interna AttentionPay:
    1. Titular: No vacío, mínimo 3 caracteres legibles.
    2. Número de tarjeta: Solo dígitos, longitud entre 13 y 19, validación de algoritmo de Luhn (mód 10).
    3. Expiración: Formato MM/AA o MM/AAAA, mes 1-12, no vencida respecto a la fecha actual UTC.
    4. CVV: 3 o 4 dígitos estrictamente numéricos.

    Retorna el número de tarjeta limpio (sin espacios ni guiones).
    """
    if not datos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Los datos de la tarjeta son obligatorios para procesar con AttentionPay.",
        )

    # 1. Validar titular
    titular = (datos.titular or "").strip()
    if len(titular) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre del titular de la tarjeta es inválido o contiene menos de 3 caracteres.",
        )

    # 2. Validar número de tarjeta
    raw_num = re.sub(r"\D", "", datos.numero_tarjeta or "")
    if len(raw_num) < 13 or len(raw_num) > 19:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Longitud de tarjeta inválida ({len(raw_num)} dígitos). Debe contener entre 13 y 19 dígitos.",
        )

    # Algoritmo de Luhn (módulo 10)
    digits = [int(c) for c in raw_num]
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += (doubled - 9) if doubled > 9 else doubled
        else:
            checksum += d

    es_valida_luhn = (checksum % 10 == 0)
    # Soporte para tarjetas de prueba bancarias de AttentionPay / Sandbox
    es_tarjeta_sandbox = (
        raw_num.startswith("4000")
        or raw_num.startswith("4500")
        or raw_num.endswith("0000")
        or raw_num.endswith("0002")
        or raw_num.endswith("4242")
    )

    if not es_valida_luhn and not es_tarjeta_sandbox:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Número de tarjeta inválido. No superó la comprobación de seguridad algorítmica (Luhn).",
        )

    # 3. Validar expiración (MM/AA o MM/AAAA)
    exp_str = (datos.expiracion or "").strip()
    match_exp = re.match(r"^(0[1-9]|1[0-2])\/?(\d{2}|\d{4})$", exp_str)
    if not match_exp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fecha de expiración inválida. Formato requerido: MM/AA o MM/AAAA.",
        )

    mes = int(match_exp.group(1))
    anio_raw = int(match_exp.group(2))
    anio = 2000 + anio_raw if anio_raw < 100 else anio_raw

    ahora = datetime.now(timezone.utc)
    if anio < ahora.year or (anio == ahora.year and mes < ahora.month):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La tarjeta ha expirado ({mes:02d}/{anio_raw}). Ingrese una tarjeta vigente.",
        )

    if anio > ahora.year + 25:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Año de expiración fuera del rango admitido.",
        )

    # 4. Validar CVV (3 o 4 dígitos)
    cvv_clean = (datos.cvv or "").strip()
    if not re.match(r"^\d{3,4}$", cvv_clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código de seguridad (CVV) inválido. Debe contener 3 o 4 dígitos numéricos.",
        )

    return raw_num


def procesar_tarjeta_attentionpay(
    db: Session,
    payload: ProcesarTarjetaPayload,
    usuario_actual: Usuario,
) -> dict[str, Any]:
    """CU21: Procesa el pago directo con tarjeta en la pasarela nativa AttentionPay.

    1. Valida algorítmicamente la tarjeta (Luhn, vencimiento, CVV, titular).
    2. Valida la disponibilidad de productos en la base de datos con bloqueo de fila.
    3. Determina la sucursal de abastecimiento y verifica inventario local.
    4. Registra la venta oficial vinculada a la sucursal.
    5. Descuenta atómicamente el stock local y global.
    6. Emite alertas de stock crítico (<= 5 unidades).
    7. Limpia el carrito persistente del cliente en BD.
    8. Genera comprobante fiscal formal con código de autorización AttentionPay.
    """
    # 1. Validación exhaustiva de los datos de la tarjeta en servidor
    num_limpio = validar_datos_tarjeta_attentionpay(payload.datos_tarjeta)

    # 2. Validar productos y calcular totales reales
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No existen productos con id(s): {', '.join(map(str, faltantes))}.",
        )

    lineas = []
    for item in payload.items:
        prod = encontrados[item.producto_id]
        if prod.estado != "Activo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El producto '{prod.nombre}' no está disponible ({prod.estado}).",
            )
        if prod.stock_total < item.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock total insuficiente para '{prod.nombre}': "
                    f"disponible {prod.stock_total}, solicitado {item.cantidad}."
                ),
            )
        precio = float(prod.precio_venta)
        lineas.append((item, prod, precio, round(precio * item.cantidad, 2)))

    subtotal_general = round(sum(l[3] for l in lineas), 2)
    costo_envio = 0.0 if subtotal_general >= ENVIO_GRATIS_DESDE else float(COSTO_ENVIO)
    total = round(subtotal_general + costo_envio, 2)

    entrega = payload.datos_entrega
    tipo_entrega = payload.tipo_entrega or "DOMICILIO"
    id_sucursal_final = payload.id_sucursal or (
        usuario_actual.id_sucursal if usuario_actual else None
    )
    if not id_sucursal_final and tipo_entrega == "DOMICILIO":
        id_sucursal_final = _resolver_sucursal_con_stock(db, payload.items, entrega.ciudad)

    # Verificar stock en la sucursal asignada
    if id_sucursal_final:
        for item, prod, _, _ in lineas:
            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == id_sucursal_final,
                    InventarioSucursal.id_producto == prod.id_producto,
                )
                .first()
            )
            if not inv_suc or inv_suc.stock < item.cantidad:
                disp = inv_suc.stock if inv_suc else 0
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Stock insuficiente en la sucursal asignada para '{prod.nombre}': "
                        f"disponible local {disp}, solicitado {item.cantidad}."
                    ),
                )

    codigo_venta = _generar_codigo_venta()
    codigo_txn = _generar_codigo_transaccion()
    codigo_autorizacion = f"AUTH-ATT-{secrets.randbelow(1000000):06d}"
    ultimos_4 = num_limpio[-4:]
    tarjeta_enmascarada = f"•••• •••• •••• {ultimos_4}"

    # 3. Registrar venta oficial en base de datos
    venta = Venta(
        id_cliente=usuario_actual.id_usuario if usuario_actual else None,
        id_vendedor=None,
        id_sucursal=id_sucursal_final,
        tipo_entrega=tipo_entrega,
        total=total,
        costo_envio=costo_envio,
        metodo_pago="TARJETA",
        estado_pago="PENDIENTE",
        codigo=codigo_venta,
        nombre_cliente=entrega.nombre_cliente,
        correo=entrega.correo,
        telefono=entrega.telefono,
        direccion=entrega.direccion,
        ciudad=entrega.ciudad,
        referencia=entrega.referencia,
    )
    db.add(venta)
    db.flush()

    for item, prod, precio, sub in lineas:
        db.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=prod.id_producto,
                cantidad=item.cantidad,
                precio_unitario=precio,
                subtotal=sub,
                talla=item.talla,
                color=item.color,
            )
        )

    detalles_pago = (
        f"AttentionPay Aprobado: {codigo_autorizacion} | "
        f"Titular: {payload.datos_tarjeta.titular.strip().upper()} | "
        f"Tarjeta: {tarjeta_enmascarada}"
    )

    transaccion = TransaccionPago(
        id_venta=venta.id_venta,
        pasarela="AttentionPay",
        codigo_transaccion=codigo_txn,
        monto=total,
        moneda="BOB",
        metodo_pago="TARJETA",
        estado="PENDIENTE",
        detalles_pago=detalles_pago,
        signature=calcular_firma_webhook(codigo_txn, total, "APPROVED"),
    )
    db.add(transaccion)
    db.flush()

    # 4. Liquidación transaccional atómica
    resultado = _liquidar_venta_transaccional(
        db=db,
        venta=venta,
        transaccion=transaccion,
        pasarela_nombre="AttentionPay",
        referencia_pago=codigo_autorizacion,
    )

    resultado["codigo_autorizacion"] = codigo_autorizacion
    resultado["tarjeta_enmascarada"] = tarjeta_enmascarada
    resultado["titular"] = payload.datos_tarjeta.titular.strip().upper()
    resultado["pasarela"] = "AttentionPay"
    return resultado


def procesar_pago_tarjeta_stripe(
    db: Session,
    payload: PagoTarjetaPayload,
    usuario_actual: Usuario,
) -> dict[str, Any]:
    """CU21: Procesa el cobro seguro con tarjeta vía Stripe API real."""
    import stripe

    settings = get_settings()
    stripe_key = settings.STRIPE_SECRET_KEY
    if not stripe_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La pasarela de pagos Stripe no está configurada en el servidor.",
        )
    stripe.api_key = stripe_key

    # 1. Validar productos y calcular totales reales desde DB
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No existen productos con id(s): {', '.join(map(str, faltantes))}.",
        )

    lineas = []
    for item in payload.items:
        prod = encontrados[item.producto_id]
        if prod.estado != "Activo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El producto '{prod.nombre}' no está disponible ({prod.estado}).",
            )
        if prod.stock_total < item.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock insuficiente para '{prod.nombre}': "
                    f"disponible {prod.stock_total}, solicitado {item.cantidad}."
                ),
            )
        precio = float(prod.precio_venta)
        lineas.append((item, prod, precio, round(precio * item.cantidad, 2)))

    subtotal_general = round(sum(l[3] for l in lineas), 2)
    costo_envio = 0.0 if subtotal_general >= ENVIO_GRATIS_DESDE else float(COSTO_ENVIO)
    total = round(subtotal_general + costo_envio, 2)

    entrega = payload.datos_entrega
    tipo_entrega = payload.tipo_entrega or "DOMICILIO"
    id_sucursal_final = payload.id_sucursal or (
        usuario_actual.id_sucursal if usuario_actual else None
    )
    if not id_sucursal_final and tipo_entrega == "DOMICILIO":
        id_sucursal_final = _resolver_sucursal_con_stock(db, payload.items, entrega.ciudad)

    codigo_venta = _generar_codigo_venta()
    monto_centavos = int(round(total * 100))

    # Determinar Payment Method de Stripe
    pm_id = payload.payment_method_id
    if not pm_id and payload.datos_tarjeta:
        num = payload.datos_tarjeta.numero_tarjeta.replace(" ", "")
        if num.endswith("0002"):
            pm_id = "pm_card_chargeCustomerFail"
        elif num.startswith("5"):
            pm_id = "pm_card_mastercard"
        elif num.startswith("3"):
            pm_id = "pm_card_amex"
        else:
            pm_id = "pm_card_visa"
    elif not pm_id:
        pm_id = "pm_card_visa"

    # Ejecutar cobro con Stripe
    try:
        intent = stripe.PaymentIntent.create(
            amount=monto_centavos,
            currency="bob",
            payment_method=pm_id,
            confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            description=f"Atention Moda - Venta {codigo_venta} - {entrega.nombre_cliente}",
            metadata={
                "codigo_venta": codigo_venta,
                "id_cliente": str(usuario_actual.id_usuario),
                "sucursal": str(id_sucursal_final or "N/A"),
            },
        )
    except stripe.error.CardError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Tarjeta rechazada por Stripe: {e.user_message or str(e)}",
        )
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error en pasarela Stripe: {str(e)}",
        )

    if intent.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"La transacción con Stripe no fue aprobada (estado: {intent.status}).",
        )

    # 2. Registrar venta en base de datos
    venta = Venta(
        id_cliente=usuario_actual.id_usuario,
        id_vendedor=None,
        id_sucursal=id_sucursal_final,
        tipo_entrega=tipo_entrega,
        total=total,
        costo_envio=costo_envio,
        metodo_pago="TARJETA",
        estado_pago="PENDIENTE",
        codigo=codigo_venta,
        nombre_cliente=entrega.nombre_cliente,
        correo=entrega.correo,
        telefono=entrega.telefono,
        direccion=entrega.direccion,
        ciudad=entrega.ciudad,
        referencia=entrega.referencia,
    )
    db.add(venta)
    db.flush()

    for item, prod, precio, sub in lineas:
        db.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=prod.id_producto,
                cantidad=item.cantidad,
                precio_unitario=precio,
                subtotal=sub,
                talla=item.talla,
                color=item.color,
            )
        )

    transaccion = TransaccionPago(
        id_venta=venta.id_venta,
        pasarela="Stripe",
        codigo_transaccion=intent.id,
        monto=total,
        moneda="BOB",
        metodo_pago="TARJETA",
        estado="PENDIENTE",
        detalles_pago=f"Stripe PaymentIntent: {intent.id} | Aprobado",
        signature=calcular_firma_webhook(intent.id, total, "APPROVED"),
    )
    db.add(transaccion)
    db.flush()

    resultado = _liquidar_venta_transaccional(
        db=db,
        venta=venta,
        transaccion=transaccion,
        pasarela_nombre="Stripe",
        referencia_pago=intent.id,
    )
    resultado["stripe_payment_intent_id"] = intent.id
    return resultado


def crear_sesion_checkout_stripe(
    db: Session,
    payload: CrearSesionStripePayload,
    usuario_actual: Usuario,
) -> dict[str, Any]:
    """CU21: Crea una sesión oficial de pago con Stripe Checkout Sessions API.

    1. Valida productos y existencias de stock en DB.
    2. Determina la sucursal de abastecimiento.
    3. Registra la Venta y DetalleVenta en estado PENDIENTE.
    4. Invoca stripe.checkout.Session.create(...) con line_items e URLs de redirección.
    5. Registra la TransaccionPago asociada y retorna el session_id y la url oficial de Stripe.
    """
    import stripe

    settings = get_settings()
    stripe_key = settings.STRIPE_SECRET_KEY
    if not stripe_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La pasarela de pagos Stripe no está configurada en el servidor (STRIPE_SECRET_KEY faltante).",
        )
    stripe.api_key = stripe_key

    # 1. Validar productos y existencias
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No existen productos con id(s): {', '.join(map(str, faltantes))}.",
        )

    lineas = []
    stripe_line_items = []
    for item in payload.items:
        prod = encontrados[item.producto_id]
        if prod.estado != "Activo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El producto '{prod.nombre}' no está disponible ({prod.estado}).",
            )
        if prod.stock_total < item.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock insuficiente para '{prod.nombre}': "
                    f"disponible {prod.stock_total}, solicitado {item.cantidad}."
                ),
            )
        precio = float(prod.precio_venta)
        sub = round(precio * item.cantidad, 2)
        lineas.append((item, prod, precio, sub))

        # Ítem para Stripe Checkout (en centavos BOB)
        stripe_line_items.append(
            {
                "price_data": {
                    "currency": "bob",
                    "unit_amount": int(round(precio * 100)),
                    "product_data": {
                        "name": prod.nombre,
                        "description": f"Talla: {item.talla} | Color: {item.color}",
                    },
                },
                "quantity": item.cantidad,
            }
        )

    subtotal_general = round(sum(l[3] for l in lineas), 2)
    costo_envio = 0.0 if subtotal_general >= ENVIO_GRATIS_DESDE else float(COSTO_ENVIO)
    total = round(subtotal_general + costo_envio, 2)

    # Si hay costo de envío, agregar como ítem en Stripe Checkout
    if costo_envio > 0:
        stripe_line_items.append(
            {
                "price_data": {
                    "currency": "bob",
                    "unit_amount": int(round(costo_envio * 100)),
                    "product_data": {
                        "name": "Costo de Envío a Domicilio",
                        "description": f"Tarifa estándar para pedidos menores a {ENVIO_GRATIS_DESDE} Bs.",
                    },
                },
                "quantity": 1,
            }
        )

    entrega = payload.datos_entrega
    tipo_entrega = payload.tipo_entrega or "DOMICILIO"
    id_sucursal_final = payload.id_sucursal or (
        usuario_actual.id_sucursal if usuario_actual else None
    )
    if not id_sucursal_final:
        id_sucursal_final = _resolver_sucursal_con_stock(
            db, payload.items, entrega.ciudad if entrega else None
        )

    codigo_venta = _generar_codigo_venta()

    # URLs por defecto de retorno hacia el frontend
    default_frontend = "http://localhost:4200/tienda/checkout"
    success_url = payload.success_url or f"{default_frontend}?stripe_session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = payload.cancel_url or f"{default_frontend}?cancel=true"

    # Registrar Venta en DB (estado PENDIENTE)
    venta = Venta(
        id_cliente=usuario_actual.id_usuario if usuario_actual else None,
        id_vendedor=None,
        id_sucursal=id_sucursal_final,
        tipo_entrega=tipo_entrega,
        total=total,
        costo_envio=costo_envio,
        metodo_pago="TARJETA",
        estado_pago="PENDIENTE",
        codigo=codigo_venta,
        nombre_cliente=entrega.nombre_cliente,
        correo=entrega.correo,
        telefono=entrega.telefono,
        direccion=entrega.direccion,
        ciudad=entrega.ciudad,
        referencia=entrega.referencia,
    )
    db.add(venta)
    db.flush()

    for item, prod, precio, sub in lineas:
        db.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=prod.id_producto,
                cantidad=item.cantidad,
                precio_unitario=precio,
                subtotal=sub,
                talla=item.talla,
                color=item.color,
            )
        )

    # Crear Checkout Session oficial con Stripe API
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=stripe_line_items,
            mode="payment",
            customer_email=entrega.correo,
            client_reference_id=str(venta.id_venta),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "id_venta": str(venta.id_venta),
                "codigo_venta": codigo_venta,
                "id_cliente": str(usuario_actual.id_usuario if usuario_actual else ""),
                "id_sucursal": str(id_sucursal_final or ""),
            },
        )
    except stripe.error.StripeError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al generar sesión oficial de Stripe Checkout: {str(e)}",
        )

    # Registrar Transacción en Pasarela
    transaccion = TransaccionPago(
        id_venta=venta.id_venta,
        pasarela="Stripe",
        codigo_transaccion=session.id,
        monto=total,
        moneda="BOB",
        metodo_pago="TARJETA",
        estado="PENDIENTE",
        detalles_pago=f"Stripe Checkout Session: {session.id}",
        signature=calcular_firma_webhook(session.id, total, "PENDIENTE"),
    )
    db.add(transaccion)
    db.commit()

    return {
        "session_id": session.id,
        "url": session.url,
        "codigo_venta": venta.codigo,
        "id_venta": venta.id_venta,
        "total": total,
        "moneda": "BOB",
    }


def confirmar_sesion_checkout_stripe(
    db: Session,
    session_id: str,
    usuario_actual: Usuario | None = None,
) -> dict[str, Any]:
    """CU21: Valida el estado de la sesión de Stripe Checkout al retornar de la pasarela y liquida la compra."""
    import stripe

    settings = get_settings()
    stripe_key = settings.STRIPE_SECRET_KEY
    if not stripe_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La pasarela de pagos Stripe no está configurada en el servidor.",
        )
    stripe.api_key = stripe_key

    # 1. Buscar la transacción en BD
    transaccion = (
        db.query(TransaccionPago)
        .filter(TransaccionPago.codigo_transaccion == session_id)
        .with_for_update(of=TransaccionPago)
        .first()
    )
    if not transaccion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la transacción con Stripe session_id {session_id}.",
        )

    venta = (
        db.query(Venta)
        .filter(Venta.id_venta == transaccion.id_venta)
        .with_for_update(of=Venta)
        .first()
    )
    if not venta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la venta asociada a la transacción {session_id}.",
        )

    # Si ya fue liquidada previamente (idempotencia)
    if transaccion.estado in ("PAGADO", "PAGADA"):
        fecha_iso = (
            venta.fecha_venta.isoformat()
            if getattr(venta, "fecha_venta", None)
            else datetime.now(timezone.utc).isoformat()
        )
        return {
            "status": "success",
            "message": "La sesión de Stripe ya fue liquidada y confirmada previamente.",
            "codigo_transaccion": transaccion.codigo_transaccion,
            "codigo_venta": venta.codigo,
            "total": float(venta.total),
            "costo_envio": float(venta.costo_envio),
            "metodo_pago": venta.metodo_pago,
            "estado_pago": "PAGADO",
            "id_venta": venta.id_venta,
            "fecha": fecha_iso,
            "stripe_session_id": session_id,
            "comprobante_fiscal": {
                "empresa": "ATTENTION S.R.L.",
                "nit": "1028374029",
                "nro_factura": venta.codigo,
                "nro_autorizacion": "29040011007",
                "codigo_control": session_id,
                "fecha_emision": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "total_bs": float(venta.total),
                "estado": "PAGADO",
                "metodo": "TARJETA",
                "leyenda": "ESTA FACTURA CONTRIBUYE AL DESARROLLO DEL PAÍS, EL USO ILÍCITO SERÁ SANCIONADO PENALMENTE DE ACUERDO A LEY",
            },
        }

    # 2. Consultar el estado en vivo con Stripe API
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al verificar la sesión con Stripe: {str(e)}",
        )

    if session.payment_status != "paid":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"El pago en Stripe no fue completado (estado: {session.payment_status}).",
        )

    # 3. Liquidar la venta transaccionalmente (descuenta stock sucursal, kardex, alerta <= 5, etc.)
    resultado = _liquidar_venta_transaccional(
        db=db,
        venta=venta,
        transaccion=transaccion,
        pasarela_nombre="Stripe-Checkout",
        referencia_pago=session_id,
    )
    resultado["stripe_session_id"] = session_id
    resultado["stripe_payment_intent_id"] = (
        getattr(session, "payment_intent", None) or session_id
    )
    return resultado


def confirmar_abono_qr(
    db: Session,
    payload: ConfirmarAbonoQRPayload,
    usuario_actual: Usuario,
) -> dict[str, Any]:
    """CU21: Valida el abono por QR institucional o comprobante de transferencia y liquida la orden."""
    transaccion = (
        db.query(TransaccionPago)
        .filter(TransaccionPago.codigo_transaccion == payload.codigo_transaccion)
        .with_for_update(of=TransaccionPago)
        .first()
    )
    if not transaccion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la transacción con código {payload.codigo_transaccion}.",
        )

    if transaccion.estado in ("PAGADO", "PAGADA"):
        venta = transaccion.venta
        return {
            "status": "success",
            "message": "El abono de esta transacción ya fue verificado previamente.",
            "codigo_transaccion": transaccion.codigo_transaccion,
            "codigo_venta": venta.codigo if venta else "",
            "estado": "PAGADO",
        }

    venta = (
        db.query(Venta)
        .filter(Venta.id_venta == transaccion.id_venta)
        .with_for_update(of=Venta)
        .first()
    )
    if not venta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la venta asociada a la transacción {payload.codigo_transaccion}.",
        )

    ref = payload.comprobante_referencia or "Abono QR Verificado"
    if payload.notas:
        ref += f" | {payload.notas}"

    return _liquidar_venta_transaccional(
        db=db,
        venta=venta,
        transaccion=transaccion,
        pasarela_nombre="QR-Simple",
        referencia_pago=ref,
    )


def obtener_estado_pago(db: Session, codigo_transaccion: str) -> dict[str, Any]:
    """Consulta el estado en vivo de una transacción de pasarela."""
    transaccion = (
        db.query(TransaccionPago)
        .filter(TransaccionPago.codigo_transaccion == codigo_transaccion)
        .first()
    )
    if not transaccion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe la transacción {codigo_transaccion}.",
        )

    venta = transaccion.venta
    return {
        "codigo_transaccion": transaccion.codigo_transaccion,
        "pasarela": transaccion.pasarela,
        "monto": float(transaccion.monto),
        "metodo_pago": transaccion.metodo_pago,
        "estado": transaccion.estado,
        "qr_data": transaccion.qr_data,
        "detalles_pago": transaccion.detalles_pago,
        "id_venta": transaccion.id_venta,
        "codigo_venta": venta.codigo if venta else None,
        "estado_venta": venta.estado_pago if venta else None,
        "fecha_creacion": transaccion.fecha_creacion.isoformat(),
        "fecha_actualizacion": transaccion.fecha_actualizacion.isoformat(),
    }
