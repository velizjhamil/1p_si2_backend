# backend/app/api/v1/endpoints/pagos.py
# CU15+CU21 — Endpoints de la Pasarela de Pagos (Procesar, Webhook, Estado, Simular)
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles
from app.modules.pagos.service import (
    calcular_firma_webhook,
    confirmar_abono_qr,
    confirmar_sesion_checkout_stripe,
    crear_sesion_checkout_stripe,
    iniciar_transaccion_pago,
    obtener_estado_pago,
    procesar_pago_tarjeta_stripe,
    procesar_tarjeta_attentionpay,
    procesar_webhook_pasarela,
)
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import TransaccionPago
from app.schemas.pago import (
    ConfirmarAbonoQRPayload,
    ConfirmarSesionStripePayload,
    CrearSesionStripePayload,
    PagoTarjetaPayload,
    ProcesarPagoPayload,
    ProcesarTarjetaPayload,
    SimularWebhookPayload,
    WebhookPayload,
)
from app.schemas.venta import CobroEfectivoPayload

router = APIRouter()


def _envelope(data: Any, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message}."""
    payload = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


@router.post("/procesar", status_code=status.HTTP_201_CREATED)
def procesar_pago(
    payload: ProcesarPagoPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU15+CU21: Inicia el proceso de checkout y genera la transacción con la pasarela.

    - Cliente (rol C): compra online (ONLINE).
    - Vendedor, Gerente o Administrador: venta en mostrador (POS).
    """
    rol = (usuario_actual.rol.nombre_rol if usuario_actual.rol else "").upper()
    modo = "POS" if payload.tipo_venta == "POS" and rol in ("V", "GS", "ASU") else "ONLINE"

    if modo == "ONLINE" and rol != "C":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo usuarios con rol Cliente pueden realizar compras en la tienda online.",
        )

    resultado = iniciar_transaccion_pago(db, payload, usuario_actual, modo)
    return _envelope(resultado, message="Transacción de pago generada exitosamente.")


@router.post("/webhook")
def recibir_webhook_pasarela(
    payload: WebhookPayload,
    db: Session = Depends(get_db),
    x_webhook_signature: str | None = Header(None, alias="X-Webhook-Signature"),
):
    """Receptor asíncrono del Webhook de la pasarela de pagos.

    Valida la firma HMAC-SHA256 y confirma la venta descontando el stock
    del inventario de la sucursal de forma transaccional.
    """
    resultado = procesar_webhook_pasarela(db, payload, x_webhook_signature)
    return _envelope(resultado, message=resultado.get("message", "Webhook procesado."))


@router.get("/estado/{codigo_transaccion}")
def consultar_estado_transaccion(
    codigo_transaccion: str,
    db: Session = Depends(get_db),
):
    """Consulta el estado en vivo de una transacción de pasarela."""
    datos = obtener_estado_pago(db, codigo_transaccion)
    return _envelope(datos, message="Estado de la transacción recuperado.")


@router.post("/simular-confirmacion")
def simular_confirmacion_webhook(
    payload: SimularWebhookPayload,
    db: Session = Depends(get_db),
):
    """Endpoint de desarrollo y demostración: emula la confirmación bancaria del pago.

    Genera una firma criptográfica HMAC-SHA256 válida y ejecuta el procesamiento
    del webhook como si proviniera directamente de la entidad financiera o pasarela.
    """
    transaccion = (
        db.query(TransaccionPago)
        .filter(TransaccionPago.codigo_transaccion == payload.codigo_transaccion)
        .first()
    )
    if not transaccion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la transacción con código {payload.codigo_transaccion}.",
        )

    firma_valida = calcular_firma_webhook(
        transaccion.codigo_transaccion, float(transaccion.monto), payload.status
    )

    webhook_payload = WebhookPayload(
        codigo_transaccion=transaccion.codigo_transaccion,
        status=payload.status,
        monto=float(transaccion.monto),
        signature=firma_valida,
        motivo=payload.motivo,
    )

    resultado = procesar_webhook_pasarela(db, webhook_payload, firma_valida)
    return _envelope(
        resultado,
        message=f"Confirmación simulada exitosamente con estado {payload.status}.",
    )


