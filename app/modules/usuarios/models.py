# backend/app/modules/usuarios/models.py
# Paquete "Gestión Usuario" — CU1 Login, CU2 Logout, CU3 Usuarios, CU4 Roles, CU5 Permisos
# Mapea las tablas REALES de la base de datos tienda_ropa (UUID + columnas en español).
from datetime import datetime
from typing import List, TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.empresa.models import Sucursal

# ---------------------------------------------------------------------------
# Tabla de asociación N:M Rol <-> Permiso (herencia de permisos por rol).
# Creada por la primera migración de Alembic junto a la tabla permisos.
# El índice ix_rol_permiso_permiso_id cubre la búsqueda inversa
# "qué roles tienen el permiso X" (el PK compuesto solo indexa rol_id primero).
# ---------------------------------------------------------------------------
rol_permiso = Table(
    "rol_permiso",
    Base.metadata,
    Column("rol_id", UUID(as_uuid=True), ForeignKey("roles.id_rol", ondelete="CASCADE"), primary_key=True),
    Column("permiso_id", Integer, ForeignKey("permisos.id", ondelete="CASCADE"), primary_key=True, index=True),
)

# ---------------------------------------------------------------------------
# Tabla de asociación N:M Usuario <-> Permiso (permisos adicionales directos,
# heredados además de los de su rol).
# Creada por la primera migración de Alembic junto a la tabla permisos.
# ---------------------------------------------------------------------------
usuario_permiso = Table(
    "usuario_permiso",
    Base.metadata,
    Column("usuario_id", UUID(as_uuid=True), ForeignKey("usuarios.id_usuario", ondelete="CASCADE"), primary_key=True),
    Column("permiso_id", Integer, ForeignKey("permisos.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Rol(Base):
    """Rol de acceso (CU4) — valores reales en DB: ASU, C, D, GS, V.

    ASU = Administrador del Sistema, GS = Gestor de Sucursal, V = Vendedor,
    C = Cliente, D = Delivery. Hereda permisos vía rol_permiso (CU5).
    """

    __tablename__ = "roles"

    # Sin index=True en PKs: roles_pkey ya indexa id_rol y la DB real NO tiene
    # un índice ix_roles_id_rol (evita un índice duplicado y ruido en Alembic).
    # default=uuid.uuid4: el ORM genera el UUID ANTES del INSERT (la DB real
    # no tiene DEFAULT en la columna; sin esto todo POST de rol crashea con
    # NotNullViolation en id_rol).
    id_rol: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre_rol: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    # CU4: descripción legible del rol (ej: "Administrador del Sistema")
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones N:M / 1:N
    usuarios: Mapped[List["Usuario"]] = relationship(back_populates="rol", lazy="selectin")
    permisos: Mapped[List["Permiso"]] = relationship(
        secondary=rol_permiso, lazy="selectin", back_populates="roles"
    )

    @property
    def cantidad_usuarios(self) -> int:
        """Cantidad de usuarios con este rol (para listados CU4).

        Rol.usuarios ya viene cargada con lazy="selectin", así que esta
        property NO emite queries extra (decisión documentada para
        RolRead.cantidad_usuarios).
        """
        return len(self.usuarios)

    def __repr__(self) -> str:
        return f"<Rol(id_rol={self.id_rol}, nombre_rol={self.nombre_rol!r})>"


class Permiso(Base):
    """Permiso granular (CU5) — ej: usuarios.ver, ventas.crear.

    `modulo` agrupa los permisos por módulo del sistema (Usuarios, Ventas,
    Inventario, ...) para renderizar el sidebar y la matriz de permisos.
    """

    __tablename__ = "permisos"

    # PK SERIAL (autoincrement) — sin index=True redundante sobre el PK.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    descripcion: Mapped[str | None] = mapped_column(String(4095), nullable=True)
    modulo: Mapped[str] = mapped_column(String(100), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones N:M — la tabla física existe desde la primera migración
    roles: Mapped[List["Rol"]] = relationship(
        secondary=rol_permiso, lazy="selectin", back_populates="permisos"
    )
    usuarios: Mapped[List["Usuario"]] = relationship(
        secondary=usuario_permiso, lazy="selectin", back_populates="permisos"
    )

    def __repr__(self) -> str:
        return f"<Permiso(id={self.id}, nombre={self.nombre!r}, modulo={self.modulo!r})>"


class Usuario(Base):
    """Cuenta de acceso al sistema (CU3) — login/logout (CU1/CU2).

    Mapea la tabla real `usuarios` de la DB tienda_ropa: UUID, correo,
    estado, intentos_fallidos y bloqueado_hasta (bloqueo temporal CU1).
    """

    __tablename__ = "usuarios"

    # Sin index=True en la PK (usuarios_pkey ya indexa; la DB real no tiene
    # ix_usuarios_id_usuario).
    # default=uuid.uuid4: el ORM genera el UUID ANTES del INSERT — la tabla
    # real usuarios NO tiene DEFAULT en id_usuario, así que sin esto el POST
    # de creación mandaba NULL y PostgreSQL rechazaba con NotNullViolation.
    id_usuario: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correo: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # La columna física se llama `password` (hash bcrypt) — NUNCA texto plano
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Nombre explícito para coincidir con la constraint REAL de la DB
    # (fk_usuario_id_rol, creada antes de adoptar la naming convention).
    id_rol: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id_rol", name="fk_usuario_id_rol"),
        nullable=False,
        index=True,
    )
    # CU1: bloqueo temporal tras 5 intentos fallidos (columna real en DB)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # CU1: timestamp de la última conexión exitosa (se actualiza en login)
    ultima_conexion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # CU3: fecha de registro del usuario (columna agregada por migración step1)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # CU17 multi-sucursal: sucursal a la que pertenece el usuario (obligatorio para GS y V)
    id_sucursal: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relaciones
    rol: Mapped["Rol"] = relationship(back_populates="usuarios", lazy="selectin")
    sucursal: Mapped["Sucursal | None"] = relationship(
        "Sucursal", foreign_keys=[id_sucursal], back_populates="personal", lazy="joined"
    )
    # Permisos directos (adicionales a los heredados del rol)
    permisos: Mapped[List["Permiso"]] = relationship(
        secondary=usuario_permiso, lazy="selectin", back_populates="usuarios"
    )

    @property
    def sucursal_nombre(self) -> str | None:
        return self.sucursal.nombre if self.sucursal else None

    def __repr__(self) -> str:
        return f"<Usuario(id_usuario={self.id_usuario}, correo={self.correo!r})>"
