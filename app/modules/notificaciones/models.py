# backend/app/modules/notificaciones/models.py
# CU10 - Gestion de Notificaciones.
#
# Una notificacion es un mensaje in-app dirigido a UN usuario concreto
# (id_usuario FK). El "destinatario" es siempre una persona, no un rol:
# si la operacion necesita fan-out a un rol, el disparador (job/handler)
# materializa N notificaciones, una por usuario del rol. Este patron es
# el mismo que usa el backend para venta.id_cliente y devolucion.id_cliente
# (siempre id_usuario concreto, no "rol destinatario").
#
# Decisiones de modelo:
# - `tipo` vive como VARCHAR con CHECK en DB (defensa en profundidad).
#   Valores permitidos: INFO, WARNING, ERROR, SUCCESS, STOCK, PEDIDO,
#   DEVOLUCION, SISTEMA. La UI los mapea a color de badge.
# - `leida` es BOOLEAN con default FALSE; el PATCH /{id}/leer lo pasa a
#   TRUE. Se indexa porque el contador de no-leidas del header se consulta
#   en cada request.
# - `referencia_tipo` + `referencia_id` son opcionales: permiten al front
#   hacer deep-link (ej: "ir a la venta #42" desde una notificacion de
#   tipo=PEDIDO). NULL cuando la notificacion es puramente informativa.
# - `fecha_creacion` con server_default=now() garantiza consistencia de
#   timestamps incluso si la app cliente tiene reloj desincronizado.
# - ondelete CASCADE en id_usuario: si se borra el usuario, se borran sus
#   notificaciones (no tiene sentido mantenerlas huérfanas).
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# Tipos válidos (alineados con el CHECK de la DB y con el badge del front).
TIPOS_NOTIFICACION = (
    "INFO",
    "WARNING",
    "ERROR",
    "SUCCESS",
    "STOCK",
    "PEDIDO",
    "DEVOLUCION",
    "SISTEMA",
)


class Notificacion(Base):
    """Notificacion in-app para un usuario concreto (CU10).

    Una fila = un mensaje para una persona. La UI la lista en la campanita
    del header y permite marcarla como leida o eliminarla. Los jobs del
    sistema (stock bajo, devolucion solicitada, venta registrada, etc.)
    insertan filas con `tipo` y `referencia_*` apropiados.
    """

    __tablename__ = "notificaciones"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('INFO', 'WARNING', 'ERROR', 'SUCCESS', 'STOCK', "
            "'PEDIDO', 'DEVOLUCION', 'SISTEMA')",
            name="tipo_notificacion_valido",
        ),
        CheckConstraint(
            "length(titulo) >= 1 AND length(titulo) <= 100",
            name="titulo_longitud_valida",
        ),
        CheckConstraint(
            "length(mensaje) >= 1 AND length(mensaje) <= 500",
            name="mensaje_longitud_valida",
        ),
        # Indice compuesto para "dame las no leidas del usuario X ordenadas
        # por fecha DESC" — patron mas frecuente del header / campanita.
        Index(
            "ix_notificaciones_usuario_leida_fecha",
            "id_usuario",
            "leida",
            "fecha_creacion",
        ),
    )

    # PK autoincrement (SERIAL en Postgres).
    id_notificacion: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Destinatario: usuario concreto de la DB real (UUID, igual que el resto).
    id_usuario: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="CASCADE"),
        nullable=False,
    )
    titulo: Mapped[str] = mapped_column(String(100), nullable=False)
    mensaje: Mapped[str] = mapped_column(String(500), nullable=False)
    tipo: Mapped[str] = mapped_column(
        String(20), nullable=False, default="INFO", index=True
    )
    leida: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Opcionales: deep-link desde la campana a un recurso concreto.
    referencia_tipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    referencia_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relación al destinatario (lazy joined para que la UI no haga N+1 al
    # renderizar el nombre del emisor cuando agreguemos ese campo en el futuro).
    usuario: Mapped["Usuario"] = relationship(  # noqa: F821
        "Usuario", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<Notificacion(id_notificacion={self.id_notificacion}, "
            f"id_usuario={self.id_usuario}, tipo={self.tipo!r}, leida={self.leida})>"
        )
