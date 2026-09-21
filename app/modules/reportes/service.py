# backend/app/modules/reportes/service.py
# CU20 - Gestion de Reportes: consultas y agregaciones (READ-ONLY).
#
# Capa de servicio entre los endpoints (app/api/v1/endpoints/reportes.py) y
# los modelos existentes. NO define tablas ni escribe en la DB: cada funcion
# solo hace SELECT con GROUP BY / SUM / COUNT ejecutados en PostgreSQL.
#
# Convenciones (documentadas para las fases siguientes):
# - El servicio NO conoce HTTP: ante filtros invalidos lanza
#   `ReporteFiltroError` y el endpoint la traduce a 422.
# - Devuelve dataclasses tipadas; los schemas Pydantic (fase 2) las mapean.
# - Solo cuentan ventas `estado_pago = 'PAGADO'` (PENDIENTE/RECHAZADO no son
#   ingreso real).
# - Canal de venta: NO existe columna; se DERIVA de `Venta.id_vendedor`
#   (NULL -> ONLINE, NOT NULL -> POS), igual que ventas.py y reportes.py.
# - Rango de fechas: [fecha_inicio 00:00 UTC, fecha_fin + 1 dia 00:00 UTC),
#   intervalo semiabierto (permite usar un indice en fecha_venta si algun dia
#   se justifica) y consistente con el UTC que ya usa reportes.py.
# - ZONA HORARIA: TODO en UTC. `hoy_utc()` (default de fechas), los limites del
#   rango y la agrupacion por dia en SQL (`_dia_utc`) usan UTC; nada depende
#   de la zona del servidor. Consecuencia de negocio: una venta hecha a las
#   21:00 en Bolivia (UTC-4) cae en el dia UTC siguiente. Si el negocio exige
#   dia local, se cambia SOLO aqui (hoy_utc, FiltrosReporte.dt_*, _dia_utc).
# - Ingresos por producto = SUM(detalle_ventas.subtotal) (sin envio).
#   `total_facturado` = SUM(ventas.total) (incluye costo_envio) y solo se
#   informa cuando NO hay filtro por categoria (el envio no es atribuible a
#   una categoria).
# - Alcance de datos: GLOBAL. El modelo no tiene id_sucursal en usuarios,
#   ventas, productos ni devoluciones (decision A del CU20).
#
# NOTAS TECNICAS PENDIENTES (decididas, no son tareas de esta fase):
# - Indice en ventas.fecha_venta: NO se crea. Con ~10 ventas EXPLAIN ANALYZE
#   muestra Seq Scan de 0.15 ms; no hay volumen para medir beneficio. Solo
#   reevaluarlo (EXPLAIN ANALYZE sobre una BD con ~100.000 ventas) si el
#   volumen de ventas crece de forma importante. Costo: espacio + una
#   escritura extra por checkout.
# - Diferencia preexistente modelo <-> BD: la BD tiene el indice
#   `ix_ventas_estado_pago` que el modelo `Venta` NO declara. No se modifica
#   ni se agrega al modelo hasta analizar su origen (migracion/manual).
# - Dependencia servicio -> endpoint: STOCK_CRITICO / STOCK_MEDIO se importan
#   de app.api.v1.endpoints.inventario (una sola fuente de verdad con CU22).
#   No es ideal (un servicio no deberia depender de la capa API); lo correcto
#   es moverlos a un modulo neutro (p.ej. app/modules/inventario/constantes.py)
#   e importarlos desde ambos lados. Se deja para un refactor aparte.
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, case, cast, distinct, func
from sqlalchemy.orm import Session

from app.api.v1.endpoints.inventario import STOCK_CRITICO, STOCK_MEDIO
from app.modules.devoluciones.models import DetalleDevolucion, Devolucion
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import Categoria, InventarioSucursal, Producto
from app.modules.ventas.models import DetalleVenta, Venta

CANALES_VENTA = ("ONLINE", "POS")
ESTADOS_DEVOLUCION = ("SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA")
NIVELES_STOCK = ("CRITICO", "BAJO", "OK")

DIAS_RANGO_DEFAULT = 30
TOP_DEFAULT = 10
TOP_MAX = 100
LIMITE_FILAS_DEFAULT = 200
LIMITE_FILAS_MAX = 1000

