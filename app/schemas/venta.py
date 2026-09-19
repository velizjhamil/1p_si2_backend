# backend/app/schemas/venta.py
# Esquemas Pydantic para CU15+CU21 (Carrito/Checkout digital) y CU11 (POS).
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Métodos de pago soportados por el checkout (CU21)
METODOS_PAGO = ("QR", "EFECTIVO", "TARJETA")

# Estados de pago de la venta
ESTADOS_PAGO = ("PENDIENTE", "PAGADO", "RECHAZADO")

# Tipo de venta: ONLINE = Cliente desde el e-commerce (CU15+CU21);
# POS = Vendedor/GS/ASU cobra en mostrador (CU11). El backend decide
# qué campos son válidos según el rol del token, no según este flag.
TIPOS_VENTA = ("ONLINE", "POS")


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

    Soporta dos flujos (CU15+CU21 digital, CU11 POS presencial):

    - ONLINE (default): el cliente se toma del TOKEN (id del JWT). El
      campo id_cliente_override se IGNORA si viene. El backend ignora
      id_vendedor (queda NULL).
    - POS: solo roles V/GS/ASU pueden usar este modo. El backend exige
      id_cliente_override (UUID de un usuario con rol C existente) y
      registra id_vendedor = id del token. El Cliente NO puede forzar
      tipo_venta='POS' aunque lo mande: el backend lo rechaza.

    - items: lista de productos comprados (id, cantidad, variante).
    - metodo_pago: QR | EFECTIVO | TARJETA.
    - datos_entrega: dirección de entrega/facturación.
    """

    items: list[CheckoutItemPayload] = Field(min_length=1)
    metodo_pago: str
    datos_entrega: DatosEntregaPayload
    # CU11 — POS: tipo de venta. Default ONLINE para no romper el flujo
    # del Cliente. El backend valida la coherencia rol <-> tipo_venta.
    tipo_venta: Literal["ONLINE", "POS"] = "ONLINE"
    # CU11 — POS: id del cliente que el Vendedor eligió en el POS.
    # Requerido solo si tipo_venta='POS'; ignorado si tipo_venta='ONLINE'
    # o si el rol del token es C.
    id_cliente_override: UUID | None = None

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
    # CU11 — POS: vendedor que registró la venta (None en ventas online).
    vendedor_id: UUID | None = None
    # CU11 — POS: tipo de venta registrado (ONLINE | POS). Default ONLINE
    # para ventas legacy anteriores al flag.
    tipo_venta: Literal["ONLINE", "POS"] = "ONLINE"
