# backend/app/modules/ventas/models.py
# Paquete "Gestión Venta" — CU14 Reservas (Carrito/Ventas/Pagos llegarán
# con sus CUs de ciclos posteriores).
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Reserva(Base):
    """Reserva de prendas del cliente (CU14) — apartado de stock.

    Ciclo de vida: PENDIENTE -> CONFIRMADA -> COMPLETADA, con CANCELADA
    desde PENDIENTE/CONFIRMADA (CHECK en DB). Al crear la reserva el
    stock de cada producto se APARTA (descuento directo de
    productos.stock_total); al cancelar se DEVUELVE.
    """

    __tablename__ = "reservas"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('PENDIENTE', 'CONFIRMADA', 'CANCELADA', 'COMPLETADA')",
            name="estado_reserva_valido",
        ),
        CheckConstraint("total_estimado >= 0", name="total_estimado_no_negativo"),
    )

    id_reserva: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Cliente que aparta las prendas (rol C del sistema)
    id_cliente: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id_usuario"), nullable=False, index=True
    )
    fecha_reserva: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_expiracion: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDIENTE")
    total_estimado: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    motivo_cancelacion: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relaciones
    cliente: Mapped["Usuario"] = relationship(lazy="joined")  # noqa: F821
    detalles: Mapped[list["DetalleReserva"]] = relationship(
        back_populates="reserva", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Reserva(id_reserva={self.id_reserva}, estado={self.estado!r}, "
            f"total={self.total_estimado})>"
        )


class DetalleReserva(Base):
    """Línea de reserva (CU14) — producto, cantidad y precio congelado.

    `precio_unitario` congela el precio al momento de reservar (el precio
    del producto puede cambiar después; la reserva mantiene su total).
    """

    __tablename__ = "detalle_reservas"

    id_detalle: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_reserva: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reservas.id_reserva", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_producto: Mapped[int] = mapped_column(
        Integer, ForeignKey("productos.id_producto"), nullable=False, index=True
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Relaciones
    reserva: Mapped["Reserva"] = relationship(back_populates="detalles")
    producto: Mapped["Producto"] = relationship(lazy="joined")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<DetalleReserva(id_detalle={self.id_detalle}, "
            f"reserva={self.id_reserva}, producto={self.id_producto}, "
            f"cant={self.cantidad})>"
        )