CERO = Decimal("0")


class ReporteFiltroError(ValueError):
    """Filtro invalido (rango invertido, categoria inexistente, canal...)."""


# ---------------------------------------------------------------------------
# Filtros reutilizables
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FiltrosReporte:
    """Filtros ya validados y normalizados, compartidos por los reportes."""

    fecha_inicio: date
    fecha_fin: date
    categoria_id: Optional[int] = None
    canal_venta: Optional[str] = None  # 'ONLINE' | 'POS' | None
    id_sucursal: Optional[int] = None  # Filtro estricto por sucursal (CU17+CU20)

    @property
    def dt_inicio(self) -> datetime:
        return datetime.combine(self.fecha_inicio, time.min, tzinfo=timezone.utc)

    @property
    def dt_fin_exclusivo(self) -> datetime:
        return datetime.combine(
            self.fecha_fin + timedelta(days=1), time.min, tzinfo=timezone.utc
        )


def nombre_categoria(db: Session, categoria_id: Optional[int]) -> Optional[str]:
    """Nombre de la categoria filtrada (para rotular la exportacion); None si no hay."""
    if categoria_id is None:
        return None
    categoria = db.get(Categoria, categoria_id)
    return categoria.nombre if categoria else None


def hoy_utc() -> date:
    """'Hoy' de los reportes: fecha calendario UTC.

    Unica fuente del dia actual en CU20. NO usar date.today(): depende de la
    zona horaria del servidor y el mismo reporte cambiaria de dia segun donde
    corra (local vs Render). Coherente con `_dia_utc` (agrupacion en SQL) y con
    los limites [00:00 UTC, +1 dia) de FiltrosReporte.
    """
    return datetime.now(timezone.utc).date()


def rango_por_defecto(hoy: Optional[date] = None) -> tuple[date, date]:
    """Ultimos 30 dias inclusive terminando en `hoy` (default: hoy UTC)."""
    hoy = hoy or hoy_utc()
    return hoy - timedelta(days=DIAS_RANGO_DEFAULT - 1), hoy


def construir_filtros(
    db: Session,
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    categoria_id: Optional[int] = None,
    canal_venta: Optional[str] = None,
    id_sucursal: Optional[int] = None,
) -> FiltrosReporte:
    """Valida y normaliza los filtros. Todos son opcionales.

    - Sin fechas: ultimos 30 dias hasta hoy (UTC), inclusive.
    - Solo una fecha: la otra se completa (fin=hoy / inicio=fin-29 dias).
    - fecha_inicio > fecha_fin -> ReporteFiltroError.
    - categoria_id debe existir; canal_venta debe ser ONLINE o POS.
    - id_sucursal debe existir si viene especificado.
    """
    hoy = hoy_utc()
    if fecha_fin is None:
        fecha_fin = hoy if fecha_inicio is None or fecha_inicio <= hoy else fecha_inicio
    if fecha_inicio is None:
        fecha_inicio = rango_por_defecto(fecha_fin)[0]
    if fecha_inicio > fecha_fin:
        raise ReporteFiltroError(
            f"fecha_inicio ({fecha_inicio}) no puede ser posterior a "
            f"fecha_fin ({fecha_fin})."
        )

    if categoria_id is not None and db.get(Categoria, categoria_id) is None:
        raise ReporteFiltroError(f"La categoria {categoria_id} no existe.")

    if id_sucursal is not None and db.get(Sucursal, id_sucursal) is None:
        raise ReporteFiltroError(f"La sucursal {id_sucursal} no existe.")

    canal = None
    if canal_venta:
        canal = canal_venta.strip().upper()
        if canal not in CANALES_VENTA:
            raise ReporteFiltroError(
                f"canal_venta invalido: use {' o '.join(CANALES_VENTA)}."
            )

    return FiltrosReporte(fecha_inicio, fecha_fin, categoria_id, canal, id_sucursal)


def _dec(valor) -> Decimal:
    return Decimal(str(valor)) if valor is not None else CERO


def _canal_col():
    """CASE id_vendedor IS NULL -> 'ONLINE' / NOT NULL -> 'POS'."""
    return case((Venta.id_vendedor.is_(None), "ONLINE"), else_="POS")


