# backend/app/schemas/carrito.py
# CU15 — Esquemas Pydantic para la gestión persistente del Carrito de Compras.
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ItemCarritoCreate(BaseModel):
    """Payload para agregar un producto/variante al carrito."""
    id_producto: int = Field(..., description="ID del producto a agregar")
    cantidad: int = Field(1, ge=1, description="Cantidad a agregar (mínimo 1)")
    talla: Optional[str] = Field(None, max_length=20, description="Talla de la prenda (ej: 'M', '38')")
    color: Optional[str] = Field(None, max_length=50, description="Color de la prenda (ej: 'Azul', 'Negro')")
    id_sucursal_preferida: Optional[int] = Field(
        None, description="Sucursal física elegida para verificar disponibilidad/retiro"
    )


class ItemCarritoUpdate(BaseModel):
    """Payload para actualizar cantidad o sucursal de un ítem existente."""
    cantidad: int = Field(..., ge=1, description="Nueva cantidad (mínimo 1)")
    id_sucursal_preferida: Optional[int] = Field(
        None, description="Actualizar sucursal física elegida"
    )


class SincronizarCarritoItem(BaseModel):
    """Ítem local a sincronizar."""
    id_producto: int
    cantidad: int = Field(1, ge=1)
    talla: Optional[str] = None
    color: Optional[str] = None
    id_sucursal_preferida: Optional[int] = None


class SincronizarCarritoPayload(BaseModel):
    """Payload para sincronizar múltiples ítems del carrito local a la base de datos."""
    items: List[SincronizarCarritoItem] = Field(default_factory=list)
    reemplazar: bool = Field(
        False,
        description="Si True, reemplaza los ítems del carrito actual; si False, los fusiona (merge)"
    )


class ItemCarritoResponse(BaseModel):
    """Detalle de una línea en el carrito con validación de stock en tiempo real."""
    model_config = ConfigDict(from_attributes=True)

    id_item: int
    id_producto: int
    nombre_producto: str
    imagen_url: Optional[str] = None
    precio_unitario: float
    cantidad: int
    subtotal: float
    talla: Optional[str] = None
    color: Optional[str] = None
    id_sucursal_preferida: Optional[int] = None
    nombre_sucursal_preferida: Optional[str] = None
    stock_disponible_sucursal: Optional[int] = None
    stock_total_disponible: int
    disponible: bool = Field(
        True, description="Indica si hay stock suficiente en la sucursal o a nivel global"
    )
    fecha_agregado: Optional[datetime] = None


class CarritoResponse(BaseModel):
    """Resumen completo del carrito de compras del cliente."""
    id_carrito: int
    items: List[ItemCarritoResponse]
    cantidad_total_items: int
    subtotal: float
    costo_envio: float
    total: float
    todos_disponibles: bool
    advertencias: List[str] = Field(default_factory=list)
