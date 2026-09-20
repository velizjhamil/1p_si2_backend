# backend/app/schemas/reporte.py
# CU20 - Gestion de Reportes (panel ejecutivo para ASU/GS).
#
# Solo DTOs de salida: este modulo es READ-ONLY puro, no crea ni muta
# datos. Los schemas son la frontera entre la query agregada SQL y la
# respuesta JSON que consume el front.
#
# Contenido:
# - DTOs de los 4 reportes nuevos (ventas, productos mas vendidos, inventario,
#   devoluciones): al final del archivo, se llenan desde app/modules/reportes/
#   service.py con `Modelo.model_validate(resultado)`.
# - DTOs de rendimiento-vendedores (contrato original, sin cambios).
# El contrato antiguo de /ventas e /inventario (una fila por dia, Decimal como
# string, lista bajo_stock/agotados) se ELIMINO: no tenia consumidores.
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    computed_field,
)


TipoReporteStr = Literal[
    "ventas",
    "productos-mas-vendidos",
    "inventario",
    "devoluciones",
    "rendimiento-vendedores",
]


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


# ===========================================================================
# CU20 - DTOs de los reportes generados por app/modules/reportes/service.py
# ===========================================================================
# Se crean con `Modelo.model_validate(dataclass_del_service)` (from_attributes).
# Los DTOs de rendimiento-vendedores (arriba) no se tocan: su endpoint conserva
# el contrato original.
#
# Convenciones de los DTOs nuevos:
# - Importes en `Dinero`: Decimal en Python (sin errores de redondeo) pero
#   NUMERO JSON (no string) para que Angular/Chart.js los use directo. Los DTOs
#   de rendimiento-vendedores (arriba) emiten Decimal como string; estos no.
# - Todo reporte trae `sin_datos` y `mensaje`. Sin resultados la respuesta es
#   200 con totales en cero y listas vacias (no es un error del servidor).
# - Las listas `por_*` ya son las series de los graficos (etiqueta + valores);
#   Angular no recalcula nada.
# - Alcance de datos GLOBAL: el modelo no tiene sucursal en usuarios, ventas,
#   productos ni devoluciones (decision A del CU20).
Dinero = Annotated[
    Decimal, PlainSerializer(float, return_type=float, when_used="json")
]

MENSAJE_SIN_DATOS = "No se encontraron datos para los parámetros ingresados."

CanalVenta = Literal["ONLINE", "POS"]
NivelStock = Literal["CRITICO", "BAJO", "OK"]
EstadoDevolucion = Literal["SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA"]


class _ReporteDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FiltrosAplicados(_ReporteDTO):
    """Filtros efectivamente usados (ya con los valores por defecto)."""

    fecha_inicio: date
    fecha_fin: date
    categoria_id: Optional[int] = None
    canal_venta: Optional[CanalVenta] = None


