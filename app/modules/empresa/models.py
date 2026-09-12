# backend/app/modules/empresa/models.py
# Paquete "Gestión Empresa" — CU16 Empresa, CU17 Sucursales, catálogo Ciudades
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


class Ciudad(Base):
    """Ciudad del catálogo (CU17) — donde operan las sucursales.

    Catálogo simple: nombre único + departamento (contexto geográfico de la
    empresa boliviana). El dropdown del formulario de sucursales se alimenta
    desde GET /api/v1/ciudades.
    """

    __tablename__ = "ciudades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    departamento: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relaciones
    sucursales: Mapped[List["Sucursal"]] = relationship(
        back_populates="ciudad", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Ciudad(id={self.id}, nombre={self.nombre!r})>"


class Sucursal(Base):
    """Sucursal de la empresa (CU17) — punto físico de venta / almacén.

    PK `codigo_sucursal` (entero autoincremental): identificador operativo
    mostrado al usuario (ej: "Sucursal 3") y clave de los endpoints
    /api/v1/sucursales/{codigo_sucursal}.
    """

    __tablename__ = "sucursales"
    __table_args__ = (
        UniqueConstraint("empresa_id", "nombre", name="uq_sucursal_empresa_nombre"),
    )

    codigo_sucursal: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    empresa_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("empresas.id"), nullable=False, index=True
    )
    # FK a Ciudad (CU17): cada sucursal opera en una ciudad del catálogo.
    id_ciudad: Mapped[int] = mapped_column(
        Integer, ForeignKey("ciudades.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Horario de atención (texto libre): ej "Lun-Sáb 09:00-20:00"
    horario_atencion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # estado Activo/Inactivo (CU17) mapeado como booleano is_active
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relaciones
    empresa: Mapped["Empresa"] = relationship(back_populates="sucursales", lazy="joined")
    ciudad: Mapped["Ciudad"] = relationship(back_populates="sucursales", lazy="joined")

    def __repr__(self) -> str:
        return f"<Sucursal(codigo_sucursal={self.codigo_sucursal}, nombre={self.nombre!r})>"
