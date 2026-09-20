# backend/app/modules/delivery/models.py
# Paquete "Gestión Delivery" — CU18 Gestión de Envío.
#
# Reutiliza lo existente en lugar de duplicarlo:
# - El "pedido" es `Venta` (ventas.id_venta). La dirección de entrega NO se
#   copia: se lee del snapshot de la venta (direccion/ciudad/referencia).
# - El repartidor es un `Usuario` con rol D (Delivery).
# - La sucursal responsable del despacho es `Sucursal` (codigo_sucursal).
#
# CU19 agrega el catálogo GLOBAL de agencias de reparto externas (sin relación
# con sucursales), sus zonas de cobertura (ciudad + subzona opcional) y sus
# tarifas por PESO o VOLUMEN. Un envío se entrega con repartidor propio
# (id_repartidor) O con una agencia (id_agencia), nunca ambos.
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ---------------------------------------------------------------------------
# Estados y reglas del ciclo de vida (fuente única; el service las consume y
# el CHECK de la DB replica los valores).
# ---------------------------------------------------------------------------
ESTADOS_ENVIO = (
    "PREPARANDO",
    "LISTO_ENVIO",
    "ASIGNADO",
    "EN_RUTA",
    "ENTREGADO",
    "INTENTO_FALLIDO",
    "REPROGRAMADO",
    "CANCELADO",
)

# Estados terminales: cierran el flujo de despacho (no admiten más cambios).
# INTENTO_FALLIDO NO es terminal: permite reprogramar y continuar.
ESTADOS_TERMINALES_ENVIO = ("ENTREGADO", "CANCELADO")

# Transiciones válidas: estado actual -> estados destino permitidos.
TRANSICIONES_ENVIO: dict[str, tuple[str, ...]] = {
    "PREPARANDO": ("LISTO_ENVIO", "CANCELADO"),
    "LISTO_ENVIO": ("ASIGNADO", "CANCELADO"),
    "ASIGNADO": ("EN_RUTA", "CANCELADO"),
    "EN_RUTA": ("ENTREGADO", "INTENTO_FALLIDO"),
    "INTENTO_FALLIDO": ("REPROGRAMADO", "CANCELADO"),
    "REPROGRAMADO": ("EN_RUTA", "CANCELADO"),
    "ENTREGADO": (),
    "CANCELADO": (),
}

# Roles que gestionan envíos (mismo criterio de tuplas de roles que
# ROLES_POS / ROLES_PROCESAMIENTO). El cliente (C) solo consulta el suyo.
ROLES_GESTION_ENVIO = ("ASU", "GS", "D")

# Tipos de entrega de la venta (ventas.tipo_entrega).
TIPOS_ENTREGA = ("DOMICILIO", "RETIRO")

# CU19: criterio por el que una tarifa de agencia cobra (uno solo por tarifa).
CRITERIOS_TARIFA = ("PESO", "VOLUMEN")


def _in_check(columna: str, valores: tuple[str, ...]) -> str:
    """Arma `columna IN ('A', 'B', ...)` para los CHECK."""
    return f"{columna} IN ({', '.join(repr(v) for v in valores)})"


