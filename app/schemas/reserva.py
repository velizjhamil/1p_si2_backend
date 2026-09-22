# backend/app/schemas/reserva.py
# Esquemas Pydantic para CU14 — Gestionar Reserva de Prendas.
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Estados válidos de la reserva (ciclo de vida CU14)
ESTADOS_RESERVA = ("PENDIENTE", "CONFIRMADA", "CANCELADA", "COMPLETADA")

# Estados destino permitidos por el PATCH de estado (transiciones de negocio)
ESTADOS_TRANSICION = ("CONFIRMADA", "CANCELADA", "COMPLETADA")


class ReservaItemPayload(BaseModel):
    """Línea de la reserva: producto, cantidad y precio congelado."""

    id_producto: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    precio_unitario: float = Field(gt=0)


class ReservaCreatePayload(BaseModel):
    """Payload de POST /api/v1/reservas.

    El cliente se toma del TOKEN de la sesión (get_current_user); si el
    payload trae id_cliente explícito (vendedor registrando a nombre de
    un cliente), se valida que exista y tenga rol C.
    """

    id_cliente: UUID | None = Field(
        default=None, description="Opcional: cliente a nombre de quien se reserva"
    )
    id_sucursal: int | None = Field(
        default=None, description="Opcional: sucursal donde se aparta la prenda"
    )
    tipo_entrega: Literal["RETIRO", "DOMICILIO"] = Field(
        default="RETIRO",
        description="Modalidad de entrega: RETIRO (en tienda física) o DOMICILIO (contra entrega)",
    )
    direccion_entrega: str | None = Field(
        default=None,
        max_length=255,
        description="Dirección de envío si la modalidad es DOMICILIO",
    )
    telefono_entrega: str | None = Field(
        default=None,
        max_length=50,
        description="Teléfono de contacto para la entrega",
    )
    fecha_expiracion: date | None = Field(
        default=None, description="Opcional: por defecto 2 días (48 horas)"
    )
    items: list[ReservaItemPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def _sin_duplicados(self) -> "ReservaCreatePayload":
        ids = [i.id_producto for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("La lista de items contiene productos duplicados.")
        return self


class ReservaStatusUpdate(BaseModel):
    """Payload de PATCH /api/v1/reservas/{id}/estado."""

    estado: str
    motivo_cancelacion: str | None = Field(
        default=None,
        max_length=255,
        description="Opcional: motivo al cancelar/anular",
    )

    @model_validator(mode="after")
    def _validar_estado(self) -> "ReservaStatusUpdate":
        if self.estado not in ESTADOS_TRANSICION:
            raise ValueError(
                f"Estado destino inválido. Valores permitidos: {', '.join(ESTADOS_TRANSICION)}."
            )
        return self


class PagarAnticipoPayload(BaseModel):
    """Payload para procesar el pago del anticipo del 50% de la reserva."""

    metodo_pago: Literal["QR", "TARJETA"] = "QR"
    referencia_pago: str | None = Field(
        default=None, description="Comprobante, código QR o referencia de pasarela"
    )


class ClienteDetalle(BaseModel):
    """Cliente embebido en la respuesta de la reserva."""

    model_config = ConfigDict(from_attributes=True)

    id_usuario: UUID
    nombre: str
    apellido: str | None = None
    correo: str


class ProductoReservadoDetalle(BaseModel):
    """Producto embebido en el detalle de la reserva."""

    model_config = ConfigDict(from_attributes=True)

    id_detalle: int
    id_producto: int
    nombre: str
    cantidad: int
    precio_unitario: Decimal


class ReservaResponse(BaseModel):
    """Respuesta completa: reserva + cliente + productos reservados + anticipo y expiración."""

    model_config = ConfigDict(from_attributes=True)

    id_reserva: int
    id_sucursal: int | None = None
    sucursal_nombre: str | None = None
    tipo_entrega: str = "RETIRO"
    direccion_entrega: str | None = None
    telefono_entrega: str | None = None
    cliente: ClienteDetalle
    fecha_reserva: datetime
    fecha_expiracion: date
    fecha_confirmacion: datetime | None = None
    fecha_expiracion_dt: datetime | None = None
    estado: str
    total_estimado: Decimal
    monto_anticipo: Decimal = Decimal("0.00")
    monto_anticipo_pagado: Decimal = Decimal("0.00")
    monto_reembolsado: Decimal = Decimal("0.00")
    monto_penalizacion: Decimal = Decimal("0.00")
    metodo_pago_anticipo: str | None = None
    codigo_transaccion_anticipo: str | None = None
    motivo_cancelacion: str | None = None
    minutos_restantes: int | None = None
    es_expirada: bool = False
    productos: list[ProductoReservadoDetalle] = Field(default_factory=list)
