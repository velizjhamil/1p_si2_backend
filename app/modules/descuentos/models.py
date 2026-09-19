# backend/app/modules/descuentos/models.py
# CU12 — Gestion de Descuentos / Cupones: una sola tabla unifica reglas
# automaticas (sin codigo) y cupones tipeables (con codigo unico).
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Descuento(Base):
    """Descuento o cupon del CU12.

    Una regla AUTOMATICA (ej. 15% off en toda la tienda en temporada)
    tiene `codigo=NULL`. Un CUPON tipeable por el Cliente (ej. BIENVENIDO20)
    tiene `codigo` unico no-NULL.

    Decisiones de modelo:
    - `tipo=PORCENTAJE` -> `valor` se interpreta como 0-100 (no como 0-1).
    - `tipo=MONTO_FIJO` -> `valor` se interpreta como Bs.
    - `fecha_fin=NULL` significa "sin vencimiento" (cupon permanente).
    - `usos_maximos=NULL` significa "ilimitado". Si `usos_actuales >= usos_maximos`
      el cupon se considera agotado.
    - `monto_minimo_compra=NULL` significa "no requiere compra minima".
    - `activo=False` deshabilita manualmente sin eliminar la fila.
    """

    __tablename__ = "descuentos"
    __table_args__ = (
        # El CHECK de tipo y valor>0 esta duplicado en la migracion (defensa
        # en profundidad: DB no depende de Pydantic para invariantes basicas).
        # Validaciones de rango (PORCENTAJE <= 100) y fecha_fin >= fecha_inicio
        # viven en Pydantic porque la constraint CHECK no permite comparar
        # dos columnas sin trigger.
    )

    id_descuento: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Codigo del cupon (NULL para reglas automaticas). Unico cuando viene.
    codigo: Mapped[str | None] = mapped_column(
        String(30), nullable=True, unique=True, index=True
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tipo: Mapped[str] = mapped_column(String(15), nullable=False)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    usos_maximos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usos_actuales: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    monto_minimo_compra: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Descuento(id={self.id_descuento}, codigo={self.codigo!r}, "
            f"tipo={self.tipo}, valor={self.valor})>"
        )
