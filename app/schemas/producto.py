# backend/app/schemas/producto.py
# Esquemas Pydantic para CU6 — Gestión de Productos de Ropa.
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Estados válidos del producto (ciclo de vida comercial, no stock del CU22)
ESTADOS_PRODUCTO = ("Activo", "Inactivo", "Agotado")


class CategoriaDetalle(BaseModel):
    """Categoría embebida en la respuesta del producto (sin línea)."""

    model_config = ConfigDict(from_attributes=True)

    id_categoria: int
    nombre: str
    linea: str


class TallaDetalle(BaseModel):
    """Talla embebida en la respuesta del producto."""

    model_config = ConfigDict(from_attributes=True)

    id_talla: int
    nombre_talla: str


class ColorDetalle(BaseModel):
    """Color embebido en la respuesta del producto."""

    model_config = ConfigDict(from_attributes=True)

    id_color: int
    nombre_color: str
    codigo_hex: str


class ProductoResponse(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/productos.

    Incluye el detalle de la categoría y los arrays de tallas y colores
    asociados (resueltos desde las tablas pivote N:M).
    """

    model_config = ConfigDict(from_attributes=True)

    id_producto: int
    nombre: str
    id_categoria: int
    categoria: CategoriaDetalle | None = None
    id_proveedor: int | None = None
    nombre_proveedor: str | None = None
    precio_venta: Decimal
    stock_total: int
    imagen_url: str | None = None
    descripcion: str | None = None
    estado: str
    tallas: list[TallaDetalle] = Field(default_factory=list)
    colores: list[ColorDetalle] = Field(default_factory=list)
    fecha_creacion: datetime | None = None


class ProductoCreatePayload(BaseModel):
    """Payload de POST /api/v1/productos.

    Validación: precio > 0, stock >= 0, y listados de IDs de tallas/colores
    (se validan existencia y duplicados en el router contra los catálogos).
    """

    nombre: str = Field(min_length=3, max_length=150)
    id_categoria: int = Field(gt=0)
    id_proveedor: int | None = Field(default=None, gt=0)
    precio_venta: float = Field(gt=0)
    stock_total: int = Field(ge=0)
    imagen_url: str | None = Field(default=None, max_length=500)
    descripcion: str | None = Field(default=None, max_length=500)
    tallas: list[int] = Field(default_factory=list)
    colores: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sin_duplicados(self) -> "ProductoCreatePayload":
        if len(self.tallas) != len(set(self.tallas)):
            raise ValueError("La lista de tallas contiene IDs duplicados.")
        if len(self.colores) != len(set(self.colores)):
            raise ValueError("La lista de colores contiene IDs duplicados.")
        return self


class ProductoUpdatePayload(BaseModel):
    """Payload parcial de PUT /api/v1/productos/{id}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que el resto).
    Los listados de IDs solo se reemplazan si vienen explícitos.
    """

    nombre: str | None = Field(default=None, min_length=3, max_length=150)
    id_categoria: int | None = Field(default=None, gt=0)
    id_proveedor: int | None = Field(default=None, gt=0)
    precio_venta: float | None = Field(default=None, gt=0)
    stock_total: int | None = Field(default=None, ge=0)
    imagen_url: str | None = Field(default=None, max_length=500)
    descripcion: str | None = Field(default=None, max_length=500)
    estado: str | None = Field(default=None)
    tallas: list[int] | None = None
    colores: list[int] | None = None

    @model_validator(mode="after")
    def _validar_estado(self) -> "ProductoUpdatePayload":
        if self.estado is not None and self.estado not in ESTADOS_PRODUCTO:
            raise ValueError(
                f"Estado inválido. Valores permitidos: {', '.join(ESTADOS_PRODUCTO)}."
            )
        return self

    @model_validator(mode="after")
    def _sin_duplicados(self) -> "ProductoUpdatePayload":
        if self.tallas is not None and len(self.tallas) != len(set(self.tallas)):
            raise ValueError("La lista de tallas contiene IDs duplicados.")
        if self.colores is not None and len(self.colores) != len(set(self.colores)):
            raise ValueError("La lista de colores contiene IDs duplicados.")
        return self
