# backend/app/api/v1/endpoints/catalogo.py
# CU24 — Temporadas y Colecciones: CRUD sobre /api/v1/colecciones y
# /api/v1/temporadas. Las tablas ya existen en la DB (migración manual
# e5f8a3b7c2d9 junto con CU9); NO se requieren migraciones nuevas.
#
# Reglas de negocio:
# - Colección: nombre único (409 si se repite).
# - Temporada: fecha_fin >= fecha_inicio (422); la vigencia (Vigente/
#   Finalizada) se deriva de la fecha actual contra el rango.
# - DELETE de colección: 409 si tiene temporadas asociadas.
# - Control de acceso: TODOS los endpoints (colecciones y temporadas) son
#   exclusivos del Administrador super usuario (ASU); JWT obligatorio (401)
#   y cualquier otro rol recibe 403.
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import inspect, or_, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.modules.inventario.models import Coleccion, Temporada
from app.schemas.catalogo import (
    ColeccionCreate,
    ColeccionRead,
    ColeccionUpdate,
    TemporadaCreate,
    TemporadaRead,
    TemporadaUpdate,
)

ROL_ASU = "ASU"


# CU24: solo el Administrador super usuario (ASU) gestiona temporadas y colecciones.
# Reutiliza el RBAC del proyecto (deps.require_roles): 401 sin token, 403 otro rol.
requerir_asu = require_roles(
    ROL_ASU,
    detail="Solo el Administrador super usuario (ASU) puede gestionar temporadas y colecciones.",
)


# Router de colecciones: /api/v1/colecciones (solo ASU)
router = APIRouter(dependencies=[Depends(requerir_asu)])

# Router de temporadas: /api/v1/temporadas (solo ASU)
temporadas_router = APIRouter(dependencies=[Depends(requerir_asu)])


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras de paginación."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _serializar_temporada(t: Temporada) -> TemporadaRead:
    """TemporadaRead con el nombre de la colección aplanado (JOIN)."""
    dto = TemporadaRead.model_validate(t)
    dto.nombre_coleccion = t.coleccion.nombre_coleccion if t.coleccion else None
    return dto


# ---------------------------------------------------------------------------
# Colecciones
# ---------------------------------------------------------------------------
def _buscar_coleccion(db: Session, id_coleccion: int) -> Coleccion:
    """404 consistente para endpoints con path param."""
    coleccion = db.get(Coleccion, id_coleccion)
    if not coleccion:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la colección con id {id_coleccion}.",
        )
    return coleccion


def _validar_nombre_coleccion_unico(
    db: Session, nombre: str, excluir_id: int | None = None
) -> None:
    """409 si ya existe una colección con ese nombre (unique en DB)."""
    query = db.query(Coleccion).filter(Coleccion.nombre_coleccion == nombre)
    if excluir_id is not None:
        query = query.filter(Coleccion.id_coleccion != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una colección llamada '{nombre}'.",
        )


# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (lección CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_colecciones(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre de colección"),
):
    """CU24: Lista completa de colecciones (dropdown del modal de temporadas)."""
    query = db.query(Coleccion)
    if q:
        query = query.filter(Coleccion.nombre_coleccion.ilike(f"%{q}%"))
    colecciones = query.order_by(Coleccion.id_coleccion).all()
    return _envelope([ColeccionRead.model_validate(c) for c in colecciones])


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_coleccion(coleccion_in: ColeccionCreate, db: Session = Depends(get_db)):
    """CU24: Registra una colección (nombre único)."""
    _validar_nombre_coleccion_unico(db, coleccion_in.nombre_coleccion)

    coleccion = Coleccion(nombre_coleccion=coleccion_in.nombre_coleccion)
    db.add(coleccion)
    db.commit()
    db.refresh(coleccion)

    return _envelope(ColeccionRead.model_validate(coleccion))


