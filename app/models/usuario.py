# backend/app/models/usuario.py
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    correo: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    # Hash bcrypt — nunca texto plano (regla backend.md #5)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    id_rol: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id_rol", name="fk_usuario_id_rol"),
        nullable=False,
        index=True,
    )
    # Bloqueo temporal tras N intentos fallidos (RNF01)
    intentos_fallidos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rol: Mapped["Rol"] = relationship(  # noqa: F821
        back_populates="usuarios", lazy="joined"
    )