class _ReporteBase(_ReporteDTO):
    filtros: FiltrosAplicados
    sin_datos: bool = Field(
        description="True si ningun registro cumple los filtros (mostrar `mensaje`)."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mensaje(self) -> Optional[str]:
        return MENSAJE_SIN_DATOS if self.sin_datos else None


# ---------------------------------------------------------------------------
# Ventas por periodo
# ---------------------------------------------------------------------------
class PuntoDiaVentas(_ReporteDTO):
    fecha: date
    ingresos: Dinero
    unidades: int
    num_ventas: int


class FilaCategoriaVentas(_ReporteDTO):
    id_categoria: int
    categoria: str
    ingresos: Dinero
    unidades: int


class FilaCanalVentas(_ReporteDTO):
    canal: CanalVenta
    ingresos: Dinero
    num_ventas: int


class FilaMetodoPagoVentas(_ReporteDTO):
    metodo_pago: str
    ingresos: Dinero
    num_ventas: int


class VentasPeriodoResponse(_ReporteBase):
    """KPIs + series del reporte de ventas.

    `ingresos_productos` = suma de las lineas de detalle (sin envio).
    `total_facturado` / `total_envios` = suma de ventas.total / costo_envio;
    son None cuando hay filtro de categoria (el envio no es atribuible).
    Solo cuentan ventas PAGADO. No descuenta devoluciones.
    """

    num_ventas: int
    unidades_vendidas: int
    ingresos_productos: Dinero
    ticket_promedio: Dinero
    total_facturado: Optional[Dinero] = None
    total_envios: Optional[Dinero] = None
    por_fecha: list[PuntoDiaVentas] = Field(
        default_factory=list, description="Un punto por dia del rango (ceros incluidos)."
    )
    por_categoria: list[FilaCategoriaVentas] = Field(default_factory=list)
    por_canal: list[FilaCanalVentas] = Field(default_factory=list)
    por_metodo_pago: list[FilaMetodoPagoVentas] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Productos mas vendidos
# ---------------------------------------------------------------------------
class ProductoMasVendidoItem(_ReporteDTO):
    posicion: int = Field(description="1 = el mas vendido.")
    id_producto: int
    producto: str
    id_categoria: int
    categoria: str
    cantidad_vendida: int
    total_generado: Dinero
    num_ventas: int


class ProductosMasVendidosResponse(_ReporteBase):
    """Ranking por unidades vendidas (desempate: ingresos, nombre)."""

    top: int
    total_productos_vendidos: int = Field(
        description="Productos distintos con ventas en el rango (antes del top)."
    )
    items: list[ProductoMasVendidoItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------
class ProductoInventarioItem(_ReporteDTO):
    id_producto: int
    producto: str
    id_categoria: int
    categoria: str
    estado: str
    stock_actual: int
    precio_venta: Dinero
    valor_stock: Dinero
    nivel_stock: NivelStock = Field(description="CRITICO (<5), BAJO (<15) u OK (criterio CU22).")
    unidades_vendidas: int = Field(description="Unidades vendidas en el rango de fechas.")
    rotacion: Optional[Dinero] = Field(
        default=None,
        description=(
            "APROXIMACION: unidades_vendidas / stock_actual. None si "
            "stock_actual = 0 (ver `rotacion_disponible`)."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rotacion_disponible(self) -> bool:
        """False cuando no hay stock: la rotacion no es calculable."""
        return self.rotacion is not None


class InventarioSituacionResponse(_ReporteBase):
    """Situacion del inventario al momento de la consulta.

    Alcance: productos no Inactivos, stock global. Las fechas/canal de
    `filtros` solo afectan a `unidades_vendidas` y `rotacion`.
    """

    total_productos: int
    stock_total_unidades: int
    valor_inventario: Dinero
    agotados: int
    por_nivel: dict[str, int] = Field(
        description="Siempre con las claves CRITICO, BAJO y OK."
    )
    total_filas: int = Field(description="Filas que cumplen el filtro (antes del limite).")
    limite: int
    items: list[ProductoInventarioItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Devoluciones
# ---------------------------------------------------------------------------
class DevolucionesPorFecha(_ReporteDTO):
    fecha: date
    num_devoluciones: int
    unidades: int
    importe: Dinero


class DevolucionesPorProducto(_ReporteDTO):
    id_producto: int
    producto: str
    categoria: str
    unidades: int
    importe: Dinero


class DevolucionesPorCategoria(_ReporteDTO):
    id_categoria: int
    categoria: str
    unidades: int
    importe: Dinero


class DevolucionesPorEstado(_ReporteDTO):
    estado: EstadoDevolucion
    num_devoluciones: int
    unidades: int
    importe: Dinero


class DevolucionDetalleItem(_ReporteDTO):
    id_devolucion: int
    fecha_solicitud: datetime
    estado: EstadoDevolucion
    codigo_venta: str
    motivo: str
    unidades: int
    importe: Dinero = Field(description="Monto solicitado (todas las lineas).")


class DevolucionesReporteResponse(_ReporteBase):
    """Reporte de devoluciones (por `fecha_solicitud`).

    `unidades_devueltas`, `importe_total` y las series por fecha/producto/
    categoria EXCLUYEN las RECHAZADA. `num_devoluciones` y `por_estado`
    incluyen todos los estados.
    """

    estado: Optional[EstadoDevolucion] = Field(
        default=None, description="Filtro de estado aplicado, si hubo."
    )
    num_devoluciones: int
    unidades_devueltas: int
    importe_total: Dinero
    importe_completado: Dinero = Field(description="Solo devoluciones COMPLETADA.")
    por_estado: list[DevolucionesPorEstado] = Field(default_factory=list)
    por_fecha: list[DevolucionesPorFecha] = Field(
        default_factory=list, description="Un punto por dia del rango (ceros incluidos)."
    )
    por_producto: list[DevolucionesPorProducto] = Field(default_factory=list)
    por_categoria: list[DevolucionesPorCategoria] = Field(default_factory=list)
    detalle: list[DevolucionDetalleItem] = Field(default_factory=list)
