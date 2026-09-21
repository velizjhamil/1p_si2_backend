# backend/app/modules/compras/models.py
# Paquete "Gestión Compra" — CU23 Proveedores
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Proveedor(Base):
    """Proveedor de mercadería (CU23) — abastece a la empresa.

    Forma final según CU23: id_proveedor PK, nombre (razón social),
    nit_rut único, contacto_operativo, telefono, correo, categoria (línea
    que abastece — filtro del listado), estado de 3 valores
    ('Activo' | 'Verificado' | 'Inactivo') y sucursal de gestión
    OPCIONAL (ubicación para el filtro por ciudad).
    """

    __tablename__ = "proveedores"
    __table_args__ = (
        # estado de 3 valores del CU23 (reemplaza al booleano is_active)
        CheckConstraint("estado IN ('Activo', 'Verificado', 'Inactivo')", name="estado_valido"),
    )

    id_proveedor: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Razón social / nombre del proveedor
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    # NIT (Bolivia) o RUT (Chile) — identificador tributario único
    nit_rut: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    contacto_operativo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    correo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # Línea/categoría que abastece (ej: Textiles, Calzado) — filtro del GET
    categoria: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="Activo")
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "Gestión Compra" depende de "Gestión Empresa": sucursal que gestiona la
    # relación comercial. OPCIONAL — un proveedor puede ser de la empresa
    # matriz sin sucursal asignada (filtro por ciudad del listado).
    sucursal_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal"),
        nullable=True,
        index=True,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relaciones
    sucursal: Mapped["Sucursal"] = relationship(lazy="joined")

    @property
    def sucursal_nombre(self) -> str | None:
        return self.sucursal.nombre if self.sucursal else None

    def __repr__(self) -> str:
        return f"<Proveedor(id_proveedor={self.id_proveedor}, nombre={self.nombre!r}, nit_rut={self.nit_rut!r})>"