def _filtro_canal(f: FiltrosReporte) -> list:
    if f.canal_venta == "ONLINE":
        return [Venta.id_vendedor.is_(None)]
    if f.canal_venta == "POS":
        return [Venta.id_vendedor.is_not(None)]
    return []


def _condiciones_venta(f: FiltrosReporte) -> list:
    """WHERE comun sobre `ventas`: pagadas, en rango y del canal pedido."""
    conds = [
        Venta.estado_pago == "PAGADO",
        Venta.fecha_venta >= f.dt_inicio,
        Venta.fecha_venta < f.dt_fin_exclusivo,
        *_filtro_canal(f),
    ]
    if f.id_sucursal is not None:
        conds.append(Venta.id_sucursal == f.id_sucursal)
    return conds


def _dia_utc(columna):
    """Fecha calendario en UTC (no depende del timezone de la sesion)."""
    return cast(func.timezone("UTC", columna), Date)


def _lineas_venta(db: Session, f: FiltrosReporte, *columnas):
    """Base de las agregaciones de venta: una fila por linea de detalle.

    detalle_ventas -> ventas (filtros) -> productos -> categorias.
    Un solo camino de JOINs para todos los reportes de venta.
    """
    q = (
        db.query(*columnas)
        .select_from(DetalleVenta)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .join(Producto, Producto.id_producto == DetalleVenta.id_producto)
        .join(Categoria, Categoria.id_categoria == Producto.id_categoria)
        .filter(*_condiciones_venta(f))
    )
    if f.categoria_id is not None:
        q = q.filter(Producto.id_categoria == f.categoria_id)
    return q


def _serie_dias(inicio: date, fin: date) -> list[date]:
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


# ---------------------------------------------------------------------------
# Resultados (dataclasses)
# ---------------------------------------------------------------------------
@dataclass
class PuntoDia:
    fecha: date
    ingresos: Decimal
    unidades: int
    num_ventas: int


@dataclass
class FilaCategoria:
    id_categoria: int
    categoria: str
    ingresos: Decimal
    unidades: int


@dataclass
class FilaCanal:
    canal: str
    ingresos: Decimal
    num_ventas: int


@dataclass
class FilaMetodoPago:
    metodo_pago: str
    ingresos: Decimal
    num_ventas: int


@dataclass
class ReporteVentas:
    filtros: FiltrosReporte
    sin_datos: bool
    num_ventas: int
    unidades_vendidas: int
    ingresos_productos: Decimal
    ticket_promedio: Decimal
    total_facturado: Optional[Decimal]  # None si hay filtro de categoria
    total_envios: Optional[Decimal]  # None si hay filtro de categoria
    por_fecha: list[PuntoDia] = field(default_factory=list)  # zero-fill
    por_categoria: list[FilaCategoria] = field(default_factory=list)
    por_canal: list[FilaCanal] = field(default_factory=list)
    por_metodo_pago: list[FilaMetodoPago] = field(default_factory=list)


@dataclass
class ProductoMasVendido:
    posicion: int
    id_producto: int
    producto: str
    id_categoria: int
    categoria: str
    cantidad_vendida: int
    total_generado: Decimal
    num_ventas: int


@dataclass
class ReporteTopProductos:
    filtros: FiltrosReporte
    sin_datos: bool
    top: int
    total_productos_vendidos: int  # productos distintos con ventas en el rango
    items: list[ProductoMasVendido] = field(default_factory=list)


@dataclass
class ProductoInventario:
    id_producto: int
    producto: str
    id_categoria: int
    categoria: str
    estado: str
    stock_actual: int
    precio_venta: Decimal
    valor_stock: Decimal
    nivel_stock: str  # CRITICO (<5) | BAJO (<15) | OK, criterio del CU22
    unidades_vendidas: int  # en el rango de fechas
    rotacion: Optional[Decimal]  # unidades_vendidas / stock_actual; None si stock=0


