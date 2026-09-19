# backend/app/api/v1/endpoints/reportes.py
# CU20 - Gestion de Reportes (panel ejecutivo para Administrador y Gerente de Sucursal).
#
# Endpoints bajo /api/v1/reportes (prefijo puesto en main.py):
#   GET /api/v1/reportes/ventas                      - ventas agrupadas por dia
#   GET /api/v1/reportes/inventario                  - valorizacion + bajo stock
#   GET /api/v1/reportes/rendimiento-vendedores     - performance POS por vendedor
#
# Diseno comun a los 3 endpoints:
# - READ-ONLY puro: no muta estado. La logica vive en el endpoint (no hay
#   service.py), igual que el resto de modulos del proyecto.
# - RBAC inline: solo ASU y GS (mismo patron que dashboard.py).
# - Agregaciones con func.sum / func.count / func.date ejecutadas en la
#   DB (no en Python) para soportar catalogos grandes.
# - Aislamiento de vendedores: los vendedores V y clientes C NO pueden
#   llegar aca (403 server-side, no depende del front).
#
# Decisiones de modelado:
# - `tipo_venta` se DERIVA de `Venta.id_vendedor IS NULL` (ONLINE) vs
#   `NOT NULL` (POS). El modelo no persiste la columna.
# - `metodo_pago` y `estado_pago` son VARCHAR con CHECK; los aceptamos
#   como filtro opcional y los devolvemos agrupados en el desglose.
# - Rango de fechas default: ultimos 30 dias hasta hoy. Si el front
#   manda `fecha_desde > fecha_hasta`, devolvemos 422 (validacion de
#   orden explicita en el endpoint).
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, literal_column
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import Categoria, Producto
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import Venta
from app.schemas.reporte import (
    DesglosePagosTipo,
    InventarioReporteResponse,
    ProductoStockItem,
    RendimientoVendedoresResponse,
    VendedorRendimientoItem,
    VentaDiariaReporte,
    VentasReporteResponse,
)


router = APIRouter()

# Roles con acceso al panel de reportes (igual que dashboard.py).
ROLES_REPORTES = {"ASU", "GS"}

# Umbral de "bajo stock" por defecto. Configurable por query param.
UMBRAL_BAJO_STOCK_DEFAULT = 5

# Cantidad maxima de filas en listas de bajo stock / agotados.
TOP_BAJO_STOCK = 20

# Defaults de paginacion (limite estricto para evitar respuestas enormes).
LIMIT_DEFAULT = 30
LIMIT_MAX = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_reporte_access(usuario: Usuario) -> None:
    """403 si el usuario no es ASU ni GS. Patron inline (no hay helper)."""
    nombre_rol = usuario.rol.nombre_rol if usuario.rol else ""
    if nombre_rol not in ROLES_REPORTES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su rol no tiene acceso al panel de reportes.",
        )


