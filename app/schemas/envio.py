# backend/app/schemas/envio.py
# Esquemas Pydantic para CU18 - Gestion de Envio.
#
# Las constantes de estados/transiciones/roles viven en
# app/modules/delivery/models.py (fuente unica); aca solo se re-exportan.
# Las reglas que dependen de la DB o del estado actual (transicion valida,
# repartidor con rol D, fecha futura, sucursal existente) se validan en el
# service; aca solo se valida la forma del payload.
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.delivery.models import (  # noqa: F401  (re-export)
    ESTADOS_ENVIO,
    ESTADOS_TERMINALES_ENVIO,
    ROLES_GESTION_ENVIO,
    TRANSICIONES_ENVIO,
)

# Estados que se pueden pedir por PATCH /{id}/estado. El resto tiene su
# endpoint propio porque exige datos adicionales:
#   LISTO_ENVIO     -> /confirmar-preparacion (sucursal)
#   ASIGNADO        -> /asignar (repartidor)
#   INTENTO_FALLIDO -> /intento-fallido (motivo)
#   REPROGRAMADO    -> /reprogramar (nueva fecha)
EstadoEnvioDirecto = Literal["EN_RUTA", "ENTREGADO", "CANCELADO"]


def _limpiar(valor: str | None) -> str | None:
    """Recorta espacios; un texto vacio pasa a None. Un NUL (0x00) se rechaza
    (422): PostgreSQL no lo admite en texto y llegaria como un 500."""
    if valor is None:
        return None
    if "\x00" in valor:
        raise ValueError("El texto no puede contener el caracter NUL (0x00).")
    valor = valor.strip()
    return valor or None


# ---------------------------------------------------------------------------
# Payloads (entrada)
# ---------------------------------------------------------------------------
class EnvioCreatePayload(BaseModel):
    """Payload de POST /api/v1/envios (iniciar envio manualmente).

    El flujo normal NO lo usa: el checkout online crea el envio solo. Sirve
    para ventas a DOMICILIO anteriores a CU18 que no tienen envio.
    """

    id_venta: int = Field(gt=0, description="Id de la venta a domicilio")
    observacion: str | None = Field(default=None, max_length=500)

    _v_obs = field_validator("observacion")(_limpiar)


class ConfirmarPreparacionPayload(BaseModel):
    """PATCH /{id}/confirmar-preparacion: PREPARANDO -> LISTO_ENVIO.

    `codigo_sucursal` fija la sucursal responsable del despacho (el
    inventario/local desde el que se preparo el paquete).
    """

    codigo_sucursal: int = Field(gt=0, description="Sucursal que despacha el paquete")
    observacion: str | None = Field(default=None, max_length=500)

    _v_obs = field_validator("observacion")(_limpiar)


class AsignarEnvioPayload(BaseModel):
    """PATCH /{id}/asignar: LISTO_ENVIO -> ASIGNADO con repartidor O agencia.

    Se indica EXACTAMENTE UNO de:
    - `id_repartidor` (CU18): usuario activo con rol D (lo valida el service).
      Contrato original, sin cambios.
    - `id_agencia` (CU19): agencia de reparto externa. Exige `peso_kg` y
      `volumen_m3` (> 0, hasta 3 decimales): con ellos el service cotiza y
      guarda la tarifa aplicada y el costo de agencia en el envio.
    Repartidor y agencia son mutuamente excluyentes (tambien lo garantiza un
    CHECK en la DB). `peso_kg`/`volumen_m3` solo tienen sentido con agencia.
    La fecha estimada es opcional; si viene debe ser futura.
    """

    id_repartidor: UUID | None = None
    id_agencia: int | None = Field(default=None, gt=0)
    peso_kg: Decimal | None = Field(
        default=None, gt=0, max_digits=10, decimal_places=3, allow_inf_nan=False
    )
    volumen_m3: Decimal | None = Field(
        default=None, gt=0, max_digits=10, decimal_places=3, allow_inf_nan=False
    )
    fecha_estimada_entrega: datetime | None = None
    observacion: str | None = Field(default=None, max_length=500)

    _v_obs = field_validator("observacion")(_limpiar)

    @model_validator(mode="after")
    def _repartidor_o_agencia(self) -> "AsignarEnvioPayload":
        if self.id_repartidor is None and self.id_agencia is None:
            raise ValueError("Indique 'id_repartidor' o 'id_agencia'.")
        if self.id_repartidor is not None and self.id_agencia is not None:
            raise ValueError(
                "'id_repartidor' e 'id_agencia' son mutuamente excluyentes: indique solo uno."
            )
        if self.id_agencia is not None:
            if self.peso_kg is None or self.volumen_m3 is None:
                raise ValueError("Con 'id_agencia' son obligatorios 'peso_kg' y 'volumen_m3'.")
        elif self.peso_kg is not None or self.volumen_m3 is not None:
            raise ValueError("'peso_kg' y 'volumen_m3' solo se admiten junto con 'id_agencia'.")
        return self