@dataclass
class ReporteInventario:
    filtros: FiltrosReporte
    sin_datos: bool
    total_productos: int
    stock_total_unidades: int
    valor_inventario: Decimal
    agotados: int
    por_nivel: dict[str, int]
    total_filas: int  # filas que cumplen el filtro (antes del limite)
    limite: int
    items: list[ProductoInventario] = field(default_factory=list)


@dataclass
class DevolucionesPorFecha:
    fecha: date
    num_devoluciones: int
    unidades: int
    importe: Decimal


@dataclass
class DevolucionesPorProducto:
    id_producto: int
    producto: str
    categoria: str
    unidades: int
    importe: Decimal


@dataclass
class DevolucionesPorCategoria:
    id_categoria: int
    categoria: str
    unidades: int
    importe: Decimal


@dataclass
class DevolucionesPorEstado:
    estado: str
    num_devoluciones: int
    unidades: int
    importe: Decimal  # incluye RECHAZADA (monto solicitado)


@dataclass
class DevolucionDetalle:
    id_devolucion: int
    fecha_solicitud: datetime
    estado: str
    codigo_venta: str
    motivo: str
    unidades: int
    importe: Decimal  # monto solicitado (todas las lineas de la devolucion)


@dataclass
class ReporteDevoluciones:
    """`unidades_devueltas` e `importe_total` (y las series por fecha/producto/
    categoria) EXCLUYEN devoluciones RECHAZADA: no hubo reembolso. Los conteos
    de devoluciones y `por_estado` incluyen todos los estados."""

    filtros: FiltrosReporte
    estado: Optional[str]
    sin_datos: bool
    num_devoluciones: int
    unidades_devueltas: int
    importe_total: Decimal
    importe_completado: Decimal  # solo COMPLETADA (mercaderia ya recibida)
    por_estado: list[DevolucionesPorEstado] = field(default_factory=list)
    por_fecha: list[DevolucionesPorFecha] = field(default_factory=list)  # zero-fill
    por_producto: list[DevolucionesPorProducto] = field(default_factory=list)
    por_categoria: list[DevolucionesPorCategoria] = field(default_factory=list)
    detalle: list[DevolucionDetalle] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Ventas por periodo
# ---------------------------------------------------------------------------
def reporte_ventas(db: Session, f: FiltrosReporte) -> ReporteVentas:
    ingresos = func.coalesce(func.sum(DetalleVenta.subtotal), 0)
    unidades = func.coalesce(func.sum(DetalleVenta.cantidad), 0)
    n_ventas = func.count(distinct(Venta.id_venta))

    tot = _lineas_venta(db, f, ingresos, unidades, n_ventas).one()
    num_ventas = int(tot[2] or 0)
    if num_ventas == 0:
        return ReporteVentas(
            filtros=f,
            sin_datos=True,
            num_ventas=0,
            unidades_vendidas=0,
            ingresos_productos=CERO,
            ticket_promedio=CERO,
            total_facturado=None if f.categoria_id is not None else CERO,
            total_envios=None if f.categoria_id is not None else CERO,
        )

    ingresos_total = _dec(tot[0])
    total_facturado = total_envios = None
    if f.categoria_id is None:
        fact = (
            db.query(
                func.coalesce(func.sum(Venta.total), 0),
                func.coalesce(func.sum(Venta.costo_envio), 0),
            )
            .filter(*_condiciones_venta(f))
            .one()
        )
        total_facturado, total_envios = _dec(fact[0]), _dec(fact[1])

    dia = _dia_utc(Venta.fecha_venta).label("dia")
    por_dia = {
        r.dia: r
        for r in _lineas_venta(
            db,
            f,
            dia,
            func.sum(DetalleVenta.subtotal).label("ingresos"),
            func.sum(DetalleVenta.cantidad).label("unidades"),
            func.count(distinct(Venta.id_venta)).label("n"),
        ).group_by(dia)
    }
    por_fecha = [
        PuntoDia(
            fecha=d,
            ingresos=_dec(por_dia[d].ingresos) if d in por_dia else CERO,
            unidades=int(por_dia[d].unidades) if d in por_dia else 0,
            num_ventas=int(por_dia[d].n) if d in por_dia else 0,
        )
        for d in _serie_dias(f.fecha_inicio, f.fecha_fin)
    ]

    ing_cat = func.sum(DetalleVenta.subtotal)
    por_categoria = [
        FilaCategoria(r.id, r.nombre, _dec(r.ingresos), int(r.unidades))
        for r in _lineas_venta(
            db,
            f,
            Categoria.id_categoria.label("id"),
            Categoria.nombre.label("nombre"),
            ing_cat.label("ingresos"),
            func.sum(DetalleVenta.cantidad).label("unidades"),
        )
        .group_by(Categoria.id_categoria, Categoria.nombre)
        .order_by(ing_cat.desc(), Categoria.nombre)
    ]

    canal = _canal_col().label("canal")
    ing_canal = func.sum(DetalleVenta.subtotal)
    por_canal = [
        FilaCanal(r.canal, _dec(r.ingresos), int(r.n))
        for r in _lineas_venta(
            db,
            f,
            canal,
            ing_canal.label("ingresos"),
            func.count(distinct(Venta.id_venta)).label("n"),
        )
        .group_by(canal)
        .order_by(ing_canal.desc())
    ]

    ing_met = func.sum(DetalleVenta.subtotal)
    por_metodo = [
        FilaMetodoPago(r.metodo, _dec(r.ingresos), int(r.n))
        for r in _lineas_venta(
            db,
            f,
            Venta.metodo_pago.label("metodo"),
            ing_met.label("ingresos"),
            func.count(distinct(Venta.id_venta)).label("n"),
        )
        .group_by(Venta.metodo_pago)
        .order_by(ing_met.desc())
    ]

    return ReporteVentas(
        filtros=f,
        sin_datos=False,
        num_ventas=num_ventas,
        unidades_vendidas=int(tot[1] or 0),
        ingresos_productos=ingresos_total,
        ticket_promedio=(ingresos_total / num_ventas).quantize(Decimal("0.01")),
        total_facturado=total_facturado,
        total_envios=total_envios,
        por_fecha=por_fecha,
        por_categoria=por_categoria,
        por_canal=por_canal,
        por_metodo_pago=por_metodo,
    )


