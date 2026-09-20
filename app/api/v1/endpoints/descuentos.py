# backend/app/api/v1/endpoints/descuentos.py
# CU12 — Gestión de Descuentos / Cupones: GET paginado, POST, PUT, DELETE.
#
# Reglas de autorización: solo usuarios autenticados (GS/ASU).
# El frontend ya filtra por rol en el guard; el backend confía en que
# el token viene, pero NO distingue rol-a-rol acá (es un CRUD de
# catálogos, análogo a categorias/proveedores). Si en el futuro se
# quiere restringir a GS/ASU específicamente, se hace con una dep nueva
# `require_roles(['GS', 'ASU'])` siguiendo el patrón de dashboard.py.
#
# Decisiones:
# - DELETE fisico solo si `usos_actuales == 0`; si ya se uso alguna vez
#   se rechaza con 409 y se sugiere desactivar (igual que proveedores).
# - Codigo de cupon: validacion 409 si ya existe (case-insensitive).
# - GET sin auth NO se expone: descuentos son internos (no van al catalogo).
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user, get_db
from app.modules.descuentos.models import Descuento
from app.modules.usuarios.models import Usuario
from app.schemas.descuento import (
    DescuentoCreate,
    DescuentoResponse,
    DescuentoUpdate,
)

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estandar del backend: {status, data, message}."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _buscar_descuento(db: Session, id_descuento: int) -> Descuento:
    """404 consistente para endpoints con path param."""
    descuento = db.get(Descuento, id_descuento)
    if not descuento:
        raise HTTPException(
            status_code=404,
            detail=f"No existe el descuento con id {id_descuento}.",
        )
    return descuento


def _validar_codigo_unico(
    db: Session, codigo: str, excluir_id: int | None = None
) -> None:
    """409 si ya existe otro descuento/cupon con ese codigo.

    Comparacion case-insensitive para que "DESCUENTO20" y "descuento20"
    choquen (es lo que el usuario espera al tipear un cupon).
    """
    if not codigo:
        return
    codigo_norm = codigo.strip().upper()
    query = db.query(Descuento).filter(Descuento.codigo.ilike(codigo_norm))
    if excluir_id is not None:
        query = query.filter(Descuento.id_descuento != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un cupon con el codigo '{codigo}'.",
        )


