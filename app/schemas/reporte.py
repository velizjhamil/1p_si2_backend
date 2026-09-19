# backend/app/schemas/reporte.py
# CU20 - Gestion de Reportes (panel ejecutivo para ASU/GS).
#
# Solo DTOs de salida: este modulo es READ-ONLY puro, no crea ni muta
# datos. Los schemas son la frontera entre la query agregada SQL y la
# respuesta JSON que consume el front.
#
# Decisiones de diseno:
# - Tipos de reporte como Literal (alineado con el front).
# - `fecha` se serializa como `date` ISO (YYYY-MM-DD), no datetime, para
#   que el front lo pueda mostrar directo en la columna "Dia".
# - `metodos_pago` y `tipos_venta` son dicts {nombre: total} en vez de
#   listas de filas. Asi el front renderiza columnas estaticas sin
#   necesitar mapear headers dinamicamente.
# - `productos_bajo_stock` y `productos_agotados` incluyen solo campos
#   que el front necesita para la tabla (id, nombre, stock, precio,
#   estado, categoria). Sin descripcion ni imagen_url para no inflar.
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


TipoReporteStr = Literal["ventas", "inventario", "rendimiento-vendedores"]


# ---------------------------------------------------------------------------
# Reporte de Ventas
# ---------------------------------------------------------------------------
class DesglosePagosTipo(BaseModel):
    """Sub-totales por metodo de pago y tipo de venta (ONLINE/POS).

    `metodos_pago` y `tipos_venta` son dicts {clave: total} para que el
    front renderice columnas estaticas (QR / EFECTIVO / TARJETA).
    """

    metodos_pago: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Total de ingresos por metodo de pago (QR/EFECTIVO/TARJETA).",
    )
    tipos_venta: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Total de ingresos por tipo de venta (ONLINE/POS).",
    )
    operaciones_por_tipo: dict[str, int] = Field(
        default_factory=dict,
        description="Cantidad de operaciones por tipo de venta.",
    )


class VentaDiariaReporte(BaseModel):
    """Una fila del reporte: las metricas agregadas de UN dia.

    `ticket_promedio = total_ingresos / total_operaciones` (0 si no hay ops).
    """

    fecha: date = Field(description="Dia de las operaciones (YYYY-MM-DD).")
    total_ingresos: Decimal = Field(description="Suma del campo `total` de las ventas del dia.")
    total_operaciones: int = Field(description="Cantidad de ventas del dia.")
    ticket_promedio: Decimal = Field(
        description="Promedio de ticket (total_ingresos / total_operaciones)."
    )
    desglose: DesglosePagosTipo = Field(
        description="Sub-totales por metodo de pago y tipo de venta."
    )


class VentasReporteResponse(BaseModel):
    """Envelope del reporte de ventas. Pagina por DIA, no por venta.

    Items: una fila por dia dentro del rango, ordenadas DESC por fecha.
    Totales: agregados de todo el rango (para tarjetas KPI del front).
    """

    items: list[VentaDiariaReporte]
    total_dias: int = Field(description="Cantidad de dias con operaciones en el rango.")
    total_ingresos: Decimal
    total_operaciones: int
    ticket_promedio: Decimal
    page: int
    limit: int
    pages: int
    fecha_desde: date
    fecha_hasta: date


# ---------------------------------------------------------------------------
# Reporte de Inventario
# ---------------------------------------------------------------------------
class ProductoStockItem(BaseModel):
    """Fila de la lista de productos con stock bajo o agotado.

    `estado` refleja el campo `estado` del modelo Producto (Activo /
    Inactivo / Agotado). `categoria` viene del join con categorias.
    """

    id_producto: int
    nombre: str
    stock_total: int
    precio_venta: Decimal
    valor_stock: Decimal = Field(
        description="stock_total * precio_venta (valorizacion a precio de venta)."
    )
    estado: str
    categoria: Optional[str] = None


class InventarioReporteResponse(BaseModel):
    """Snapshot del inventario al momento de la consulta.

    `productos_bajo_stock` se limita a `top_bajo_stock` filas (default 20)
    para no devolver listas enormes si la DB tiene miles de productos.
    Si el front necesita paginacion, lo agregamos despues.
    """

    total_productos: int = Field(description="Cantidad de productos en el catalogo (cualquier estado).")
    productos_activos: int
    total_stock_unidades: int = Field(description="Suma de stock_total de productos activos.")
    valor_inventario: Decimal = Field(
        description="Suma de (stock_total * precio_venta) de productos activos."
    )
    umbral_bajo_stock: int = Field(
        description="Umbral usado para clasificar 'bajo stock' (default 5)."
    )
    productos_bajo_stock: list[ProductoStockItem] = Field(
        description="Productos activos con stock_total <= umbral_bajo_stock."
    )
    productos_agotados: list[ProductoStockItem] = Field(
        description="Productos con stock_total = 0 o estado='Agotado'."
    )
    generado_en: datetime = Field(description="Timestamp de cuando se corrio la query.")


# ---------------------------------------------------------------------------
# Reporte de Rendimiento de Vendedores
# ---------------------------------------------------------------------------
class VendedorRendimientoItem(BaseModel):
    """Una fila del reporte: metricas agregadas de UN vendedor.

    `ultima_venta` puede ser None si el vendedor no tiene ventas en el
    rango. `ticket_promedio = 0` si `total_operaciones = 0` (no deberia
    pasar porque filtramos vendedores con >= 1 venta).
    """

    id_vendedor: str = Field(description="UUID del usuario vendedor.")
    nombre: str
    correo: str
    total_ventas: int = Field(description="Cantidad de ventas POS en el rango.")
    total_ingresos: Decimal = Field(description="Suma de `total` de sus ventas POS en el rango.")
    ticket_promedio: Decimal
    primera_venta: Optional[datetime] = None
    ultima_venta: Optional[datetime] = None
    tipos_venta: dict[str, int] = Field(
        default_factory=dict,
        description="Desglose de operaciones por tipo (ONLINE/POS).",
    )


class RendimientoVendedoresResponse(BaseModel):
    items: list[VendedorRendimientoItem]
    total_vendedores: int = Field(description="Cantidad de vendedores con >= 1 venta en el rango.")
    total_ingresos: Decimal
    total_operaciones: int
    fecha_desde: date
    fecha_hasta: date