# ---------------------------------------------------------------------------
# 2. Productos mas vendidos
# ---------------------------------------------------------------------------
def reporte_productos_mas_vendidos(
    db: Session, f: FiltrosReporte, top: int = TOP_DEFAULT
) -> ReporteTopProductos:
    """Ranking por unidades vendidas (desempate: ingresos, luego nombre).

    Calculado desde detalle_ventas; no existe ningun contador almacenado.
    """
    top = max(1, min(top, TOP_MAX))
    cantidad = func.sum(DetalleVenta.cantidad)
    generado = func.sum(DetalleVenta.subtotal)

    base = _lineas_venta(
        db,
        f,
        Producto.id_producto.label("id"),
        Producto.nombre.label("nombre"),
        Categoria.id_categoria.label("id_cat"),
        Categoria.nombre.label("cat"),
        cantidad.label("cantidad"),
        generado.label("generado"),
        func.count(distinct(Venta.id_venta)).label("n"),
    ).group_by(
        Producto.id_producto, Producto.nombre, Categoria.id_categoria, Categoria.nombre
    )

    total_productos = base.count()  # subquery COUNT(*) sobre el GROUP BY
    rows = base.order_by(cantidad.desc(), generado.desc(), Producto.nombre).limit(top)
    items = [
        ProductoMasVendido(
            posicion=i,
            id_producto=r.id,
            producto=r.nombre,
            id_categoria=r.id_cat,
            categoria=r.cat,
            cantidad_vendida=int(r.cantidad),
            total_generado=_dec(r.generado),
            num_ventas=int(r.n),
        )
        for i, r in enumerate(rows, start=1)
    ]
    return ReporteTopProductos(
        filtros=f,
        sin_datos=not items,
        top=top,
        total_productos_vendidos=total_productos,
        items=items,
    )


