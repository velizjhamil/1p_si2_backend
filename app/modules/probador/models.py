# backend/app/modules/probador/models.py
# Paquete "Probador Virtual" — CU8: fotos del cliente y simulaciones AR.
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


class FotoUsuario(Base):
    """Foto del cliente para el probador virtual (CU8).

    El frontend sube la imagen como Data URL (base64) que se persiste
    en url_imagen (TEXT); las medidas opcionales alimentan el motor de
    recomendación de talla. `complexion` se deriva del IMC de referencia
    (server-side, mismo criterio del mock del frontend).
    """

    __tablename__ = "fotos_usuario"
    __table_args__ = (
        CheckConstraint(
            "complexion IN ('DELGADA', 'MEDIA', 'ROBUSTA', 'NO_INDICADA')",
            name="complexion_valida",
        ),
    )

    id_foto: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_usuario: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id_usuario"), nullable=False, index=True
    )
    # Data URL de la imagen (base64) — el motor AR la procesa
    url_imagen: Mapped[str] = mapped_column(String(4_000_000), nullable=False)
    fecha_subida: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Medidas morfométricas opcionales (alimentan la recomendación)
    estatura_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peso_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complexion: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NO_INDICADA"
    )

    # Relaciones
    usuario: Mapped["Usuario"] = relationship(lazy="joined")  # noqa: F821
    simulaciones: Mapped[list["SimulacionProbador"]] = relationship(
        back_populates="foto", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<FotoUsuario(id_foto={self.id_foto}, usuario={self.id_usuario}, "
            f"complexion={self.complexion!r})>"
        )


class SimulacionProbador(Base):
    """Simulación AR del probador (CU8) — prenda probada sobre una foto.

    El motor (mock de IA) recomienda talla según la complexión derivada
    de las medidas de la foto y estima el ajuste comparando la talla
    elegida contra la recomendada. url_resultado apunta a la foto del
    cliente (la superposición AR la dibuja el visualizador frontend).
    """

    __tablename__ = "simulaciones_probador"
    __table_args__ = (
        CheckConstraint(
            "ajuste_estimado IN ('PERFECTO', 'AJUSTADO', 'HOLGADO')",
            name="ajuste_estimado_valido",
        ),
        CheckConstraint(
            "talla_elegida IS NOT NULL AND talla_recomendada IS NOT NULL",
            name="tallas_obligatorias",
        ),
    )

    id_simulacion: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_usuario: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id_usuario"), nullable=False, index=True
    )
    id_producto: Mapped[int] = mapped_column(
        Integer, ForeignKey("productos.id_producto"), nullable=False, index=True
    )
    id_foto: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fotos_usuario.id_foto", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Foto del cliente (la superposición AR la renderiza el frontend)
    url_resultado: Mapped[str] = mapped_column(String(4_000_000), nullable=False)
    talla_elegida: Mapped[str] = mapped_column(String(20), nullable=False)
    talla_recomendada: Mapped[str] = mapped_column(String(20), nullable=False)
    ajuste_estimado: Mapped[str] = mapped_column(String(20), nullable=False)
    # Variante probada (congelada para el lookbook)
    color_nombre: Mapped[str | None] = mapped_column(String(50), nullable=True)
    color_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    fecha_simulacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relaciones
    usuario: Mapped["Usuario"] = relationship(lazy="joined")  # noqa: F821
    foto: Mapped["FotoUsuario"] = relationship(back_populates="simulaciones")
    producto: Mapped["Producto"] = relationship(lazy="joined")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<SimulacionProbador(id_simulacion={self.id_simulacion}, "
            f"producto={self.id_producto}, talla={self.talla_elegida!r}, "
            f"ajuste={self.ajuste_estimado!r})>"
        )
