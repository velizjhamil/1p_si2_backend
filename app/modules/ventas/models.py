# backend/app/modules/ventas/models.py
# Paquete "Gestión Venta" — CU14 Reservas, CU15+CU21 Carrito/Checkout.
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import Producto
from app.modules.usuarios.models import Usuario


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
    # Sucursal de apartado de stock (CU17 multi-sucursal)
    id_sucursal: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # CU14: Política de anticipo 50% y expiración a 48h con reembolso parcial 50%
    monto_anticipo: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    monto_anticipo_pagado: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    monto_reembolsado: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    monto_penalizacion: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    metodo_pago_anticipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    codigo_transaccion_anticipo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_confirmacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fecha_expiracion_dt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Modalidad de entrega: RETIRO (en tienda) o DOMICILIO (contra entrega)
    tipo_entrega: Mapped[str] = mapped_column(
        String(20), nullable=False, default="RETIRO"
    )
    direccion_entrega: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    telefono_entrega: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Relaciones
    cliente: Mapped["Usuario"] = relationship(lazy="joined")  # noqa: F821
    sucursal: Mapped["Sucursal | None"] = relationship(lazy="joined")  # noqa: F821
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


class Venta(Base):
    """Venta procesada por el checkout (CU15+CU21).

    Ciclo de pago: PENDIENTE -> PAGADO (pasarela aprueba) o RECHAZADO.
    El checkout registra la venta ya PAGADA (mock de pasarela), descuenta
    stock de productos y escribe el kardex SALIDA en la misma transacción.
    `codigo` es el comprobante legible del ticket (ATT-xxxxxx).
    """

    __tablename__ = "ventas"
    __table_args__ = (
        CheckConstraint(
            "metodo_pago IN ('QR', 'EFECTIVO', 'TARJETA')", name="metodo_pago_valido"
        ),
        CheckConstraint(
            "estado_pago IN ('PENDIENTE', 'PAGADO', 'RECHAZADO')",
            name="estado_pago_valido",
        ),
        CheckConstraint("total >= 0", name="total_no_negativo"),
        CheckConstraint("costo_envio >= 0", name="costo_envio_no_negativo"),
        CheckConstraint(
            "tipo_entrega IN ('DOMICILIO', 'RETIRO')", name="tipo_entrega_valido"
        ),
    )

    id_venta: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Cliente que compró (rol C del sistema; también del token)
    id_cliente: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id_usuario"), nullable=False, index=True
    )
    # Vendedor que cobró la venta (NULL en ventas online del Cliente).
    # Para el POS (CU11) lo setea el endpoint cuando el rol del token es
    # V/GS/ASU y el payload trae tipo_venta='POS'.
    id_vendedor: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fecha_venta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    costo_envio: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    metodo_pago: Mapped[str] = mapped_column(String(20), nullable=False)
    estado_pago: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDIENTE")
    # Comprobante legible del ticket (ATT-xxxxxx)
    codigo: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    comprobante_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # CU18: DOMICILIO genera un envío (modules/delivery); RETIRO = entrega
    # en tienda/mostrador (POS). El server_default cubre las ventas previas.
    tipo_entrega: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DOMICILIO", server_default="DOMICILIO"
    )

    # Datos de entrega/facturación (snapshot congelado de la compra)
    nombre_cliente: Mapped[str] = mapped_column(String(150), nullable=False)
    correo: Mapped[str] = mapped_column(String(100), nullable=False)
    telefono: Mapped[str] = mapped_column(String(20), nullable=False)
    direccion: Mapped[str] = mapped_column(String(255), nullable=False)
    ciudad: Mapped[str] = mapped_column(String(100), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Sucursal donde se efectuó la venta / retiro (CU17 multi-sucursal)
    id_sucursal: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relaciones
    # Con id_vendedor en la tabla hay DOS FKs a usuarios.id_usuario;
    # SQLAlchemy no puede inferir cuál usar para `cliente`, así que se
    # especifica explícitamente con foreign_keys.
    cliente: Mapped["Usuario"] = relationship(  # noqa: F821
        "Usuario",
        foreign_keys="Venta.id_cliente",
        lazy="joined",
    )
    vendedor: Mapped["Usuario | None"] = relationship(  # noqa: F821
        "Usuario",
        foreign_keys="Venta.id_vendedor",
        lazy="joined",
    )
    sucursal: Mapped["Sucursal | None"] = relationship(  # noqa: F821
        lazy="joined",
    )
    detalles: Mapped[list["DetalleVenta"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan"
    )
    transacciones: Mapped[list["TransaccionPago"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Venta(id_venta={self.id_venta}, codigo={self.codigo!r}, "
            f"total={self.total}, estado_pago={self.estado_pago!r})>"
        )


class DetalleVenta(Base):
    """Línea de venta (CU15+CU21) — producto, cantidad, precio y subtotal.

    `precio_unitario` congela el precio REAL del catálogo al momento de
    la compra (lo calcula el backend, nunca el cliente). `talla` y
    `color` congelan la variante seleccionada en el carrito.
    """

    __tablename__ = "detalle_ventas"

    id_detalle: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_venta: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ventas.id_venta", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_producto: Mapped[int] = mapped_column(
        Integer, ForeignKey("productos.id_producto"), nullable=False, index=True
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    # Variante comprada (talla+color seleccionadas en el carrito)
    talla: Mapped[str | None] = mapped_column(String(20), nullable=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relaciones
    venta: Mapped["Venta"] = relationship(back_populates="detalles")
    producto: Mapped["Producto"] = relationship(lazy="joined")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<DetalleVenta(id_detalle={self.id_detalle}, venta={self.id_venta}, "
            f"producto={self.id_producto}, cant={self.cantidad})>"
        )


class TransaccionPago(Base):
    """Transacción de pasarela de pago (CU15+CU21).

    Representa el intento o procesamiento de pago vinculado a una venta.
    Soporta confirmación asíncrona mediante Webhook firmado criptográficamente.
    """

    __tablename__ = "transacciones_pago"
    __table_args__ = (
        CheckConstraint(
            "metodo_pago IN ('QR', 'EFECTIVO', 'TARJETA')",
            name="metodo_pago_transaccion_valido",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE', 'PAGADO', 'RECHAZADO')",
            name="estado_transaccion_valido",
        ),
        CheckConstraint("monto >= 0", name="monto_transaccion_no_negativo"),
    )

    id_transaccion: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_venta: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ventas.id_venta", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pasarela: Mapped[str] = mapped_column(
        String(50), nullable=False, default="AttentionPay"
    )
    codigo_transaccion: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    monto: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(10), nullable=False, default="BOB")
    metodo_pago: Mapped[str] = mapped_column(String(20), nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDIENTE"
    )
    detalles_pago: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    qr_data: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relaciones
    venta: Mapped["Venta"] = relationship(back_populates="transacciones")

    def __repr__(self) -> str:
        return (
            f"<TransaccionPago(id={self.id_transaccion}, "
            f"codigo={self.codigo_transaccion!r}, monto={self.monto}, "
            f"estado={self.estado!r})>"
        )


class Carrito(Base):
    """Carrito de compras persistente por usuario (CU15).
    
    Permite almacenar las prendas seleccionadas por el cliente en la base de datos
    para persistencia multiplataforma (Web y Móvil) y validación de disponibilidad
    en tiempo real antes del checkout.
    """

    __tablename__ = "carritos"

    id_carrito: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_usuario: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relaciones
    usuario: Mapped["Usuario"] = relationship(lazy="selectin")  # noqa: F821
    items: Mapped[list["ItemCarrito"]] = relationship(
        back_populates="carrito",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ItemCarrito.id_item",
    )

    def __repr__(self) -> str:
        return f"<Carrito(id={self.id_carrito}, usuario={self.id_usuario})>"


class ItemCarrito(Base):
    """Línea o ítem dentro del carrito de compras (CU15).
    
    Identificado unívocamente por la combinación: id_carrito + id_producto + talla + color.
    """

    __tablename__ = "items_carrito"
    __table_args__ = (
        UniqueConstraint(
            "id_carrito", "id_producto", "talla", "color",
            name="uq_item_carrito_variante",
        ),
        CheckConstraint("cantidad > 0", name="cantidad_item_carrito_positiva"),
    )

    id_item: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_carrito: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("carritos.id_carrito", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_producto: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("productos.id_producto", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    talla: Mapped[str | None] = mapped_column(String(20), nullable=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    id_sucursal_preferida: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fecha_agregado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones
    carrito: Mapped["Carrito"] = relationship(back_populates="items")
    producto: Mapped["Producto"] = relationship(lazy="selectin")  # noqa: F821
    sucursal_preferida: Mapped["Sucursal | None"] = relationship(lazy="selectin")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<ItemCarrito(id={self.id_item}, producto={self.id_producto}, "
            f"talla={self.talla!r}, color={self.color!r}, cant={self.cantidad})>"
        )

