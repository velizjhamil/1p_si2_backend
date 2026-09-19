# backend/app/schemas/notificacion.py
# Esquemas Pydantic para CU10 - Gestion de Notificaciones.
#
# El modelo de entrada permite al disparador elegir el `id_usuario`
# destinatario (POST lo usan solo roles administrativos - ASU/GS - para
# emitir alertas a usuarios concretos). El usuario final NO crea sus
# propias notificaciones: solo las recibe, las lista y las marca como
# leidas.
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.notificaciones.models import TIPOS_NOTIFICACION


# Tipo literal alineado con el CHECK de la DB y con la union del front.
TipoNotificacionStr = Literal[
    "INFO", "WARNING", "ERROR", "SUCCESS", "STOCK", "PEDIDO", "DEVOLUCION", "SISTEMA"
]


class NotificacionCreate(BaseModel):
    """Payload POST /api/v1/notificaciones (disparador administrativo).

    Solo ASU/GS pueden crear notificaciones manualmente. El resto del
    sistema (jobs de stock, devolucion, venta) insertará via ORM con
    los mismos campos — este schema es la frontera HTTP.

    Modos de envio (mutuamente excluyentes, validados por `model_validator`):
    - Individual: `id_usuario` UUID y `enviar_a_todos=False` (default).
    - Masivo:     `enviar_a_todos=True` y `id_usuario` se ignora.
    """

    id_usuario: UUID | None = Field(
        default=None,
        description=(
            "UUID del destinatario (FK a usuarios.id_usuario). "
            "Requerido cuando `enviar_a_todos=False`. Se ignora si "
            "`enviar_a_todos=True`."
        ),
    )
    enviar_a_todos: bool = Field(
        default=False,
        description=(
            "Si True, hace fan-out a TODOS los usuarios activos del "
            "sistema (incluye Cliente). `id_usuario` se ignora."
        ),
    )
    titulo: str = Field(
        min_length=1, max_length=100, description="Titulo corto (1..100 chars)."
    )
    mensaje: str = Field(
        min_length=1, max_length=500, description="Cuerpo (1..500 chars)."
    )
    tipo: TipoNotificacionStr = Field(
        default="INFO", description=f"Uno de {TIPOS_NOTIFICACION}."
    )
    referencia_tipo: str | None = Field(
        default=None,
        max_length=30,
        description="Tipo de recurso referenciado (ej: 'venta', 'devolucion').",
    )
    referencia_id: str | None = Field(
        default=None,
        max_length=64,
        description="Id del recurso para deep-link (ej: '42', 'a1b2...').",
    )

    @model_validator(mode="after")
    def _validar_destinatario(self) -> "NotificacionCreate":
        """Exige exactamente UN modo de envio: individual O masivo.

        - Si `enviar_a_todos=True`, `id_usuario` puede ser None o cualquier
          UUID (se ignora). Esto simplifica al front (puede mandarlo o no).
        - Si `enviar_a_todos=False`, `id_usuario` es obligatorio.
        """
        if not self.enviar_a_todos and self.id_usuario is None:
            raise ValueError(
                "Debe indicar `id_usuario` (envio individual) o "
                "`enviar_a_todos=true` (envio masivo)."
            )
        return self


class NotificacionMasivaResponse(BaseModel):
    """Envelope de respuesta cuando se hace fan-out masivo.

    No devuelve la lista completa de NotificacionResponse para no inflar
    la respuesta cuando hay miles de destinatarios. El front usa `creadas`
    para mostrar el toast y `destinatarios` si necesita auditarlos.
    """

    model_config = ConfigDict(from_attributes=True)

    creadas: int = Field(
        description="Cantidad de notificaciones persistidas (== destinatarios activos)."
    )
    destinatarios: list[UUID] = Field(
        description="UUIDs de los usuarios a los que se les envio la notificacion."
    )
    omitidos_inactivos: int = Field(
        default=0,
        description=(
            "Cantidad de usuarios ignorados por estar inactivos. Hoy es 0 "
            "porque el fan-out filtra por `estado=True`, pero queda el "
            "campo para auditoria futura."
        ),
    )


class NotificacionResponse(BaseModel):
    """DTO de salida - coincide con la fila persistida (mas metadatos UI)."""

    model_config = ConfigDict(from_attributes=True)

    id_notificacion: int
    id_usuario: UUID
    titulo: str
    mensaje: str
    tipo: TipoNotificacionStr
    leida: bool
    fecha_creacion: datetime
    referencia_tipo: str | None = None
    referencia_id: str | None = None


class NotificacionListado(BaseModel):
    """Envelope del listado paginado - incluye total_no_leidas para el badge."""

    items: list[NotificacionResponse]
    total: int
    total_no_leidas: int
    page: int
    limit: int
    pages: int