# ---------------------------------------------------------------------------
# 3. Inventario (snapshot actual + rotacion aproximada en unidades)
# ---------------------------------------------------------------------------
def reporte_inventario(
    db: Session,
    f: FiltrosReporte,
    nivel_stock: Optional[str] = None,
    limite: int = LIMITE_FILAS_DEFAULT,
) -> ReporteInventario:
    """Situacion del inventario AL MOMENTO de la consulta.

    - Alcance: productos no Inactivos (Activo + Agotado), stock GLOBAL
      (`productos.stock_total`; no hay stock por sucursal en el modelo).
    - Las fechas/canal del filtro solo afectan a `unidades_vendidas` y
      `rotacion`; el stock no es historico.
    - rotacion = unidades_vendidas(rango) / stock_actual. Es una
      APROXIMACION en unidades: el modelo no tiene costo de compra ni stock
      historico por dia, asi que no es la rotacion contable (costo/inv.
      promedio). None cuando stock_actual = 0.
    - nivel_stock usa los umbrales del CU22 (STOCK_CRITICO / STOCK_MEDIO).
    """
    if nivel_stock is not None:
        nivel_stock = nivel_stock.strip().upper()
        if nivel_stock not in NIVELES_STOCK:
            raise ReporteFiltroError(
                f"nivel_stock invalido: use {', '.join(NIVELES_STOCK)}."
            )
    limite = max(1, min(limite, LIMITE_FILAS_MAX))

    alcance = [Producto.estado != "Inactivo"]
    if f.categoria_id is not None:
        alcance.append(Producto.id_categoria == f.categoria_id)

    if f.id_sucursal is not None:
        inv_sub = (
            db.query(
                InventarioSucursal.id_producto.label("id_producto"),
                InventarioSucursal.stock.label("stock_suc"),
            )
            .filter(InventarioSucursal.id_sucursal == f.id_sucursal)
            .subquery()
        )
        stock_col = func.coalesce(inv_sub.c.stock_suc, 0)
        nivel = case(
            (stock_col < STOCK_CRITICO, "CRITICO"),
            (stock_col < STOCK_MEDIO, "BAJO"),
            else_="OK",
        )
        agg = (
            db.query(
                func.count(Producto.id_producto),
                func.coalesce(func.sum(stock_col), 0),
                func.coalesce(func.sum(stock_col * Producto.precio_venta), 0),
                func.coalesce(func.sum(case((stock_col == 0, 1), else_=0)), 0),
            )
            .outerjoin(inv_sub, inv_sub.c.id_producto == Producto.id_producto)
            .filter(*alcance)
            .one()
        )
    else:
        inv_sub = None
        stock_col = Producto.stock_total
        nivel = case(
            (stock_col < STOCK_CRITICO, "CRITICO"),
            (stock_col < STOCK_MEDIO, "BAJO"),
            else_="OK",
        )
        agg = (
            db.query(
                func.count(Producto.id_producto),
                func.coalesce(func.sum(stock_col), 0),
                func.coalesce(func.sum(stock_col * Producto.precio_venta), 0),
                func.coalesce(func.sum(case((stock_col == 0, 1), else_=0)), 0),
            )
            .filter(*alcance)
            .one()
        )

    total = int(agg[0] or 0)
    if total == 0:
        return ReporteInventario(
            filtros=f,
            sin_datos=True,
            total_productos=0,
            stock_total_unidades=0,
            valor_inventario=CERO,
            agotados=0,
            por_nivel={n: 0 for n in NIVELES_STOCK},
            total_filas=0,
            limite=limite,
        )

    por_nivel = {n: 0 for n in NIVELES_STOCK}
    nivel_q = db.query(nivel, func.count(Producto.id_producto))
    if inv_sub is not None:
        nivel_q = nivel_q.outerjoin(inv_sub, inv_sub.c.id_producto == Producto.id_producto)
    for n, c in nivel_q.filter(*alcance).group_by(nivel):
        por_nivel[n] = int(c)

    vendidas = (
        db.query(
            DetalleVenta.id_producto.label("id_producto"),
            func.sum(DetalleVenta.cantidad).label("unidades"),
        )
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .filter(*_condiciones_venta(f))
        .group_by(DetalleVenta.id_producto)
        .subquery()
    )

    filas_q = (
        db.query(
            Producto.id_producto,
            Producto.nombre,
            Categoria.id_categoria.label("id_cat"),
            Categoria.nombre.label("cat"),
            Producto.estado,
            stock_col.label("stock_total"),
            Producto.precio_venta,
            nivel.label("nivel"),
            func.coalesce(vendidas.c.unidades, 0).label("unidades"),
        )
        .join(Categoria, Categoria.id_categoria == Producto.id_categoria)
    )
    if inv_sub is not None:
        filas_q = filas_q.outerjoin(inv_sub, inv_sub.c.id_producto == Producto.id_producto)
    filas = (
        filas_q.outerjoin(vendidas, vendidas.c.id_producto == Producto.id_producto)
        .filter(*alcance)
    )
    if nivel_stock:
        filas = filas.filter(nivel == nivel_stock)
    total_filas = filas.count()
    filas = filas.order_by(stock_col.asc(), Producto.nombre).limit(limite)

    items = []
    for r in filas:
        stock = int(r.stock_total)
        vend = int(r.unidades or 0)
        precio = _dec(r.precio_venta)
        items.append(
            ProductoInventario(
                id_producto=r.id_producto,
                producto=r.nombre,
                id_categoria=r.id_cat,
                categoria=r.cat,
                estado=r.estado,
                stock_actual=stock,
                precio_venta=precio,
                valor_stock=precio * stock,
                nivel_stock=r.nivel,
                unidades_vendidas=vend,
                rotacion=(Decimal(vend) / stock).quantize(Decimal("0.01")) if stock > 0 else None,
            )
        )

    return ReporteInventario(
        filtros=f,
        sin_datos=False,
        total_productos=total,
        stock_total_unidades=int(agg[1] or 0),
        valor_inventario=_dec(agg[2]),
        agotados=int(agg[3] or 0),
        por_nivel=por_nivel,
        total_filas=total_filas,
        limite=limite,
        items=items,
    )