def _ejecutar_consulta_descuentos(
    db: Session,
    q: Optional[str] = None,
    tipo: Optional[str] = None,
    activo: Optional[bool] = None,
    vigente_hoy: Optional[bool] = None,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """Consulta paginada con filtros reutilizable para endpoints públicos y de gestión."""
    query = db.query(Descuento)

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Descuento.codigo.ilike(term),
                Descuento.nombre.ilike(term),
            )
        )
    if tipo:
        if tipo not in ("PORCENTAJE", "MONTO_FIJO"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Tipo invalido '{tipo}'. Valores permitidos: "
                    "PORCENTAJE, MONTO_FIJO."
                ),
            )
        query = query.filter(Descuento.tipo == tipo)
    if activo is not None:
        query = query.filter(Descuento.activo == activo)
    if vigente_hoy:
        hoy = date.today()
        query = query.filter(Descuento.fecha_inicio <= hoy)
        query = query.filter(
            (Descuento.fecha_fin.is_(None)) | (Descuento.fecha_fin >= hoy)
        )

    total = query.count()
    items = (
        query.order_by(Descuento.id_descuento.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [DescuentoResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


# ---------------------------------------------------------------------------
# GET — listado paginado con filtros
# ---------------------------------------------------------------------------
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_descuentos(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    q: Optional[str] = Query(
        default=None, description="Busca por codigo o nombre"
    ),
    tipo: Optional[str] = Query(
        default=None, description="PORCENTAJE | MONTO_FIJO"
    ),
    activo: Optional[bool] = Query(
        default=None, description="Filtra por estado activo/inactivo"
    ),
    vigente_hoy: Optional[bool] = Query(
        default=None,
        description="Si true, solo descuentos vigentes en la fecha actual",
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
):
    """CU12: Lista descuentos con filtros basicos y paginacion.

    Filtros:
    - q: busqueda por codigo (case-insensitive) o nombre (parcial).
    - tipo: PORCENTAJE | MONTO_FIJO.
    - activo: true/false (estado manual).
    - vigente_hoy: filtra por fecha_inicio <= hoy <= fecha_fin (o fecha_fin null).
    """
    return _ejecutar_consulta_descuentos(
        db=db,
        q=q,
        tipo=tipo,
        activo=activo,
        vigente_hoy=vigente_hoy,
        page=page,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /activos — descuentos y promociones vigentes hoy (público para clientes / tienda)
# ---------------------------------------------------------------------------
@router.get("/activos", response_model=None)
def listar_descuentos_activos(
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_optional_user),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
):
    """CU12: Descuentos y cupones vigentes en la fecha actual para clientes (acceso público)."""
    return _ejecutar_consulta_descuentos(
        db=db,
        activo=True,
        vigente_hoy=True,
        page=page,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /{id} — detalle
# ---------------------------------------------------------------------------
@router.get("/{id_descuento}", response_model=None)
def obtener_descuento(
    id_descuento: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU12: Detalle de un descuento/cupon."""
    descuento = _buscar_descuento(db, id_descuento)
    return _envelope(DescuentoResponse.model_validate(descuento))


# ---------------------------------------------------------------------------
# POST — crear
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_descuento(
    descuento_in: DescuentoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU12: Crea un descuento o cupon. Validaciones:
    - codigo unico (case-insensitive) si viene no-NULL.
    - El resto lo valida Pydantic (tipo, valor>0, fechas, etc.).
    """
    if descuento_in.codigo:
        _validar_codigo_unico(db, descuento_in.codigo)

    descuento = Descuento(
        codigo=descuento_in.codigo.strip().upper() if descuento_in.codigo else None,
        nombre=descuento_in.nombre.strip(),
        descripcion=descuento_in.descripcion,
        tipo=descuento_in.tipo,
        valor=descuento_in.valor,
        fecha_inicio=descuento_in.fecha_inicio,
        fecha_fin=descuento_in.fecha_fin,
        activo=descuento_in.activo,
        usos_maximos=descuento_in.usos_maximos,
        monto_minimo_compra=descuento_in.monto_minimo_compra,
        usos_actuales=0,
    )
    db.add(descuento)
    db.commit()
    db.refresh(descuento)

    return _envelope(
        DescuentoResponse.model_validate(descuento),
        message="Descuento registrado correctamente.",
    )


# ---------------------------------------------------------------------------
# PUT /{id} — actualizar (semantica PATCH sobre PUT)
# ---------------------------------------------------------------------------
@router.put("/{id_descuento}", response_model=None)
def actualizar_descuento(
    id_descuento: int,
    descuento_in: DescuentoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU12: Actualiza datos del descuento. None = no cambiar.

    Si cambia el codigo, se revalida unicidad.
    Si cambia `tipo` o `valor`, se revalidan combinaciones (ej. PORCENTAJE
    no puede pasar de 100 — esto lo cubre el _validar_update del schema).
    """
    descuento = _buscar_descuento(db, id_descuento)

    # Validar unicidad de codigo solo si viene y cambia
    if (
        descuento_in.codigo is not None
        and descuento_in.codigo.strip().upper()
        != (descuento.codigo or "")
    ):
        _validar_codigo_unico(
            db, descuento_in.codigo, excluir_id=descuento.id_descuento
        )
        descuento.codigo = descuento_in.codigo.strip().upper()
    # Si viene codigo="" (string vacio) lo interpretamos como "quitar codigo"
    elif descuento_in.codigo is not None and not descuento_in.codigo.strip():
        descuento.codigo = None

    for campo in (
        "nombre",
        "descripcion",
        "tipo",
        "valor",
        "fecha_inicio",
        "fecha_fin",
        "activo",
        "usos_maximos",
        "monto_minimo_compra",
    ):
        valor = getattr(descuento_in, campo)
        if valor is not None:
            # Si cambia tipo a PORCENTAJE o valor cambia, el modelo
            # ya valida en Pydantic; aca solo aplicamos.
            if campo == "tipo" and valor == "PORCENTAJE":
                # Si ya tenemos un valor, validamos manualmente (porque el
                # _validar_update del schema no cruza tipo con valor si
                # solo viene uno de los dos).
                if descuento_in.valor is not None and descuento_in.valor > 100:
                    raise HTTPException(
                        status_code=422,
                        detail="Un descuento PORCENTAJE no puede superar 100.",
                    )
                if (
                    descuento_in.valor is None
                    and descuento.tipo != "PORCENTAJE"
                    and descuento.valor and float(descuento.valor) > 100
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="El valor actual supera 100, no se puede cambiar a PORCENTAJE.",
                    )
            setattr(descuento, campo, valor)

    db.commit()
    db.refresh(descuento)

    return _envelope(
        DescuentoResponse.model_validate(descuento),
        message="Descuento actualizado correctamente.",
    )


# ---------------------------------------------------------------------------
# DELETE /{id} — eliminar o desactivar
# ---------------------------------------------------------------------------
@router.delete("/{id_descuento}", response_model=None)
def eliminar_descuento(
    id_descuento: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU12: Eliminacion con restriccion de negocio.

    Si el descuento/cupon ya fue usado (usos_actuales > 0), se IMPIDE
    la eliminacion fisica (409) y se sugiere desactivar con PUT cambiando
    `activo=false`. Si nunca se uso, se elimina fisicamente.
    Esto preserva el historial: una venta que se hizo con un cupon debe
    poder seguir mostrando el codigo del cupon aunque este se elimine.
    """
    descuento = _buscar_descuento(db, id_descuento)

    if descuento.usos_actuales > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: el descuento '{descuento.nombre}' "
                f"ya fue usado {descuento.usos_actuales} vez/veces. "
                f"Sugerencia: desactívelo (PUT activo=false) para conservar el historial."
            ),
        )

    nombre = descuento.nombre
    db.delete(descuento)
    db.commit()

    return _envelope(
        None, message=f"Descuento '{nombre}' eliminado correctamente."
    )
