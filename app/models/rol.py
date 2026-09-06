# backend/app/models/rol.py
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Rol(Base):
    __tablename__ = "roles"

    id_rol: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    nombre_rol: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )

    # Relación inversa: usuarios asignados a este rol
    usuarios: Mapped[list["Usuario"]] = relationship(  # noqa: F821
        back_populates="rol"
    )
