# backend/app/api/v1/endpoints/reportes.py
# CU20 - Gestion de Reportes (panel ejecutivo para Administrador y Gerente de Sucursal).
#
# Endpoints bajo /api/v1/reportes (prefijo puesto en main.py):
#   GET /api/v1/reportes/ventas                      - ventas del periodo (KPIs + series)
#   GET /api/v1/reportes/productos-mas-vendidos      - ranking por unidades vendidas
#   GET /api/v1/reportes/inventario                  - situacion + rotacion aproximada
#   GET /api/v1/reportes/devoluciones                - devoluciones del periodo
#   GET /api/v1/reportes/rendimiento-vendedores      - performance POS por vendedor
#
# Diseno comun:
# - READ-ONLY puro: no muta estado.
# - Los 4 primeros reportes son una capa DELGADA: RBAC + traduccion de errores
#   + envelope. Consultas y agregaciones viven en
#   app/modules/reportes/service.py; el contrato JSON son los DTOs de
#   app/schemas/reporte.py. Filtros comunes: fecha_inicio, fecha_fin,
#   categoria_id, canal_venta (todos opcionales; sin fechas = ultimos 30 dias UTC).
# - Sin resultados NO es un error: 200 con totales en cero, `sin_datos=true` y
#   el mensaje "No se encontraron datos para los parametros ingresados."
#   (tambien en `message` del envelope). Filtros invalidos -> 422.
# - `rendimiento-vendedores` conserva su implementacion y contrato originales
#   (fecha_desde/fecha_hasta, respuesta con Decimal-string); solo comparte el
#   calculo del rango por defecto.
# - RBAC: solo ASU y GS mediante `require_roles` (deps.py) en el decorador de
#   cada ruta; V, C y D reciben 403 server-side, sin token 401. Alcance de
#   datos GLOBAL (el modelo no tiene sucursal en usuarios/ventas/productos/
#   devoluciones).
# - `tipo_venta` de rendimiento-vendedores se DERIVA de `Venta.id_vendedor IS
#   NULL` (ONLINE) vs `NOT NULL` (POS); el modelo no persiste la columna.
# - Todas las fechas por defecto son UTC (ver service.hoy_utc).
# - EXPORTACION PDF/Excel: parametro `formato` (json | pdf | xlsx) en los 5
#   endpoints; ver `_respuesta` y app/modules/reportes/exportacion.py.
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterator, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.modules.reportes import exportacion, service
from app.modules.reportes.service import ReporteFiltroError
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import Venta
from app.schemas.reporte import (
    MENSAJE_SIN_DATOS,
    DevolucionesReporteResponse,
    InventarioSituacionResponse,
    ProductosMasVendidosResponse,
    RendimientoVendedoresResponse,
    VendedorRendimientoItem,
    VentasPeriodoResponse,
)


router = APIRouter()

# Roles con acceso al panel de reportes (igual que dashboard.py).
ROLES_REPORTES = ("ASU", "GS")