@router.put("/{id_coleccion}", response_model=None)
def actualizar_coleccion(
    id_coleccion: int, coleccion_in: ColeccionUpdate, db: Session = Depends(get_db)
):
    """CU24: Renombra una colección validando unicidad del nombre."""
    coleccion = _buscar_coleccion(db, id_coleccion)

    if (
        coleccion_in.nombre_coleccion is not None
        and coleccion_in.nombre_coleccion != coleccion.nombre_coleccion
    ):
        _validar_nombre_coleccion_unico(
            db, coleccion_in.nombre_coleccion, excluir_id=coleccion.id_coleccion
        )
        coleccion.nombre_coleccion = coleccion_in.nombre_coleccion

    db.commit()
    db.refresh(coleccion)

    return _envelope(ColeccionRead.model_validate(coleccion))


@router.delete("/{id_coleccion}", response_model=None)
def eliminar_coleccion(id_coleccion: int, db: Session = Depends(get_db)):
    """CU24: Elimina una colección.

    RESTRICCIÓN DE NEGOCIO: si tiene temporadas asociadas se IMPIDE la
    eliminación (409) para no dejar temporadas huérfanas visibles; deben
    reasignarse o eliminarse primero.
    """
    coleccion = _buscar_coleccion(db, id_coleccion)

    temporadas = db.query(Temporada).filter(
        Temporada.id_coleccion == coleccion.id_coleccion
    )
    if temporadas.count() > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la colección '{coleccion.nombre_coleccion}' "
                f"tiene {temporadas.count()} temporada(s) asociadas. "
                f"Reasigne o elimine las temporadas primero."
            ),
        )

    nombre = coleccion.nombre_coleccion
    db.delete(coleccion)
    db.commit()

    return _envelope(None, message=f"Colección '{nombre}' eliminada correctamente.")


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------
def _buscar_temporada(db: Session, id_temporada: int) -> Temporada:
    """404 consistente para endpoints con path param."""
    temporada = db.get(Temporada, id_temporada)
    if not temporada:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la temporada con id {id_temporada}.",
        )
    return temporada


def _validar_coleccion(db: Session, id_coleccion: int | None) -> None:
    """404 si la colección del payload no existe (FK opcional)."""
    if id_coleccion is None:
        return  # temporada general de la tienda, sin colección
    if not db.get(Coleccion, id_coleccion):
        raise HTTPException(
            status_code=404,
            detail=f"No existe la colección con id {id_coleccion}.",
        )


def _validar_rango_fechas(inicio: date, fin: date) -> None:
    """422 si fecha_fin < fecha_inicio (regla de negocio CU24)."""
    if fin < inicio:
        raise HTTPException(
            status_code=422,
            detail="La fecha de fin no puede ser anterior a la fecha de inicio.",
        )


def _validar_nombre_temporada_unico(
    db: Session, nombre: str, excluir_id: int | None = None
) -> None:
    """409 si ya existe una temporada con ese nombre (regla de negocio CU24)."""
    query = db.query(Temporada).filter(Temporada.nombre_temporada == nombre)
    if excluir_id is not None:
        query = query.filter(Temporada.id_temporada != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una temporada llamada '{nombre}'.",
        )


def _productos_asociados(db: Session, temporada: Temporada) -> int:
    """Cuenta productos del catálogo asociados a esta temporada.

    Dinámico (misma técnica que CU9): si la tabla `productos` no existe aún
    en la DB devuelve 0 — la restricción se activa sola cuando llegue el
    CU6 con la columna id_temporada.
    """
    inspector = inspect(db.get_bind())
    if "productos" not in inspector.get_table_names():
        return 0
    # Columna FK aún por definir en CU6: verificarla dinámicamente también
    columnas = {
        c["name"] for c in inspector.get_columns("productos")
    }
    if "id_temporada" not in columnas:
        return 0
    resultado = db.execute(
        text("SELECT COUNT(*) FROM productos WHERE id_temporada = :tid"),
        {"tid": temporada.id_temporada},
    ).scalar()
    return int(resultado or 0)


