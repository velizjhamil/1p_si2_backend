# backend/app/schemas/reserva.py
# Esquemas Pydantic para CU14 — Gestionar Reserva de Prendas.
from datetime import date, datetime
from decimal import Decimal
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
    fecha_expiracion: date
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
        default=None, max_length=255,
        description="Opcional: motivo al cancelar/anular",
    )

    @model_validator(mode="after")
    def _validar_estado(self) -> "ReservaStatusUpdate":
        if self.estado not in ESTADOS_TRANSICION:
            raise ValueError(
                f"Estado destino inválido. Valores permitidos: {', '.join(ESTADOS_TRANSICION)}."
            )
        return self


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
    """Respuesta completa: reserva + cliente + productos reservados."""

    model_config = ConfigDict(from_attributes=True)

    id_reserva: int
    cliente: ClienteDetalle
    fecha_reserva: datetime
    fecha_expiracion: date
    estado: str
    total_estimado: Decimal
    motivo_cancelacion: str | None = None
    productos: list[ProductoReservadoDetalle] = Field(default_factory=list)
