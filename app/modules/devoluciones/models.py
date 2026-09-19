# backend/app/modules/devoluciones/models.py
# CU13 - Gestion de Devoluciones.
#
# Ciclo de vida: SOLICITADA -> APROBADA -> COMPLETADA, con RECHAZADA como
# salida desde SOLICITADA.
# - SOLICITADA: el Cliente (rol C) pide la devolucion sobre una venta PAGADA
#   dentro de la ventana permitida (24h por defecto).
# - APROBADA: el Vendedor/GS/ASU admite la devolucion. AUN no se mueve stock.
# - COMPLETADA: el Vendedor/GS/ASU confirma la recepcion de la mercaderia y
#   se hace la ENTRADA del kardex (CU22) por cada linea devuelta, en la
#   misma transaccion (atomicidad con el UPDATE de productos.stock_total).
# - RECHAZADA: salida terminal con motivo_rechazo obligatorio.
#
# Decisiones de modelo:
# - `monto_total_devuelto` se persiste para auditoria (no se recalcula cada
#   vez). Equivale a SUM(detalle.subtotal) al momento de COMPLETAR.
# - `id_solicitante` y `id_procesador` son FKs a usuarios (snapshot del
#   responsable, igual que venta.id_vendedor).
# - `id_detalle_venta` en DetalleDevolucion permite conocer la variante
#   exacta (talla/color) que se devuelve y limitar la cantidad por linea
#   original: SUM(d.cantidad_devuelta) por id_detalle_venta <= d_venta.cantidad.
# - `id_producto` se duplica (ya esta en DetalleVenta) para queries directas
#   sin JOIN adicional (la columna es barata y el kardex lo necesita).
# - El CHECK de estado vive en la DB (defensa en profundidad) ademas de en
#   Pydantic. Los rangos de cantidad/subtotal tambien.
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
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


class Devolucion(Base):
    """Devolucion de una venta (CU13).

    `motivo` es el texto que escribio el cliente al solicitar. `motivo_rechazo`
    es la justificacion obligatoria cuando el Vendedor/GS/ASU rechaza
    (estado=RECHAZADA). `id_procesador` queda NULL hasta que pase de
    SOLICITADA.
    """

    __tablename__ = "devoluciones"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('SOLICITADA', 'APROBADA', 'RECHAZADA', 'COMPLETADA')",
            name="estado_devolucion_valido",
        ),
        CheckConstraint(
            "monto_total_devuelto >= 0", name="monto_total_devuelto_no_negativo"
        ),
    )

    id_devolucion: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Venta sobre la que se solicita la devolucion (debe estar PAGADO).
    id_venta: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ventas.id_venta"),
        nullable=False,
        index=True,
    )
    # Cliente dueno de la venta (snapshot; coincide con Venta.id_cliente).
    id_cliente: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario"),
        nullable=False,
        index=True,
    )
    # Usuario que registro la solicitud (normalmente == id_cliente; puede
    # diferir si en el futuro un vendedor solicita en nombre del cliente).
    id_solicitante: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario"),
        nullable=False,
        index=True,
    )
    # Vendedor/GS/ASU que aprobo/rechazo/completo (NULL en SOLICITADA).
    id_procesador: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fecha_solicitud: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Momento en que paso de SOLICITADA a APROBADA/RECHAZADA/COMPLETADA.
    fecha_procesado: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SOLICITADA", index=True
    )
    motivo: Mapped[str] = mapped_column(String(500), nullable=False)
    motivo_rechazo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    monto_total_devuelto: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )

    # Relaciones
    venta: Mapped["Venta"] = relationship(lazy="joined")  # noqa: F821
    cliente: Mapped["Usuario"] = relationship(  # noqa: F821
        "Usuario", foreign_keys="Devolucion.id_cliente", lazy="joined"
    )
    solicitante: Mapped["Usuario"] = relationship(  # noqa: F821
        "Usuario", foreign_keys="Devolucion.id_solicitante", lazy="joined"
    )
    procesador: Mapped["Usuario | None"] = relationship(  # noqa: F821
        "Usuario", foreign_keys="Devolucion.id_procesador", lazy="joined"
    )
    detalles: Mapped[list["DetalleDevolucion"]] = relationship(
        back_populates="devolucion", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Devolucion(id_devolucion={self.id_devolucion}, "
            f"id_venta={self.id_venta}, estado={self.estado!r})>"
        )


class DetalleDevolucion(Base):
    """Linea de devolucion (CU13) - cantidad devuelta por item de la venta.

    `precio_unitario` se congela del DetalleVenta original (no del catalogo
    actual) para que el reembolso respete lo que el cliente pago. La suma de
    `cantidad_devuelta` para un mismo `id_detalle_venta` entre TODAS las
    devoluciones de la misma venta NO puede superar la cantidad original
    (regla validada en el service).
    """

    __tablename__ = "detalle_devoluciones"
    __table_args__ = (
        CheckConstraint("cantidad_devuelta > 0", name="cantidad_devuelta_positiva"),
        CheckConstraint("precio_unitario >= 0", name="precio_unitario_no_negativo"),
        CheckConstraint("subtotal >= 0", name="subtotal_no_negativo"),
    )

    id_detalle: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_devolucion: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("devoluciones.id_devolucion", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Linea original de la venta que se devuelve (FK al detalle de venta).
    id_detalle_venta: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("detalle_ventas.id_detalle"),
        nullable=False,
        index=True,
    )
    # Producto devuelto (duplicado respecto a DetalleVenta para queries del
    # kardex y para que la API no necesite JOIN extra al listar).
    id_producto: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("productos.id_producto"),
        nullable=False,
        index=True,
    )
    cantidad_devuelta: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Relaciones
    devolucion: Mapped["Devolucion"] = relationship(back_populates="detalles")
    detalle_venta: Mapped["DetalleVenta"] = relationship(lazy="joined")  # noqa: F821
    producto: Mapped["Producto"] = relationship(lazy="joined")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<DetalleDevolucion(id_detalle={self.id_detalle}, "
            f"devolucion={self.id_devolucion}, producto={self.id_producto}, "
            f"cant={self.cantidad_devuelta})>"
        )