class Envio(Base):
    """Envío a domicilio de una venta online (CU18).

    Una venta con tipo_entrega='DOMICILIO' tiene exactamente un envío
    (UNIQUE en id_venta). El envío nace en PREPARANDO cuando el checkout
    registra la venta; en ese momento aún no se sabe qué sucursal despacha,
    por eso `codigo_sucursal` es NULL solo mientras está PREPARANDO (o si se
    cancela ahí). Desde LISTO_ENVIO el CHECK exige la sucursal responsable.
    """

    __tablename__ = "envios"
    __table_args__ = (
        CheckConstraint(_in_check("estado", ESTADOS_ENVIO), name="estado_envio_valido"),
        CheckConstraint("intentos_fallidos >= 0", name="intentos_no_negativos"),
        # Sucursal obligatoria una vez que el paquete sale de PREPARANDO.
        CheckConstraint(
            "estado IN ('PREPARANDO', 'CANCELADO') OR codigo_sucursal IS NOT NULL",
            name="sucursal_requerida_al_despachar",
        ),
        # CU19: repartidor propio O agencia, nunca ambos.
        CheckConstraint(
            "id_agencia IS NULL OR id_repartidor IS NULL",
            name="repartidor_o_agencia",
        ),
        # CU19: con agencia el envío guarda el snapshot completo de la
        # asignación (tarifa, costo, peso y volumen > 0).
        CheckConstraint(
            "id_agencia IS NULL OR (id_tarifa_aplicada IS NOT NULL "
            "AND costo_agencia IS NOT NULL AND peso_kg IS NOT NULL AND peso_kg > 0 "
            "AND volumen_m3 IS NOT NULL AND volumen_m3 > 0)",
            name="agencia_snapshot_completo",
        ),
        # CU19: sin agencia esos campos no se usan.
        CheckConstraint(
            "id_agencia IS NOT NULL OR (id_tarifa_aplicada IS NULL "
            "AND costo_agencia IS NULL AND peso_kg IS NULL AND volumen_m3 IS NULL)",
            name="datos_agencia_requieren_agencia",
        ),
        CheckConstraint(
            "costo_agencia IS NULL OR costo_agencia >= 0",
            name="costo_agencia_no_negativo",
        ),
    )

    id_envio: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Pedido: 1 envío por venta.
    id_venta: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ventas.id_venta"),
        nullable=False,
        unique=True,
        index=True,
    )
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PREPARANDO", index=True
    )
    # Sucursal responsable del despacho (se fija al confirmar preparación).
    codigo_sucursal: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sucursales.codigo_sucursal"),
        nullable=True,
        index=True,
    )
    # Repartidor asignado: Usuario con rol D (validado en el service).
    id_repartidor: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Fecha/hora estimada vigente (se actualiza al reprogramar).
    fecha_estimada_entrega: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fecha_entrega_real: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Motivo del ÚLTIMO intento fallido (el detalle de cada intento vive en
    # envio_historial, que nunca se sobrescribe).
    motivo_fallo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Momento en que se registró la última reprogramación (la nueva fecha
    # de entrega queda en fecha_estimada_entrega).
    fecha_reprogramacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    intentos_fallidos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # --- CU19: entrega por agencia externa (todo NULL con repartidor propio).
    # Sin ondelete (RESTRICT): no se borra una agencia/tarifa con envíos.
    id_agencia: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("agencias_reparto.id_agencia"),
        nullable=True,
        index=True,
    )
    # Snapshot de la asignación: tarifa aplicada, costo INTERNO de logística
    # (no es el costo cobrado al cliente, ventas.costo_envio) y medidas
    # digitadas por el GS al asignar.
    id_tarifa_aplicada: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agencia_tarifas.id_tarifa"), nullable=True
    )
    costo_agencia: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    peso_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    volumen_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relaciones (varias FKs a usuarios -> foreign_keys explícito).
    venta: Mapped["Venta"] = relationship(lazy="joined")  # noqa: F821
    sucursal: Mapped["Sucursal | None"] = relationship(lazy="joined")  # noqa: F821
    repartidor: Mapped["Usuario | None"] = relationship(  # noqa: F821
        "Usuario", foreign_keys="Envio.id_repartidor", lazy="joined"
    )
    # lazy="select": no altera las consultas existentes de CU18.
    agencia: Mapped["AgenciaReparto | None"] = relationship(lazy="select")
    historial: Mapped[list["EnvioHistorial"]] = relationship(
        back_populates="envio",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="EnvioHistorial.fecha, EnvioHistorial.id_historial",
    )

    def __repr__(self) -> str:
        return (
            f"<Envio(id_envio={self.id_envio}, venta={self.id_venta}, "
            f"estado={self.estado!r})>"
        )


