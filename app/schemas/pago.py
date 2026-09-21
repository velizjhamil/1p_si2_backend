# backend/app/schemas/pago.py
# CU15+CU21 — Pasarela de Pagos E-Commerce (AttentionPay / QR / Tarjeta / Webhook)
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.venta import CheckoutItemPayload, DatosEntregaPayload


class DatosTarjetaPayload(BaseModel):
    """Datos de la tarjeta de crédito o débito (solo en memoria durante el procesamiento)."""

    titular: str = Field(..., min_length=3, max_length=100)
    numero_tarjeta: str = Field(..., min_length=13, max_length=20)
    expiracion: str = Field(..., min_length=4, max_length=7, description="MM/AA o MM/AAAA")
    cvv: str = Field(..., min_length=3, max_length=4)


class ProcesarPagoPayload(BaseModel):
    """Payload para iniciar la compra y generar la transacción con la pasarela."""

    items: list[CheckoutItemPayload] = Field(..., min_length=1)
    metodo_pago: Literal["QR", "TARJETA", "EFECTIVO"]
    datos_entrega: DatosEntregaPayload
    datos_tarjeta: DatosTarjetaPayload | None = None
    id_sucursal: int | None = None
    id_cliente_override: UUID | None = None
    tipo_entrega: Literal["DOMICILIO", "RETIRO"] | None = None
    tipo_venta: Literal["ONLINE", "POS"] = "ONLINE"


class WebhookPayload(BaseModel):
    """Payload asíncrono emitido por el proveedor de la pasarela de pagos."""

    codigo_transaccion: str
    status: Literal["APPROVED", "REJECTED"]
    monto: float
    signature: str
    motivo: str | None = None


class TransaccionPagoResponse(BaseModel):
    """Respuesta con los datos públicos de la transacción de pago."""

    model_config = ConfigDict(from_attributes=True)

    id_transaccion: int
    id_venta: int
    pasarela: str
    codigo_transaccion: str
    monto: float
    moneda: str
    metodo_pago: str
    estado: str
    qr_data: str | None = None
    detalles_pago: str | None = None
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class SimularWebhookPayload(BaseModel):
    """Payload para emular la confirmación de la pasarela de forma local."""

    codigo_transaccion: str
    status: Literal["APPROVED", "REJECTED"] = "APPROVED"
    motivo: str | None = None


class PagoTarjetaPayload(BaseModel):
    """Payload para procesar cobro directo con tarjeta de crédito/débito vía Stripe API."""

    items: list[CheckoutItemPayload] = Field(..., min_length=1)
    datos_entrega: DatosEntregaPayload
    datos_tarjeta: DatosTarjetaPayload | None = None
    monto_total: float | None = None
    payment_method_id: str | None = None
    id_sucursal: int | None = None
    tipo_entrega: Literal["DOMICILIO", "RETIRO"] | None = "DOMICILIO"


class ProcesarTarjetaPayload(BaseModel):
    """Payload para procesar cobro directo con tarjeta en la pasarela nativa AttentionPay."""

    items: list[CheckoutItemPayload] = Field(..., min_length=1)
    datos_entrega: DatosEntregaPayload
    datos_tarjeta: DatosTarjetaPayload
    monto_total: float | None = None
    id_sucursal: int | None = None
    tipo_entrega: Literal["DOMICILIO", "RETIRO"] | None = "DOMICILIO"


class ConfirmarAbonoQRPayload(BaseModel):
    """Payload para confirmar la validación del abono o comprobante de pago por QR."""

    codigo_transaccion: str
    comprobante_referencia: str | None = None
    notas: str | None = None


class CrearSesionStripePayload(BaseModel):
    """Payload para generar una sesión de Stripe Checkout oficial en el servidor."""

    items: list[CheckoutItemPayload] = Field(..., min_length=1)
    datos_entrega: DatosEntregaPayload
    tipo_entrega: Literal["DOMICILIO", "RETIRO"] = "DOMICILIO"
    id_sucursal: int | None = None
    success_url: str | None = None
    cancel_url: str | None = None


class ConfirmarSesionStripePayload(BaseModel):
    """Payload para validar y liquidar la sesión de Stripe Checkout al retornar del pago."""

    session_id: str
