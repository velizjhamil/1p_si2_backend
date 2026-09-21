# backend/app/schemas/inventario.py
# Esquemas Pydantic para CU22 — Gestión de Inventario (kardex).
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Tipos de movimiento válidos del kardex (CU22)
TIPOS_MOVIMIENTO = ("ENTRADA", "SALIDA", "AJUSTE")


class MovimientoCreatePayload(BaseModel):
    """Payload de POST /api/v1/inventario/movimientos.

    - cantidad: ENTRADA/SALIDA exigen > 0; AJUSTE admite 0 (fija el stock
      total del producto al valor dado, nunca negativo).
    - id_producto: se valida existencia en el router (422 si no existe).
    - tipo: validado aquí (422 automático si no es ENTRADA/SALIDA/AJUSTE).
    - id_usuario: NO viene en el payload — se toma del token de la sesión.
    - id_sucursal: opcional en el payload; si es GS, se toma de su token.
    """

    id_producto: int = Field(gt=0)
    tipo: str
    cantidad: int = Field(ge=0)
    motivo: str | None = Field(default=None, max_length=255)
    id_sucursal: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validar_tipo_y_cantidad(self) -> "MovimientoCreatePayload":
        if self.tipo not in TIPOS_MOVIMIENTO:
            raise ValueError(
                f"Tipo de movimiento inválido. Valores permitidos: {', '.join(TIPOS_MOVIMIENTO)}."
            )
        if self.tipo in ("ENTRADA", "SALIDA") and self.cantidad <= 0:
            raise ValueError(
                "La cantidad debe ser mayor a cero para ENTRADA/SALIDA "
                "(use AJUSTE para fijar el stock en un valor exacto)."
            )
        return self


class UsuarioMovimientoDetalle(BaseModel):
    """Usuario embebido en la respuesta (quién registró el movimiento)."""

    model_config = ConfigDict(from_attributes=True)

    id_usuario: UUID
    nombre: str
    apellido: str | None = None
    correo: str


class ProductoMovimientoDetalle(BaseModel):
    """Producto embebido en la respuesta del movimiento."""

    model_config = ConfigDict(from_attributes=True)

    id_producto: int
    nombre: str
    categoria: str | None = None
    estado: str
    stock_total: int


class MovimientoResponse(BaseModel):
    """Respuesta detallada: movimiento + producto + usuario."""

    model_config = ConfigDict(from_attributes=True)

    id_movimiento: int
    tipo: str
    cantidad: int
    stock_anterior: int
    stock_nuevo: int
    motivo: str | None = None
    fecha_movimiento: datetime
    id_sucursal: int | None = None
    sucursal_nombre: str | None = None
    producto: ProductoMovimientoDetalle
    usuario: UsuarioMovimientoDetalle


class StockProductoResponse(BaseModel):
    """Fila del GET /api/v1/inventario/stock: stock actual + alerta."""

    model_config = ConfigDict(from_attributes=True)

    id_producto: int
    nombre: str
    categoria: str | None = None
    estado: str
    stock_total: int
    umbral_minimo: int
    nivel: str  # 'CRITICO' | 'BAJO' | 'OK'
    id_sucursal: int | None = None
    sucursal_nombre: str | None = None


class StockAlertResponse(BaseModel):
    """Productos con stock por debajo del umbral mínimo (stock < umbral)."""

    model_config = ConfigDict(from_attributes=True)

    productos: list[StockProductoResponse] = Field(default_factory=list)
    umbral_minimo: int
    total_alerta: int