# ---------------------------------------------------------------------------
# 4. Devoluciones
# ---------------------------------------------------------------------------
def reporte_devoluciones(
    db: Session,
    f: FiltrosReporte,
    estado: Optional[str] = None,
    top: int = TOP_DEFAULT,
    limite_detalle: int = LIMITE_FILAS_DEFAULT,
) -> ReporteDevoluciones:
    """Devoluciones por `fecha_solicitud`. El canal y la categoria se toman de
    la venta original / producto devuelto."""
    if estado is not None:
        estado = estado.strip().upper()
        if estado not in ESTADOS_DEVOLUCION:
            raise ReporteFiltroError(
                f"estado invalido: use {', '.join(ESTADOS_DEVOLUCION)}."
            )
    top = max(1, min(top, TOP_MAX))
    limite_detalle = max(1, min(limite_detalle, LIMITE_FILAS_MAX))

    def lineas(*columnas):
        q = (
            db.query(*columnas)
            .select_from(DetalleDevolucion)
            .join(Devolucion, Devolucion.id_devolucion == DetalleDevolucion.id_devolucion)
            .join(Venta, Venta.id_venta == Devolucion.id_venta)
            .join(Producto, Producto.id_producto == DetalleDevolucion.id_producto)
            .join(Categoria, Categoria.id_categoria == Producto.id_categoria)
            .filter(
                Devolucion.fecha_solicitud >= f.dt_inicio,
                Devolucion.fecha_solicitud < f.dt_fin_exclusivo,
                *_filtro_canal(f),
            )
        )
        if f.categoria_id is not None:
            q = q.filter(Producto.id_categoria == f.categoria_id)
        if f.id_sucursal is not None:
            q = q.filter(Venta.id_sucursal == f.id_sucursal)
        if estado:
            q = q.filter(Devolucion.estado == estado)
        return q

    efectiva = Devolucion.estado != "RECHAZADA"
    unid_ef = func.coalesce(
        func.sum(case((efectiva, DetalleDevolucion.cantidad_devuelta), else_=0)), 0
    )
    imp_ef = func.coalesce(
        func.sum(case((efectiva, DetalleDevolucion.subtotal), else_=0)), 0
    )
    n_dev = func.count(distinct(Devolucion.id_devolucion))

    tot = lineas(
        n_dev,
        unid_ef,
        imp_ef,
        func.coalesce(
            func.sum(
                case(
                    (Devolucion.estado == "COMPLETADA", DetalleDevolucion.subtotal),
                    else_=0,
                )
            ),
            0,
        ),
    ).one()
    num = int(tot[0] or 0)
    if num == 0:
        return ReporteDevoluciones(
            filtros=f,
            estado=estado,
            sin_datos=True,
            num_devoluciones=0,
            unidades_devueltas=0,
            importe_total=CERO,
            importe_completado=CERO,
        )

    por_estado = [
        DevolucionesPorEstado(
            r.estado, int(r.n), int(r.unidades), _dec(r.importe)
        )
        for r in lineas(
            Devolucion.estado.label("estado"),
            func.count(distinct(Devolucion.id_devolucion)).label("n"),
            func.sum(DetalleDevolucion.cantidad_devuelta).label("unidades"),
            func.sum(DetalleDevolucion.subtotal).label("importe"),
        )
        .group_by(Devolucion.estado)
        .order_by(Devolucion.estado)
    ]

    dia = _dia_utc(Devolucion.fecha_solicitud).label("dia")
    por_dia = {
        r.dia: r
        for r in lineas(
            dia,
            func.count(distinct(Devolucion.id_devolucion)).label("n"),
            unid_ef.label("unidades"),
            imp_ef.label("importe"),
        ).group_by(dia)
    }
    por_fecha = [
        DevolucionesPorFecha(
            fecha=d,
            num_devoluciones=int(por_dia[d].n) if d in por_dia else 0,
            unidades=int(por_dia[d].unidades) if d in por_dia else 0,
            importe=_dec(por_dia[d].importe) if d in por_dia else CERO,
        )
        for d in _serie_dias(f.fecha_inicio, f.fecha_fin)
    ]

    por_producto = [
        DevolucionesPorProducto(r.id, r.nombre, r.cat, int(r.unidades), _dec(r.importe))
        for r in lineas(
            Producto.id_producto.label("id"),
            Producto.nombre.label("nombre"),
            Categoria.nombre.label("cat"),
            unid_ef.label("unidades"),
            imp_ef.label("importe"),
        )
        .group_by(Producto.id_producto, Producto.nombre, Categoria.nombre)
        .having(unid_ef > 0)
        .order_by(unid_ef.desc(), imp_ef.desc(), Producto.nombre)
        .limit(top)
    ]

    por_categoria = [
        DevolucionesPorCategoria(r.id, r.nombre, int(r.unidades), _dec(r.importe))
        for r in lineas(
            Categoria.id_categoria.label("id"),
            Categoria.nombre.label("nombre"),
            unid_ef.label("unidades"),
            imp_ef.label("importe"),
        )
        .group_by(Categoria.id_categoria, Categoria.nombre)
        .having(unid_ef > 0)
        .order_by(imp_ef.desc(), Categoria.nombre)
    ]

    detalle = [
        DevolucionDetalle(
            id_devolucion=r.id,
            fecha_solicitud=r.fecha,
            estado=r.estado,
            codigo_venta=r.codigo,
            motivo=r.motivo,
            unidades=int(r.unidades),
            importe=_dec(r.importe),
        )
        for r in lineas(
            Devolucion.id_devolucion.label("id"),
            Devolucion.fecha_solicitud.label("fecha"),
            Devolucion.estado.label("estado"),
            Venta.codigo.label("codigo"),
            Devolucion.motivo.label("motivo"),
            func.sum(DetalleDevolucion.cantidad_devuelta).label("unidades"),
            func.sum(DetalleDevolucion.subtotal).label("importe"),
        )
        .group_by(
            Devolucion.id_devolucion,
            Devolucion.fecha_solicitud,
            Devolucion.estado,
            Venta.codigo,
            Devolucion.motivo,
        )
        .order_by(Devolucion.fecha_solicitud.desc(), Devolucion.id_devolucion.desc())
        .limit(limite_detalle)
    ]

    return ReporteDevoluciones(
        filtros=f,
        estado=estado,
        sin_datos=False,
        num_devoluciones=num,
        unidades_devueltas=int(tot[1] or 0),
        importe_total=_dec(tot[2]),
        importe_completado=_dec(tot[3]),
        por_estado=por_estado,
        por_fecha=por_fecha,
        por_producto=por_producto,
        por_categoria=por_categoria,
        detalle=detalle,
    )
