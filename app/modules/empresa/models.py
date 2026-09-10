# backend/app/modules/empresa/models.py
# Paquete "Gestión Empresa" — CU16 Empresa, CU17 Sucursales
from datetime import datetime
from typing import List

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Empresa(Base):
    """Empresa del grupo (CU16) — matriz a la que pertenecen las sucursales.

    NIT (Número de Identificación Tributaria) identifica a la empresa ante
    Impuestos; email/ciudad/logo_url completan el perfil institucional que
    muestra la vista de configuración de Angular.
    """

    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    razon_social: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ciudad: Mapped[str | None] = mapped_column(String(100), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relaciones
    sucursales: Mapped[List["Sucursal"]] = relationship(
        back_populates="empresa", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Empresa(id={self.id}, razon_social={self.razon_social!r})>"


class Sucursal(Base):
    """Sucursal de la empresa (CU17) — punto físico de venta / almacén."""

    __tablename__ = "sucursales"
    __table_args__ = (
        UniqueConstraint("empresa_id", "nombre", name="uq_sucursal_empresa_nombre"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    empresa_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("empresas.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relaciones
    empresa: Mapped["Empresa"] = relationship(back_populates="sucursales", lazy="joined")

    def __repr__(self) -> str:
        return f"<Sucursal(id={self.id}, nombre={self.nombre!r})>"