@temporadas_router.get("", response_model=None)
@temporadas_router.get("/", response_model=None)
def listar_temporadas(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Busca por nombre de temporada"),
    id_coleccion: int | None = Query(
        default=None, description="Filtra por colección (null = todas)"
    ),
    vigente: bool | None = Query(
        default=None, description="Filtra por vigencia: true/false (null = todas)"
    ),
    page: int = Query(default=1, ge=1, description="Página (base 1)"),
    limit: int = Query(default=10, ge=1, le=100, description="Registros por página"),
):
    """CU24: Lista paginada de temporadas con su colección y vigencia."""
    query = db.query(Temporada)

    if q:
        term = f"%{q}%"
        query = query.filter(Temporada.nombre_temporada.ilike(term))
    if id_coleccion is not None:
        query = query.filter(Temporada.id_coleccion == id_coleccion)
    if vigente is not None:
        hoy = date.today()
        if vigente:
            query = query.filter(
                Temporada.fecha_inicio <= hoy, Temporada.fecha_fin >= hoy
            )
        else:
            query = query.filter(
                or_(Temporada.fecha_inicio > hoy, Temporada.fecha_fin < hoy)
            )

    total = query.count()
    items = (
        query.order_by(Temporada.fecha_inicio.desc(), Temporada.id_temporada)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_temporada(t) for t in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,  # techo de división
    )


@temporadas_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@temporadas_router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_temporada(temporada_in: TemporadaCreate, db: Session = Depends(get_db)):
    """CU24: Registra una temporada validando unicidad, colección y fechas."""
    _validar_nombre_temporada_unico(db, temporada_in.nombre_temporada)
    _validar_coleccion(db, temporada_in.id_coleccion)
    _validar_rango_fechas(temporada_in.fecha_inicio, temporada_in.fecha_fin)

    temporada = Temporada(
        id_coleccion=temporada_in.id_coleccion,
        nombre_temporada=temporada_in.nombre_temporada,
        fecha_inicio=temporada_in.fecha_inicio,
        fecha_fin=temporada_in.fecha_fin,
    )
    db.add(temporada)
    db.commit()
    db.refresh(temporada)

    return _envelope(_serializar_temporada(temporada))


@temporadas_router.put("/{id_temporada}", response_model=None)
def actualizar_temporada(
    id_temporada: int, temporada_in: TemporadaUpdate, db: Session = Depends(get_db)
):
    """CU24: Actualiza una temporada (parcial: None = no cambiar).

    La regla fecha_fin >= fecha_inicio se re-valida contra la entidad
    combinada: si solo viene un extremo, se contrasta con el persistido.
    """
    temporada = _buscar_temporada(db, id_temporada)

    if temporada_in.id_coleccion is not None:
        _validar_coleccion(db, temporada_in.id_coleccion)
        temporada.id_coleccion = temporada_in.id_coleccion
    if temporada_in.nombre_temporada is not None and temporada_in.nombre_temporada != temporada.nombre_temporada:
        _validar_nombre_temporada_unico(
            db, temporada_in.nombre_temporada, excluir_id=temporada.id_temporada
        )
        temporada.nombre_temporada = temporada_in.nombre_temporada

    # Fechas: combinar payload con persistido y re-validar el rango completo
    nuevo_inicio = (
        temporada_in.fecha_inicio
        if temporada_in.fecha_inicio is not None
        else temporada.fecha_inicio
    )
    nuevo_fin = (
        temporada_in.fecha_fin
        if temporada_in.fecha_fin is not None
        else temporada.fecha_fin
    )
    _validar_rango_fechas(nuevo_inicio, nuevo_fin)
    temporada.fecha_inicio = nuevo_inicio
    temporada.fecha_fin = nuevo_fin

    db.commit()
    db.refresh(temporada)

    return _envelope(_serializar_temporada(temporada))


@temporadas_router.delete("/{id_temporada}", response_model=None)
def eliminar_temporada(id_temporada: int, db: Session = Depends(get_db)):
    """CU24: Elimina una temporada con RESTRICCIÓN DE NEGOCIO.

    Si tiene productos asociados en el catálogo, se IMPIDE la eliminación
    física (409) para conservar el historial de vigencia de esos productos.
    La verificación es DINÁMICA (misma técnica que CU9): cuando la tabla
    productos llegue con su FK id_temporada (CU6), la restricción se
    activa sin tocar este router.
    """
    temporada = _buscar_temporada(db, id_temporada)

    productos = _productos_asociados(db, temporada)
    if productos > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la temporada '{temporada.nombre_temporada}' "
                f"tiene {productos} producto(s) asociados en el catálogo."
            ),
        )

    nombre = temporada.nombre_temporada
    db.delete(temporada)
    db.commit()

    return _envelope(None, message=f"Temporada '{nombre}' eliminada correctamente.")
