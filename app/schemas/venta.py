# backend/app/schemas/venta.py
# Esquemas Pydantic para CU15+CU21 — Carrito de Compras y Checkout.
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Métodos de pago soportados por el checkout (CU21)
METODOS_PAGO = ("QR", "EFECTIVO", "TARJETA")

# Estados de pago de la venta
ESTADOS_PAGO = ("PENDIENTE", "PAGADO", "RECHAZADO")


class CheckoutItemPayload(BaseModel):
    """Ítem del carrito a comprar: producto, variante y cantidad.

    El precio NO viaja del cliente: el backend lo resuelve desde el
    catálogo real (precio_venta de la DB) para evitar manipulación.
    """

    producto_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    talla: str | None = Field(default=None, max_length=20)
    color: str | None = Field(default=None, max_length=50)


class DatosEntregaPayload(BaseModel):
    """Datos de facturación/entrega del checkout (CU21)."""

    nombre_cliente: str = Field(min_length=3, max_length=150)
    correo: str = Field(max_length=100)
    telefono: str = Field(min_length=6, max_length=20)
    direccion: str = Field(min_length=5, max_length=255)
    ciudad: str = Field(min_length=2, max_length=100)
    referencia: str | None = Field(default=None, max_length=255)


class CheckoutPayload(BaseModel):
    """Payload de POST /api/v1/ventas/checkout.

    - items: lista de productos comprados (id, cantidad, variante).
    - metodo_pago: QR | EFECTIVO | TARJETA.
    - datos_entrega: dirección de entrega/facturación.
    - El cliente se toma del TOKEN de la sesión (get_current_user).
    """

    items: list[CheckoutItemPayload] = Field(min_length=1)
    metodo_pago: str
    datos_entrega: DatosEntregaPayload

    @model_validator(mode="after")
    def _validar_metodo(self) -> "CheckoutPayload":
        if self.metodo_pago not in METODOS_PAGO:
            raise ValueError(
                f"Método de pago inválido. Valores permitidos: {', '.join(METODOS_PAGO)}."
            )
        return self

    @model_validator(mode="after")
    def _sin_duplicados(self) -> "CheckoutPayload":
        claves = [
            (i.producto_id, i.talla, i.color) for i in self.items
        ]
        if len(claves) != len(set(claves)):
            raise ValueError(
                "El carrito contiene variantes duplicadas (producto+talla+color)."
            )
        return self


class ItemVentaDetalle(BaseModel):
    """Línea de venta embebida en la respuesta (con variante y subtotal)."""

    id_detalle: int
    producto_id: int
    nombre: str
    talla: str | None = None
    color: str | None = None
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal


class VentaResponse(BaseModel):
    """Resumen completo de la venta procesada con su comprobante.

    Estructura alineada con el ticket del frontend: codigo, items con
    talla/color, total, metodo_pago, estado_pago, fecha y datos_entrega.
    """

    model_config = ConfigDict(from_attributes=True)

    id_venta: int
    codigo: str
    fecha_venta: datetime
    total: Decimal
    costo_envio: Decimal
    metodo_pago: str
    estado_pago: str
    comprobante_url: str | None = None
    items: list[ItemVentaDetalle] = Field(default_factory=list)
    datos_entrega: DatosEntregaPayload
    cliente_id: UUID | None = None
