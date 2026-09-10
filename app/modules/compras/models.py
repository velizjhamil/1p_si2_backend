# backend/app/modules/compras/models.py
# Paquete "Gestión Compra" — CU23 Proveedores
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Proveedor(Base):
    """Proveedor de mercadería (CU23) — persona/jurídica que abastece a la empresa."""

    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # "Gestión Compra" depende de "Gestión Empresa": cada proveedor se registra
    # asociado a la sucursal que gestiona su relación comercial.
    sucursal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sucursales.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relaciones
    sucursal: Mapped["Sucursal"] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<Proveedor(id={self.id}, nombre={self.nombre!r}, ruc={self.ruc!r})>"
