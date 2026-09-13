# backend/app/modules/inventario/models.py
# Paquete "Gestión Inventario" — CU6 Productos, CU7 Tallas/Colores,
# CU9 Categorías, CU22 Kardex (movimientos), CU24 Colecciones y Temporadas.
# (ModelosAR llega con su CU de ciclos posteriores.)
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# CU6 — Tablas pivote N:M producto <-> tallas / colores
# ---------------------------------------------------------------------------
# Tablas de asociación clásicas de SQLAlchemy (sin modelo de dominio): la
# relación N:M se gestiona desde `Producto` con secondary=...
producto_talla = Table(
    "producto_tallas",
    Base.metadata,
    Column(
        "id_producto",
        Integer,
        ForeignKey("productos.id_producto", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "id_talla",
        Integer,
        ForeignKey("tallas.id_talla", ondelete="CASCADE"),
        primary_key=True,
    ),
)

producto_color = Table(
    "producto_colores",
    Base.metadata,
    Column(
        "id_producto",
        Integer,
        ForeignKey("productos.id_producto", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "id_color",
        Integer,
        ForeignKey("colores.id_color", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Categoria(Base):
    """Categoría del catálogo (CU9) — clasificación de prendas por línea.

    `linea` segmenta el catálogo en Hombre / Mujer / Unisex (validado con
    CHECK en DB); `activo` habilita el soft delete: una categoría con
    productos asociados NO se elimina físicamente, solo se desactiva.
    """

    __tablename__ = "categorias"
    __table_args__ = (
        # Línea de prenda del CU9 (3 valores válidos)
        CheckConstraint("linea IN ('Hombre', 'Mujer', 'Unisex')", name="linea_valida"),
    )

    id_categoria: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    linea: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Categoria(id_categoria={self.id_categoria}, nombre={self.nombre!r}, linea={self.linea!r})>"


class Coleccion(Base):
    """Colección del catálogo (CU24) — línea de diseño que agrupa temporadas.

    Ej: "Línea Urbana", "Clásicos Atemporales". Simple catálogo con nombre
    único; las temporadas cuelgan de ella de forma OPCIONAL (una temporada
    puede ser general de la tienda sin colección específica).
    """

    __tablename__ = "colecciones"

    id_coleccion: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    nombre_coleccion: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones
    temporadas: Mapped[list["Temporada"]] = relationship(
        back_populates="coleccion", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Coleccion(id_coleccion={self.id_coleccion}, nombre={self.nombre_coleccion!r})>"


class Temporada(Base):
    """Temporada del catálogo (CU24) — ventana de vigencia de una colección.

    Regla de negocio: fecha_fin >= fecha_inicio (validada en el schema
    Pydantic y en el router). La vigencia (Vigente/Finalizada) se deriva
    de la fecha actual contra el rango [fecha_inicio, fecha_fin].
    """

    __tablename__ = "temporadas"

    id_temporada: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # FK OPCIONAL a colección (una temporada general no pertenece a ninguna)
    id_coleccion: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("colecciones.id_coleccion"),
        nullable=True,
        index=True,
    )
    nombre_temporada: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones
    coleccion: Mapped["Coleccion"] = relationship(
        back_populates="temporadas", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<Temporada(id_temporada={self.id_temporada}, "
            f"nombre={self.nombre_temporada!r}, {self.fecha_inicio}..{self.fecha_fin})>"
        )


class Talla(Base):
    """Talla del catálogo (CU7) — variante de tamaño de una prenda (XS, S...).

    `activo` habilita el soft delete: una talla asociada a variantes de
    producto NO se elimina físicamente (409 en el DELETE), solo se
    desactiva. La unicidad del nombre se garantiza con unique en DB.
    """

    __tablename__ = "tallas"

    id_talla: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    nombre_talla: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True
    )
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Talla(id_talla={self.id_talla}, nombre={self.nombre_talla!r})>"


class Color(Base):
    """Color del catálogo (CU7) — variante cromática con código HEX.

    `codigo_hex` valida el formato #RRGGBB en el schema Pydantic (regex)
    y es UNIQUE en DB. `activo` habilita el soft delete igual que Talla:
    un color con variantes asociadas responde 409 en el DELETE.
    """

    __tablename__ = "colores"

    id_color: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    nombre_color: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True
    )
    codigo_hex: Mapped[str] = mapped_column(
        String(7), nullable=False, unique=True, index=True
    )
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Color(id_color={self.id_color}, nombre={self.nombre_color!r}, hex={self.codigo_hex!r})>"


class Producto(Base):
    """Producto de ropa del catálogo (CU6) — prenda vendible.

    FKs a Categoria (obligatoria) y Proveedor (opcional: un producto puede
    fabricarse internamente). `estado` es el ciclo de vida comercial
    (Activo/Inactivo/Agotado, CHECK en DB) — independiente del stock, que
    se gestiona por sucursal en el CU22. Las tallas y colores disponibles
    cuelgan por las tablas pivote N:M producto_tallas / producto_colores.
    """

    __tablename__ = "productos"
    __table_args__ = (
        # Ciclo de vida comercial del producto (no es el stock del CU22)
        CheckConstraint(
            "estado IN ('Activo', 'Inactivo', 'Agotado')", name="estado_producto_valido"
        ),
        # Precio de venta al público siempre positivo
        CheckConstraint("precio_venta > 0", name="precio_venta_positivo"),
        # Stock total agregado (suma de sucursales) nunca negativo
        CheckConstraint("stock_total >= 0", name="stock_total_no_negativo"),
    )

    id_producto: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    # FK obligatoria: la categoría clasifica la prenda (CU9)
    id_categoria: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("categorias.id_categoria"),
        nullable=False,
        index=True,
    )
    # FK opcional: un producto puede fabricarse internamente (sin proveedor)
    id_proveedor: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("proveedores.id_proveedor"),
        nullable=True,
        index=True,
    )
    precio_venta: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    stock_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imagen_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    descripcion: Mapped[str | None] = mapped_column(String(500), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="Activo")
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
    categoria: Mapped["Categoria"] = relationship(lazy="joined")
    proveedor: Mapped["Proveedor | None"] = relationship(lazy="joined")
    tallas: Mapped[list["Talla"]] = relationship(
        secondary=producto_talla, lazy="selectin", order_by="Talla.id_talla"
    )
    colores: Mapped[list["Color"]] = relationship(
        secondary=producto_color, lazy="selectin", order_by="Color.id_color"
    )

    def __repr__(self) -> str:
        return (
            f"<Producto(id_producto={self.id_producto}, nombre={self.nombre!r}, "
            f"estado={self.estado!r}, stock={self.stock_total})>"
        )


class MovimientoInventario(Base):
    """Movimiento de stock (CU22) — kardex del inventario.

    Registra ENTRADA/SALIDA/AJUSTE sobre un producto con el snapshot del
    stock (stock_anterior -> stock_nuevo) para auditoría. La actualización
    de productos.stock_total ocurre en el MISMO request del POST (end-
    point) con SELECT FOR UPDATE — este registro es la evidencia del kardex.

    Semántica de `cantidad`:
    - ENTRADA/SALIDA: unidades movidas (siempre > 0).
    - AJUSTE: valor EXACTO al que se fija el stock total del producto
      (puede ser 0; la delta se calcula para el kardex como
      stock_nuevo - stock_anterior).
    """

    __tablename__ = "movimientos_inventario"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('ENTRADA', 'SALIDA', 'AJUSTE')", name="tipo_movimiento_valido"
        ),
        CheckConstraint("cantidad >= 0", name="cantidad_no_negativa"),
        CheckConstraint("stock_anterior >= 0", name="stock_anterior_no_negativo"),
        CheckConstraint("stock_nuevo >= 0", name="stock_nuevo_no_negativo"),
    )

    id_movimiento: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_producto: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("productos.id_producto"),
        nullable=False,
        index=True,
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_anterior: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_nuevo: Mapped[int] = mapped_column(Integer, nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fecha_movimiento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Usuario que registró el movimiento (auditoría del kardex)
    id_usuario: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id_usuario"), nullable=False, index=True
    )

    # Relaciones
    producto: Mapped["Producto"] = relationship(lazy="joined")
    usuario: Mapped["Usuario"] = relationship(lazy="joined")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<MovimientoInventario(id_movimiento={self.id_movimiento}, "
            f"tipo={self.tipo!r}, producto={self.id_producto}, "
            f"{self.stock_anterior}->{self.stock_nuevo})>"
        )