def _envelope(data) -> dict:
    """Envelope estandar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operacion exitosa"}


def _validar_rango(fecha_desde: date, fecha_hasta: date) -> None:
    """422 si el rango esta invertido. Lo chequeamos aca (no en Pydantic)
    para devolver un mensaje claro con el codigo HTTP consistente."""
    if fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"fecha_desde ({fecha_desde}) no puede ser mayor que "
                f"fecha_hasta ({fecha_hasta})."
            ),
        )


def _rango_default() -> tuple[date, date]:
    """Default: ultimos 30 dias (inclusive)."""
    hoy = date.today()
    return hoy.replace(day=max(1, hoy.day - 29)), hoy


def _venta_tipo_sql() -> case:
    """CASE que mapea id_vendedor IS NULL -> 'ONLINE' y NOT NULL -> 'POS'.
    Vive aca (no en el modelo) porque la columna no esta persistida.
    """
    return case((Venta.id_vendedor.is_(None), "ONLINE"), else_="POS")


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/ventas
# ---------------------------------------------------------------------------
@router.get("/ventas", response_model=None)
def reporte_ventas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    fecha_desde: Optional[date] = Query(default=None, description="YYYY-MM-DD. Default: hoy - 30 dias."),
    fecha_hasta: Optional[date] = Query(default=None, description="YYYY-MM-DD. Default: hoy."),
    metodo_pago: Optional[str] = Query(
        default=None, description="Filtra: QR | EFECTIVO | TARJETA."
    ),
    tipo_venta: Optional[str] = Query(
        default=None, description="Filtra: ONLINE | POS."
    ),
    estado_pago: Optional[str] = Query(
        default=None, description="Filtra: PENDIENTE | PAGADO | RECHAZADO."
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
):
    """CU20: Reporte de ventas agrupado por dia (pagina por dia, no por venta).

    Para cada dia del rango devuelve: total_ingresos, total_operaciones,
    ticket_promedio, desglose por metodo de pago y tipo (ONLINE/POS).
    """
    _require_reporte_access(usuario)

    if fecha_desde is None or fecha_hasta is None:
        fecha_desde, fecha_hasta = _rango_default()
    _validar_rango(fecha_desde, fecha_hasta)

    # Construimos el rango como timestamps UTC para comparar contra
    # `fecha_venta` (que es DateTime(timezone=True)). Tomamos [00:00:00, 23:59:59.999]
    # del dia de inicio y fin respectivamente.
    dt_desde = datetime.combine(fecha_desde, time.min, tzinfo=timezone.utc)
    dt_hasta = datetime.combine(fecha_hasta, time.max, tzinfo=timezone.utc)

    # Subquery base con los filtros aplicables (compartida por la lista y los totales).
    filtros = [
        Venta.fecha_venta >= dt_desde,
        Venta.fecha_hasta_label if False else Venta.fecha_venta <= dt_hasta,
    ]
    if metodo_pago:
        filtros.append(Venta.metodo_pago == metodo_pago)
    if estado_pago:
        filtros.append(Venta.estado_pago == estado_pago)
    if tipo_venta == "ONLINE":
        filtros.append(Venta.id_vendedor.is_(None))
    elif tipo_venta == "POS":
        filtros.append(Venta.id_vendedor.is_not(None))

    # ---- Lista agrupada por DIA (pagina por cantidad de dias) -----------
    tipo_col = _venta_tipo_sql().label("tipo_venta")
    # `func.date()` en Postgres devuelve un tipo DATE.
    dia_col = func.date(Venta.fecha_venta).label("fecha")

    rows_por_dia = (
        db.query(
            dia_col,
            func.coalesce(func.sum(Venta.total), 0).label("total_ingresos"),
            func.count(Venta.id_venta).label("total_operaciones"),
        )
        .filter(*filtros)
        .group_by(dia_col)
        .order_by(dia_col.desc())
        .all()
    )

    # Sub-desglose por (dia, metodo_pago) y (dia, tipo_venta). Dos queries
    # adicionales: cada una devuelve N filas pequenas (max ~30 dias x 3
    # metodos = 90 filas) y se agregan en Python. Esto evita un solo
    # GROUP BY multidimensional que seria mas opaco.
    rows_metodo = (
        db.query(
            dia_col,
            Venta.metodo_pago.label("metodo"),
            func.coalesce(func.sum(Venta.total), 0).label("total"),
        )
        .filter(*filtros)
        .group_by(dia_col, Venta.metodo_pago)
        .all()
    )

    rows_tipo = (
        db.query(
            dia_col,
            tipo_col,
            func.count(Venta.id_venta).label("ops"),
            func.coalesce(func.sum(Venta.total), 0).label("total"),
        )
        .filter(*filtros)
        .group_by(dia_col, tipo_col)
        .all()
    )

    # Indexamos sub-desgloses por fecha para O(1) en la fusion.
    metodos_por_dia: dict[date, dict[str, Decimal]] = {}
    for r in rows_metodo:
        metodos_por_dia.setdefault(r.fecha, {})[r.metodo] = r.total

    tipos_por_dia: dict[date, dict[str, dict]] = {}
    for r in rows_tipo:
        tipos_por_dia.setdefault(r.fecha, {})[r.tipo_venta] = {
            "ingresos": r.total,
            "ops": r.ops,
        }

    # ---- Fusion: construimos las filas finales ---------------------------
    dias_con_datos: list[VentaDiariaReporte] = []
    total_ingresos = Decimal("0")
    total_operaciones = 0

    for r in rows_por_dia:
        ingresos = Decimal(str(r.total_ingresos))
        ops = int(r.total_operaciones or 0)
        ticket = (ingresos / Decimal(ops)) if ops > 0 else Decimal("0")

        metodos_dia = metodos_por_dia.get(r.fecha, {})
        tipos_dia = tipos_por_dia.get(r.fecha, {})

        desglose = DesglosePagosTipo(
            metodos_pago={k: Decimal(str(v)) for k, v in metodos_dia.items()},
            tipos_venta={k: Decimal(str(v["ingresos"])) for k, v in tipos_dia.items()},
            operaciones_por_tipo={k: int(v["ops"]) for k, v in tipos_dia.items()},
        )
        dias_con_datos.append(
            VentaDiariaReporte(
                fecha=r.fecha,
                total_ingresos=ingresos,
                total_operaciones=ops,
                ticket_promedio=ticket,
                desglose=desglose,
            )
        )
        total_ingresos += ingresos
        total_operaciones += ops

    total_dias = len(dias_con_datos)
    ticket_global = (
        (total_ingresos / Decimal(total_operaciones))
        if total_operaciones > 0
        else Decimal("0")
    )

    # Paginacion por dia (no por venta individual).
    inicio = (page - 1) * limit
    fin = inicio + limit
    items_paginados = dias_con_datos[inicio:fin]
    pages = (total_dias + limit - 1) // limit if total_dias > 0 else 0

    return _envelope(
        VentasReporteResponse(
            items=items_paginados,
            total_dias=total_dias,
            total_ingresos=total_ingresos,
            total_operaciones=total_operaciones,
            ticket_promedio=ticket_global,
            page=page,
            limit=limit,
            pages=pages,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        ).model_dump(mode="json")
    )


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/inventario
# ---------------------------------------------------------------------------
@router.get("/inventario", response_model=None)
def reporte_inventario(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    umbral_bajo_stock: int = Query(
        default=UMBRAL_BAJO_STOCK_DEFAULT,
        ge=0,
        le=1000,
        description="Stock <= este umbral cuenta como 'bajo stock'.",
    ),
    top_bajo_stock: int = Query(
        default=TOP_BAJO_STOCK,
        ge=1,
        le=200,
        description="Maximo de filas en las listas bajo_stock y agotados.",
    ),
):
    """CU20: Snapshot del inventario al momento de la consulta.

    Devuelve KPIs (totales + valorizacion) y dos listas top-N
    (bajo stock y agotados) para que el front pinte las tarjetas y
    las tablas ejecutivas sin pedir paginacion.
    """
    _require_reporte_access(usuario)

    # ---- Totales / valorizacion (1 sola query agregada) ------------------
    agg = (
        db.query(
            func.count(Producto.id_producto).label("total"),
            func.sum(
                case((Producto.estado == "Activo", 1), else_=0)
            ).label("activos"),
            func.coalesce(
                func.sum(
                    case(
                        (Producto.estado == "Activo", Producto.stock_total),
                        else_=0,
                    )
                ),
                0,
            ).label("stock_unidades"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Producto.estado == "Activo",
                            Producto.stock_total * Producto.precio_venta,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("valor_inventario"),
        )
        .one()
    )
    total_productos = int(agg.total or 0)
    productos_activos = int(agg.activos or 0)
    total_stock_unidades = int(agg.stock_unidades or 0)
    valor_inventario = Decimal(str(agg.valor_inventario or 0))

    # ---- Top N productos con bajo stock (activos) -----------------------
    bajo_stock_rows = (
        db.query(Producto, Categoria)
        .outerjoin(Categoria, Producto.id_categoria == Categoria.id_categoria)
        .filter(
            Producto.estado == "Activo",
            Producto.stock_total <= umbral_bajo_stock,
        )
        .order_by(Producto.stock_total.asc(), Producto.nombre.asc())
        .limit(top_bajo_stock)
        .all()
    )
    productos_bajo_stock = [
        ProductoStockItem(
            id_producto=p.id_producto,
            nombre=p.nombre,
            stock_total=p.stock_total,
            precio_venta=Decimal(str(p.precio_venta)),
            valor_stock=Decimal(str(p.stock_total)) * Decimal(str(p.precio_venta)),
            estado=p.estado,
            categoria=cat.nombre if cat else None,
        )
        for p, cat in bajo_stock_rows
    ]

    # ---- Top N productos agotados ---------------------------------------
    agotados_rows = (
        db.query(Producto, Categoria)
        .outerjoin(Categoria, Producto.id_categoria == Categoria.id_categoria)
        .filter(
            (Producto.stock_total == 0) | (Producto.estado == "Agotado")
        )
        .order_by(Producto.nombre.asc())
        .limit(top_bajo_stock)
        .all()
    )
    productos_agotados = [
        ProductoStockItem(
            id_producto=p.id_producto,
            nombre=p.nombre,
            stock_total=p.stock_total,
            precio_venta=Decimal(str(p.precio_venta)),
            valor_stock=Decimal(str(p.stock_total)) * Decimal(str(p.precio_venta)),
            estado=p.estado,
            categoria=cat.nombre if cat else None,
        )
        for p, cat in agotados_rows
    ]

    return _envelope(
        InventarioReporteResponse(
            total_productos=total_productos,
            productos_activos=productos_activos,
            total_stock_unidades=total_stock_unidades,
            valor_inventario=valor_inventario,
            umbral_bajo_stock=umbral_bajo_stock,
            productos_bajo_stock=productos_bajo_stock,
            productos_agotados=productos_agotados,
            generado_en=datetime.now(timezone.utc),
        ).model_dump(mode="json")
    )


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/rendimiento-vendedores
# ---------------------------------------------------------------------------
@router.get("/rendimiento-vendedores", response_model=None)
def reporte_rendimiento_vendedores(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    fecha_desde: Optional[date] = Query(default=None, description="YYYY-MM-DD. Default: hoy - 30 dias."),
    fecha_hasta: Optional[date] = Query(default=None, description="YYYY-MM-DD. Default: hoy."),
    tipo_venta: Optional[str] = Query(
        default=None, description="Filtra: ONLINE | POS. Si se omite, muestra ambos."
    ),
):
    """CU20: Rendimiento POS / ONLINE por vendedor en el rango.

    Solo vendedores con al menos una venta en el rango aparecen
    (filtro server-side). Ordenado por ingresos DESC. Para el caso
    ONLINE, agrupa por `id_cliente` no se pide aca (el endpoint se
    llama 'rendimiento de VENDEDORES', no de clientes).

    Si `tipo_venta=POS` filtra explicitamente ventas con id_vendedor
    NOT NULL. Si `tipo_venta=ONLINE`, devuelve clientes (agrupados por
    id_cliente) como 'compradores' — util para cruzar.
    """
    _require_reporte_access(usuario)

    if fecha_desde is None or fecha_hasta is None:
        fecha_desde, fecha_hasta = _rango_default()
    _validar_rango(fecha_desde, fecha_hasta)

    dt_desde = datetime.combine(fecha_desde, time.min, tzinfo=timezone.utc)
    dt_hasta = datetime.combine(fecha_hasta, time.max, tzinfo=timezone.utc)

    # Solo vendedores con al menos una venta en el rango. Filtro POS/ONLINE
    # se aplica sobre la columna calculada.
    tipo_col = _venta_tipo_sql().label("tipo_venta")
    filtros = [
        Venta.fecha_venta >= dt_desde,
        Venta.fecha_venta <= dt_hasta,
    ]
    if tipo_venta == "POS":
        filtros.append(Venta.id_vendedor.is_not(None))
    elif tipo_venta == "ONLINE":
        # Para ONLINE, el "actor" relevante es el id_cliente. Aun asi lo
        # exponemos como vendedor=None con un grupo por id_cliente.
        filtros.append(Venta.id_vendedor.is_(None))

    # El join principal se hace contra usuarios (alias) usando la FK que
    # corresponda segun el tipo_venta. Para simplificar y reutilizar la
    # misma query, hacemos dos branches:
    if tipo_venta == "ONLINE":
        # Agrupamos por id_cliente como "comprador online".
        actor_col = Venta.id_cliente.label("id_actor")
        rows = (
            db.query(
                actor_col,
                func.coalesce(func.sum(Venta.total), 0).label("total_ingresos"),
                func.count(Venta.id_venta).label("ops"),
                func.min(Venta.fecha_venta).label("primera"),
                func.max(Venta.fecha_venta).label("ultima"),
            )
            .filter(*filtros)
            .group_by(actor_col)
            .order_by(func.sum(Venta.total).desc())
            .all()
        )
        actor_ids = [r.id_actor for r in rows]
        usuarios = {
            u.id_usuario: u
            for u in db.query(Usuario).filter(Usuario.id_usuario.in_(actor_ids)).all()
        }
    else:
        # POS (o ambos): agrupamos por id_vendedor, pero excluimos NULL
        # para no traer filas "sin vendedor" en el caso "ambos".
        filtros_pos = filtros + [Venta.id_vendedor.is_not(None)]
        actor_col = Venta.id_vendedor.label("id_actor")
        rows = (
            db.query(
                actor_col,
                func.coalesce(func.sum(Venta.total), 0).label("total_ingresos"),
                func.count(Venta.id_venta).label("ops"),
                func.min(Venta.fecha_venta).label("primera"),
                func.max(Venta.fecha_venta).label("ultima"),
            )
            .filter(*filtros_pos)
            .group_by(actor_col)
            .order_by(func.sum(Venta.total).desc())
            .all()
        )
        actor_ids = [r.id_actor for r in rows]
        usuarios = {
            u.id_usuario: u
            for u in db.query(Usuario).filter(Usuario.id_usuario.in_(actor_ids)).all()
        }

    # Sub-desglose por (vendedor, tipo_venta) para tipos_venta por fila.
    tipo_rows: dict[UUID, dict[str, int]] = {}
    if rows:
        tipo_q = (
            db.query(
                actor_col,
                tipo_col,
                func.count(Venta.id_venta).label("ops"),
            )
            .filter(*filtros)
            .group_by(actor_col, tipo_col)
            .all()
        )
        for r in tipo_q:
            tipo_rows.setdefault(r.id_actor, {})[r.tipo_venta] = int(r.ops or 0)

    # Tambien para POS-only (id_vendedor NOT NULL) si vamos por 'ambos'.
    if tipo_venta is None:
        tipo_pos_q = (
            db.query(
                Venta.id_vendedor.label("id_actor"),
                tipo_col,
                func.count(Venta.id_venta).label("ops"),
            )
            .filter(
                Venta.fecha_venta >= dt_desde,
                Venta.fecha_venta <= dt_hasta,
                Venta.id_vendedor.is_not(None),
            )
            .group_by(Venta.id_vendedor, tipo_col)
            .all()
        )
        for r in tipo_pos_q:
            tipo_rows.setdefault(r.id_actor, {})[r.tipo_venta] = int(r.ops or 0)

    items: list[VendedorRendimientoItem] = []
    total_ingresos = Decimal("0")
    total_operaciones = 0
    for r in rows:
        ingresos = Decimal(str(r.total_ingresos))
        ops = int(r.ops or 0)
        ticket = (ingresos / Decimal(ops)) if ops > 0 else Decimal("0")
        u = usuarios.get(r.id_actor)
        if u is None:
            # El vendedor fue borrado (FK ondelete SET NULL deberia haberlo
            # cubierto, pero defensivamente saltamos filas huerfanas).
            continue
        items.append(
            VendedorRendimientoItem(
                id_vendedor=str(r.id_actor),
                nombre=f"{u.nombre} {u.apellido or ''}".strip(),
                correo=u.correo,
                total_ventas=ops,
                total_ingresos=ingresos,
                ticket_promedio=ticket,
                primera_venta=r.primera,
                ultima_venta=r.ultima,
                tipos_venta=tipo_rows.get(r.id_actor, {}),
            )
        )
        total_ingresos += ingresos
        total_operaciones += ops

    return _envelope(
        RendimientoVendedoresResponse(
            items=items,
            total_vendedores=len(items),
            total_ingresos=total_ingresos,
            total_operaciones=total_operaciones,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        ).model_dump(mode="json")
    )