# Autorizacion de TODOS los reportes: la dependencia RBAC existente (deps.py),
# declarada en el decorador de cada ruta. Se resuelve ANTES de validar los
# parametros de query y de ejecutar la logica: sin token -> 401, rol no
# autorizado -> 403 (aunque los parametros sean invalidos), autorizado -> 200/422.
# No se usan los permisos granulares reportes.ver / reportes.exportar (el
# proyecto autoriza por rol; esos permisos solo existen en el seed).
_acceso_reportes = require_roles(
    *ROLES_REPORTES, detail="Su rol no tiene acceso al panel de reportes."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _envelope(data, message: str = "Operacion exitosa") -> dict:
    """Envelope estandar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": message}


def _envelope_reporte(dto) -> dict:
    """Envelope de un reporte CU20 (DTO ya validado).

    Sin datos sigue siendo 200/success: el mensaje viaja en `message` y el DTO
    trae `sin_datos=true` + `mensaje` para que Angular lo muestre.
    """
    return _envelope(
        dto.model_dump(mode="json"),
        message=MENSAJE_SIN_DATOS if dto.sin_datos else "Operacion exitosa",
    )


# Formato de salida: json (contrato de siempre) | pdf | xlsx (exportacion CU20).
# La exportacion NO tiene rutas propias: es el MISMO endpoint con el MISMO
# service, filtros, validaciones y RBAC (401/403/422 identicos); solo cambia la
# representacion del DTO que ya se calculo. Asi el archivo coincide exactamente
# con el reporte que el usuario esta viendo.
Formato = Literal["json", "pdf", "xlsx"]


def _q_formato():
    return Query(
        default="json",
        description="json (por defecto) | pdf | xlsx. pdf/xlsx descargan el reporte con estos mismos filtros.",
    )


def _respuesta(dto, tipo: str, formato: str, db: Session, extras: Optional[dict] = None):
    """json -> envelope estandar; pdf/xlsx -> archivo adjunto (solo lectura)."""
    if formato == "json":
        return _envelope_reporte(dto)
    filtros = getattr(dto, "filtros", None)
    categoria = service.nombre_categoria(db, filtros.categoria_id) if filtros else None
    archivo = exportacion.exportar(tipo, formato, dto, categoria=categoria, extras=extras)
    return Response(
        content=archivo.contenido,
        media_type=archivo.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{archivo.nombre}"',
            "Cache-Control": "no-store",  # datos de gestion: no cachear
        },
    )


@contextmanager
def _filtros_invalidos_como_422() -> Iterator[None]:
    """Traduce ReporteFiltroError del service a HTTP 422 (dato invalido)."""
    try:
        yield
    except ReporteFiltroError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )


def _validar_rango(fecha_desde: date, fecha_hasta: date) -> None:
    """422 si el rango esta invertido (solo rendimiento-vendedores; los
    reportes nuevos validan en service.construir_filtros)."""
    if fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"fecha_desde ({fecha_desde}) no puede ser mayor que "
                f"fecha_hasta ({fecha_hasta})."
            ),
        )


def _rango_default(hoy: Optional[date] = None) -> tuple[date, date]:
    """Default: ultimos 30 dias (inclusive) en UTC, cruzando mes/anio.

    Delega en service.rango_por_defecto (unica implementacion). `hoy` es
    opcional solo para poder probar con fechas fijas.
    """
    return service.rango_por_defecto(hoy)


def _venta_tipo_sql() -> case:
    """CASE que mapea id_vendedor IS NULL -> 'ONLINE' y NOT NULL -> 'POS'.
    Vive aca (no en el modelo) porque la columna no esta persistida.
    """
    return case((Venta.id_vendedor.is_(None), "ONLINE"), else_="POS")


# Parametros de filtro comunes (mismos nombres y descripcion en los 4 reportes).
def _q_fecha_inicio():
    return Query(default=None, description="YYYY-MM-DD. Default: fecha_fin - 29 dias (UTC).")


def _q_fecha_fin():
    return Query(default=None, description="YYYY-MM-DD. Default: hoy (UTC).")


def _q_categoria():
    return Query(default=None, ge=1, description="Id de categoria (debe existir).")


def _q_canal():
    return Query(default=None, description="ONLINE | POS (derivado de id_vendedor).")


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/ventas
# ---------------------------------------------------------------------------
@router.get("/ventas", response_model=None, dependencies=[Depends(_acceso_reportes)])
def reporte_ventas(
    db: Session = Depends(get_db),
    formato: Formato = _q_formato(),
    fecha_inicio: Optional[date] = _q_fecha_inicio(),
    fecha_fin: Optional[date] = _q_fecha_fin(),
    categoria_id: Optional[int] = _q_categoria(),
    canal_venta: Optional[str] = _q_canal(),
):
    """CU20: Ventas del periodo: KPIs + series por fecha/categoria/canal/metodo de pago.

    Solo ventas PAGADO. Contrato: VentasPeriodoResponse.
    """
    with _filtros_invalidos_como_422():
        filtros = service.construir_filtros(
            db, fecha_inicio, fecha_fin, categoria_id, canal_venta
        )
        resultado = service.reporte_ventas(db, filtros)
    return _respuesta(VentasPeriodoResponse.model_validate(resultado), "ventas", formato, db)


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/productos-mas-vendidos
# ---------------------------------------------------------------------------
@router.get("/productos-mas-vendidos", response_model=None, dependencies=[Depends(_acceso_reportes)])
def reporte_productos_mas_vendidos(
    db: Session = Depends(get_db),
    formato: Formato = _q_formato(),
    fecha_inicio: Optional[date] = _q_fecha_inicio(),
    fecha_fin: Optional[date] = _q_fecha_fin(),
    categoria_id: Optional[int] = _q_categoria(),
    canal_venta: Optional[str] = _q_canal(),
    top: int = Query(
        default=service.TOP_DEFAULT, ge=1, le=service.TOP_MAX,
        description="Cantidad de productos del ranking.",
    ),
):
    """CU20: Ranking de productos por unidades vendidas (desde detalle_ventas).

    Contrato: ProductosMasVendidosResponse.
    """
    with _filtros_invalidos_como_422():
        filtros = service.construir_filtros(
            db, fecha_inicio, fecha_fin, categoria_id, canal_venta
        )
        resultado = service.reporte_productos_mas_vendidos(db, filtros, top)
    return _respuesta(ProductosMasVendidosResponse.model_validate(resultado), "productos-mas-vendidos", formato, db)


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/inventario
# ---------------------------------------------------------------------------
@router.get("/inventario", response_model=None, dependencies=[Depends(_acceso_reportes)])
def reporte_inventario(
    db: Session = Depends(get_db),
    formato: Formato = _q_formato(),
    fecha_inicio: Optional[date] = Query(
        default=None, description="Ventana de la rotacion. Default: fecha_fin - 29 dias (UTC)."
    ),
    fecha_fin: Optional[date] = _q_fecha_fin(),
    categoria_id: Optional[int] = _q_categoria(),
    canal_venta: Optional[str] = Query(
        default=None, description="ONLINE | POS. Solo afecta a unidades vendidas / rotacion."
    ),
    nivel_stock: Optional[str] = Query(
        default=None, description="CRITICO (<5) | BAJO (<15) | OK. Filtra las filas."
    ),
    limite: int = Query(
        default=service.LIMITE_FILAS_DEFAULT, ge=1, le=service.LIMITE_FILAS_MAX,
        description="Maximo de filas devueltas (KPIs y conteos son del total).",
    ),
):
    """CU20: Situacion del inventario (stock global) + rotacion aproximada.

    El stock es el actual; las fechas solo afectan a unidades vendidas y
    rotacion. Contrato: InventarioSituacionResponse.
    """
    with _filtros_invalidos_como_422():
        filtros = service.construir_filtros(
            db, fecha_inicio, fecha_fin, categoria_id, canal_venta
        )
        resultado = service.reporte_inventario(db, filtros, nivel_stock, limite)
    return _respuesta(
        InventarioSituacionResponse.model_validate(resultado),
        "inventario",
        formato,
        db,
        extras={"nivel_stock": (nivel_stock or "").strip().upper() or None},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/devoluciones
# ---------------------------------------------------------------------------
@router.get("/devoluciones", response_model=None, dependencies=[Depends(_acceso_reportes)])
def reporte_devoluciones(
    db: Session = Depends(get_db),
    formato: Formato = _q_formato(),
    fecha_inicio: Optional[date] = _q_fecha_inicio(),
    fecha_fin: Optional[date] = _q_fecha_fin(),
    categoria_id: Optional[int] = _q_categoria(),
    canal_venta: Optional[str] = _q_canal(),
    estado: Optional[str] = Query(
        default=None, description="SOLICITADA | APROBADA | RECHAZADA | COMPLETADA."
    ),
    top: int = Query(
        default=service.TOP_DEFAULT, ge=1, le=service.TOP_MAX,
        description="Productos en `por_producto`.",
    ),
    limite_detalle: int = Query(
        default=service.LIMITE_FILAS_DEFAULT, ge=1, le=service.LIMITE_FILAS_MAX,
        description="Maximo de filas de `detalle`.",
    ),
):
    """CU20: Devoluciones del periodo (por fecha de solicitud).

    Contrato: DevolucionesReporteResponse.
    """
    with _filtros_invalidos_como_422():
        filtros = service.construir_filtros(
            db, fecha_inicio, fecha_fin, categoria_id, canal_venta
        )
        resultado = service.reporte_devoluciones(db, filtros, estado, top, limite_detalle)
    return _respuesta(
        DevolucionesReporteResponse.model_validate(resultado),
        "devoluciones",
        formato,
        db,
        extras={"top": top, "limite_detalle": limite_detalle},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/reportes/rendimiento-vendedores
# ---------------------------------------------------------------------------
@router.get("/rendimiento-vendedores", response_model=None, dependencies=[Depends(_acceso_reportes)])
def reporte_rendimiento_vendedores(
    db: Session = Depends(get_db),
    formato: Formato = _q_formato(),
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

    dto = RendimientoVendedoresResponse(
        items=items,
        total_vendedores=len(items),
        total_ingresos=total_ingresos,
        total_operaciones=total_operaciones,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )
    if formato != "json":  # exportacion CU20 (el JSON de siempre queda igual)
        return _respuesta(dto, "rendimiento-vendedores", formato, db, extras={"tipo_venta": tipo_venta})
    return _envelope(dto.model_dump(mode="json"))