@router.post("/cobrar-efectivo", status_code=status.HTTP_200_OK)
def cobrar_en_efectivo_pagos(
    payload: CobroEfectivoPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Cobro en efectivo en mostrador (alias accesible en /api/v1/pagos/cobrar-efectivo)."""
    from app.api.v1.endpoints.ventas import cobrar_en_efectivo as _cobrar
    return _cobrar(payload=payload, db=db, usuario_actual=usuario_actual)


@router.post("/procesar-tarjeta", status_code=status.HTTP_200_OK)
def procesar_tarjeta_attentionpay_endpoint(
    payload: ProcesarTarjetaPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Procesa el pago directo con tarjeta en la pasarela nativa AttentionPay.

    - Valida número de tarjeta (algoritmo de Luhn), fecha de expiración y CVV en servidor.
    - Liquida la venta en la sucursal de inventario correspondiente y descuenta stock atómicamente.
    - Dispara notificaciones de stock crítico si el inventario queda con 5 o menos prendas.
    - Genera el comprobante fiscal y confirma la orden para el cliente.
    """
    resultado = procesar_tarjeta_attentionpay(db, payload, usuario_actual)
    return _envelope(
        resultado,
        message="Pago con tarjeta aprobado y procesado exitosamente por AttentionPay. Stock liquidado.",
    )


@router.post("/tarjeta", status_code=status.HTTP_200_OK)
def cobrar_con_tarjeta(
    payload: PagoTarjetaPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Procesa el pago directo con tarjeta (AttentionPay o Stripe)."""
    if payload.datos_tarjeta and not payload.payment_method_id:
        p_att = ProcesarTarjetaPayload(
            items=payload.items,
            datos_entrega=payload.datos_entrega,
            datos_tarjeta=payload.datos_tarjeta,
            monto_total=payload.monto_total,
            id_sucursal=payload.id_sucursal,
            tipo_entrega=payload.tipo_entrega,
        )
        resultado = procesar_tarjeta_attentionpay(db, p_att, usuario_actual)
        return _envelope(resultado, message="Pago con tarjeta procesado exitosamente por AttentionPay.")

    resultado = procesar_pago_tarjeta_stripe(db, payload, usuario_actual)
    return _envelope(resultado, message="Pago con tarjeta procesado exitosamente vía Stripe.")


@router.post("/qr/confirmar-abono", status_code=status.HTTP_200_OK)
def confirmar_abono_qr_endpoint(
    payload: ConfirmarAbonoQRPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Valida el abono/transferencia del QR institucional y confirma la liquidación de la orden."""
    resultado = confirmar_abono_qr(db, payload, usuario_actual)
    return _envelope(resultado, message="Abono de QR verificado exitosamente. Stock liquidado.")


@router.post("/crear-sesion-stripe", status_code=status.HTTP_201_CREATED)
def crear_sesion_stripe_endpoint(
    payload: CrearSesionStripePayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Genera una sesión de pago oficial en Stripe Checkout Sessions.

    Retorna session_id y la URL oficial de Stripe para redirigir al cliente.
    """
    resultado = crear_sesion_checkout_stripe(db, payload, usuario_actual)
    return _envelope(resultado, message="Sesión oficial de Stripe Checkout generada exitosamente.")


@router.post("/confirmar-sesion-stripe", status_code=status.HTTP_200_OK)
def confirmar_sesion_stripe_endpoint(
    payload: ConfirmarSesionStripePayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU21: Valida el pago con Stripe al retornar a success_url y liquida la compra.

    Descuenta stock local de la sucursal, dispara alertas críticas y genera factura.
    """
    resultado = confirmar_sesion_checkout_stripe(db, payload.session_id, usuario_actual)
    return _envelope(resultado, message="Sesión de Stripe confirmada exitosamente. Stock liquidado.")