class EnvioHistorial(Base):
    """Bitácora append-only de cambios del envío (CU18).

    Una fila por cambio de estado: no se actualiza ni borra, así el intento
    fallido anterior se conserva aunque el envío se reprograme.
    `estado_anterior` es NULL solo en la fila de creación.
    """

    __tablename__ = "envio_historial"
    __table_args__ = (
        CheckConstraint(
            _in_check("estado_nuevo", ESTADOS_ENVIO), name="estado_nuevo_valido"
        ),
        CheckConstraint(
            "estado_anterior IS NULL OR "
            + _in_check("estado_anterior", ESTADOS_ENVIO),
            name="estado_anterior_valido",
        ),
    )

    id_historial: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_envio: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("envios.id_envio", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    estado_anterior: Mapped[str | None] = mapped_column(String(20), nullable=True)
    estado_nuevo: Mapped[str] = mapped_column(String(20), nullable=False)
    # Responsable del cambio (NULL si el usuario se elimina después).
    id_usuario: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Motivo u observación (obligatorio para INTENTO_FALLIDO en el service).
    observacion: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    envio: Mapped["Envio"] = relationship(back_populates="historial")
    usuario: Mapped["Usuario | None"] = relationship(  # noqa: F821
        "Usuario", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<EnvioHistorial(id_envio={self.id_envio}, "
            f"{self.estado_anterior!r}->{self.estado_nuevo!r})>"
        )


class AgenciaReparto(Base):
    """Agencia de reparto externa autorizada por la marca (CU19).

    Catálogo GLOBAL de la empresa (sin sucursal). `is_active` es la
    habilitación: una agencia deshabilitada no se ofrece para nuevas
    asignaciones pero conserva sus envíos históricos. Los datos de
    facturación (NIT, razón social, correo y dirección fiscal) son
    obligatorios; su validación es solo de formato/consistencia local, no
    una verificación fiscal. `nit` se guarda normalizado y `razon_social`
    es única sin distinguir mayúsculas.
    """

    __tablename__ = "agencias_reparto"
    __table_args__ = (
        CheckConstraint("length(btrim(razon_social)) > 0", name="razon_social_no_vacia"),
        CheckConstraint("length(btrim(nit)) > 0", name="nit_no_vacio"),
        Index(
            "uq_agencias_reparto_razon_social_lower",
            func.lower(func.btrim(text("razon_social"))),
            unique=True,
        ),
    )

    id_agencia: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    razon_social: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    contacto_operativo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    correo: Mapped[str | None] = mapped_column(String(150), nullable=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Datos de facturación (obligatorios).
    correo_facturacion: Mapped[str] = mapped_column(String(150), nullable=False)
    direccion_fiscal: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), index=True
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    zonas: Mapped[list["AgenciaZona"]] = relationship(
        back_populates="agencia",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="AgenciaZona.id_zona",
    )

    def __repr__(self) -> str:
        return f"<AgenciaReparto(id_agencia={self.id_agencia}, nit={self.nit!r})>"


class AgenciaZona(Base):
    """Zona de cobertura de una agencia: ciudad del catálogo + subzona opcional.

    La ciudad se referencia por FK a `ciudades`. `nombre_zona` (barrio/zona)
    es opcional. El UNIQUE funcional trata NULL y cadena vacía como "toda la
    ciudad" y no distingue mayúsculas, así no hay coberturas duplicadas.
    """

    __tablename__ = "agencia_zonas"
    __table_args__ = (
        Index(
            "uq_agencia_zonas_cobertura",
            "id_agencia",
            "id_ciudad",
            func.lower(func.coalesce(func.btrim(text("nombre_zona")), "")),
            unique=True,
        ),
    )

    id_zona: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_agencia: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agencias_reparto.id_agencia", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_ciudad: Mapped[int] = mapped_column(
        Integer, ForeignKey("ciudades.id"), nullable=False, index=True
    )
    nombre_zona: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agencia: Mapped["AgenciaReparto"] = relationship(back_populates="zonas")
    ciudad: Mapped["Ciudad"] = relationship(lazy="joined")  # noqa: F821
    tarifas: Mapped[list["AgenciaTarifa"]] = relationship(
        back_populates="zona",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="AgenciaTarifa.criterio, AgenciaTarifa.rango_min",
    )

    def __repr__(self) -> str:
        return (
            f"<AgenciaZona(id_zona={self.id_zona}, agencia={self.id_agencia}, "
            f"ciudad={self.id_ciudad}, zona={self.nombre_zona!r})>"
        )


class AgenciaTarifa(Base):
    """Tarifa de una zona de cobertura por PESO (kg) o VOLUMEN (m3) (CU19).

    Un único `criterio` por tarifa. El rango es [rango_min, rango_max) y
    `rango_max` NULL es el último tramo abierto. Aplicable solo si
    `is_active` y la fecha actual está en [vigente_desde, vigente_hasta]
    (`vigente_hasta` NULL = sin fin). El solapamiento de rangos/vigencias
    dentro de agencia+zona+criterio lo valida el service (sin EXCLUDE ni
    extensiones en la DB). `costo` es un costo interno de logística.
    """

    __tablename__ = "agencia_tarifas"
    __table_args__ = (
        CheckConstraint(
            f"criterio IN ({', '.join(repr(c) for c in CRITERIOS_TARIFA)})",
            name="criterio_valido",
        ),
        CheckConstraint("rango_min >= 0", name="rango_min_no_negativo"),
        CheckConstraint(
            "rango_max IS NULL OR rango_max > rango_min", name="rango_max_mayor_que_min"
        ),
        CheckConstraint("costo >= 0", name="costo_no_negativo"),
        CheckConstraint(
            "vigente_hasta IS NULL OR vigente_hasta >= vigente_desde",
            name="vigencia_valida",
        ),
    )

    id_tarifa: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    id_zona: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agencia_zonas.id_zona", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    criterio: Mapped[str] = mapped_column(String(10), nullable=False)
    rango_min: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    rango_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    costo: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    vigente_desde: Mapped[date] = mapped_column(Date, nullable=False)
    vigente_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    zona: Mapped["AgenciaZona"] = relationship(back_populates="tarifas")

    def __repr__(self) -> str:
        return (
            f"<AgenciaTarifa(id_tarifa={self.id_tarifa}, zona={self.id_zona}, "
            f"{self.criterio} [{self.rango_min}, {self.rango_max}) = {self.costo})>"
        )
