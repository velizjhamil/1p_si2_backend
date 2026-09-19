# backend/app/schemas/devolucion.py
# Esquemas Pydantic para CU13 - Gestion de Devoluciones.
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Estados de la devolucion (alineados con el CHECK de la DB).
ESTADOS_DEVOLUCION = ("SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA")

# Roles con permiso para procesar devoluciones (aprobar/rechazar/completar).
# Coincide con el set de roles operativos del sistema.
ROLES_PROCESAMIENTO = ("ASU", "GS", "V")

# Ventana maxima en horas para que un cliente pueda solicitar una
# devolucion sobre una venta PAGADO. Esta pensada como politica por
# defecto: si la operacion quiere otra ventana, se cambia aca.
VENTANA_DEVOLUCION_HORAS = 24


class DetalleDevolucionPayload(BaseModel):
    """Linea de la devolucion solicitada por el cliente.

    `detalle_venta_id` apunta a la linea ORIGINAL de la venta
    (id_detalle de detalle_ventas). La cantidad devuelta no puede
    superar la cantidad original de esa linea ni la cantidad ya devuelta
    en solicitudes previas para el mismo id_detalle_venta (validado en
    el service porque requiere leer otras devoluciones).
    """

    detalle_venta_id: int = Field(gt=0, description="Id de la linea de venta original")
    cantidad_devuelta: int = Field(gt=0, description="Unidades a devolver (>0)")


class DevolucionCreatePayload(BaseModel):
    """Payload de POST /api/v1/devoluciones (cliente solicita).

    El backend toma `id_cliente` y `id_solicitante` del token (rol C).
    El `id_venta` debe corresponder a una venta del cliente en estado
    PAGADO y dentro de la ventana permitida.
    """

    id_venta: int = Field(gt=0, description="Id de la venta a devolver")
    motivo: str = Field(
        min_length=10,
        max_length=500,
        description="Motivo de la devolucion (>=10 caracteres)",
    )
    items: list[DetalleDevolucionPayload] = Field(
        min_length=1, description="Lineas a devolver (>=1)"
    )

    @model_validator(mode="after")
    def _sin_duplicados(self) -> "DevolucionCreatePayload":
        ids = [i.detalle_venta_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "La solicitud contiene lineas duplicadas (detalle_venta_id repetido)."
            )
        return self


class DevolucionProcesarPayload(BaseModel):
    """Payload de PATCH /api/v1/devoluciones/{id}/procesar.

    Solo roles con permiso de procesamiento (V/GS/ASU). La accion es:
    - APROBAR: pasa de SOLICITADA -> APROBADA (no mueve stock todavia).
    - RECHAZAR: pasa de SOLICITADA -> RECHAZADA. `motivo_rechazo`
      obligatorio en este caso.
    - COMPLETAR: pasa de APROBADA -> COMPLETADA. Aqui se hace la
      ENTRADA del kardex por linea.
    """

    accion: Literal["APROBAR", "RECHAZAR", "COMPLETAR"]
    motivo_rechazo: str | None = Field(
        default=None,
        max_length=500,
        description="Obligatorio si accion=RECHAZAR",
    )

    @model_validator(mode="after")
    def _validar_motivo_rechazo(self) -> "DevolucionProcesarPayload":
        if self.accion == "RECHAZAR":
            if not self.motivo_rechazo or len(self.motivo_rechazo.strip()) < 5:
                raise ValueError(
                    "Para RECHAZAR una devolucion debe indicar un motivo_rechazo "
                    "de al menos 5 caracteres."
                )
        # Si NO es RECHAZAR y viene motivo_rechazo, lo ignoramos silenciosamente
        # (no es error; el backend simplemente no lo persiste).
        return self


class DetalleDevolucionResponse(BaseModel):
    """Linea de devolucion serializada para la respuesta."""

    model_config = ConfigDict(from_attributes=True)

    id_detalle: int
    detalle_venta_id: int
    id_producto: int
    nombre_producto: str | None = None
    cantidad_devuelta: int
    precio_unitario: Decimal
    subtotal: Decimal


class DevolucionResponse(BaseModel):
    """Devolucion completa con sus lineas (alineada al envelope estandar)."""

    model_config = ConfigDict(from_attributes=True)

    id_devolucion: int
    id_venta: int
    codigo_venta: str | None = None
    estado: Literal["SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA"]
    motivo: str
    motivo_rechazo: str | None = None
    fecha_solicitud: datetime
    fecha_procesado: datetime | None = None
    monto_total_devuelto: Decimal
    cliente_id: UUID
    cliente_nombre: str | None = None
    solicitante_id: UUID
    procesador_id: UUID | None = None
    procesador_nombre: str | None = None
    items: list[DetalleDevolucionResponse] = Field(default_factory=list)