class CambiarEstadoPayload(BaseModel):
    """PATCH /{id}/estado: EN_RUTA | ENTREGADO | CANCELADO.

    La transicion se valida contra el estado actual en el service (409 si
    no es valida). CANCELADO exige `observacion` (motivo).
    """

    estado: EstadoEnvioDirecto
    observacion: str | None = Field(default=None, max_length=500)

    _v_obs = field_validator("observacion")(_limpiar)

    @model_validator(mode="after")
    def _cancelacion_con_motivo(self) -> "CambiarEstadoPayload":
        if self.estado == "CANCELADO" and not self.observacion:
            raise ValueError("Para cancelar el envio debe indicar el motivo (observacion).")
        return self


class IntentoFallidoPayload(BaseModel):
    """PATCH /{id}/intento-fallido: EN_RUTA -> INTENTO_FALLIDO."""

    motivo: str = Field(
        min_length=5,
        max_length=255,
        description="Ej: direccion no encontrada, cliente no responde",
    )
    observacion: str | None = Field(default=None, max_length=500)

    @field_validator("motivo")
    @classmethod
    def _motivo_no_vacio(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("El motivo debe tener al menos 5 caracteres.")
        return v

    _v_obs = field_validator("observacion")(_limpiar)


class ReprogramarEnvioPayload(BaseModel):
    """PATCH /{id}/reprogramar: INTENTO_FALLIDO -> REPROGRAMADO.

    La nueva fecha debe ser futura; se valida en el service porque depende
    de si el datetime llega con zona horaria o sin ella.
    """

    nueva_fecha_entrega: datetime
    observacion: str | None = Field(default=None, max_length=500)

    _v_obs = field_validator("observacion")(_limpiar)


# ---------------------------------------------------------------------------
# Respuestas (salida)
# ---------------------------------------------------------------------------
class EnvioEntregaResponse(BaseModel):
    """Datos de entrega: snapshot de la venta (no se duplica en el envio)."""

    nombre_cliente: str
    correo: str
    telefono: str
    direccion: str
    ciudad: str
    referencia: str | None = None


class EnvioItemResponse(BaseModel):
    """Prenda incluida en el paquete (para el checklist de preparacion)."""

    producto_id: int
    nombre: str
    talla: str | None = None
    color: str | None = None
    cantidad: int


class EnvioResponse(BaseModel):
    """Envio con datos del pedido, entrega, repartidor y sucursal."""

    model_config = ConfigDict(from_attributes=True)

    id_envio: int
    id_venta: int
    codigo_venta: str
    estado: str
    # Estados a los que se puede pasar desde el actual (guia para el front).
    transiciones_permitidas: list[str] = Field(default_factory=list)

    cliente_id: UUID
    cliente_nombre: str
    total_venta: Decimal
    datos_entrega: EnvioEntregaResponse
    items: list[EnvioItemResponse] = Field(default_factory=list)

    codigo_sucursal: int | None = None
    sucursal_nombre: str | None = None
    repartidor_id: UUID | None = None
    repartidor_nombre: str | None = None
    # CU19: agencia de reparto (alternativa al repartidor propio).
    agencia_id: int | None = None
    agencia_nombre: str | None = None
    # CU19, solo ASU/GS (costo INTERNO de logistica; nunca se muestra al cliente):
    costo_agencia: Decimal | None = None
    id_tarifa_aplicada: int | None = None
    peso_kg: Decimal | None = None
    volumen_m3: Decimal | None = None

    fecha_estimada_entrega: datetime | None = None
    fecha_entrega_real: datetime | None = None
    motivo_fallo: str | None = None
    fecha_reprogramacion: datetime | None = None
    intentos_fallidos: int = 0
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class EnvioHistorialResponse(BaseModel):
    """Una fila de la bitacora del envio (orden cronologico)."""

    model_config = ConfigDict(from_attributes=True)

    id_historial: int
    id_envio: int
    estado_anterior: str | None = None
    estado_nuevo: str
    id_usuario: UUID | None = None
    usuario_nombre: str | None = None
    observacion: str | None = None
    fecha: datetime


class RepartidorResponse(BaseModel):
    """Usuario rol D disponible para asignar (GET /envios/repartidores)."""

    id_usuario: UUID
    nombre: str
    correo: str
    # Envios ASIGNADO/EN_RUTA/REPROGRAMADO: ayuda a repartir la carga.
    envios_activos: int = 0
