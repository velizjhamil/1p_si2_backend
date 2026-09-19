# backend/app/api/v1/endpoints/notificaciones.py
# CU10 - Gestion de Notificaciones.
#
# Endpoints bajo /api/v1/notificaciones:
#   GET    /                  - lista del usuario autenticado (paginado, ?solo_no_leidas)
#   POST   /                  - crea notificacion (solo ASU/GS; resto usa ORM directo)
#   GET    /{id}              - detalle de UNA notificacion (propia o admin)
#   PATCH  /{id}/leer         - marca UNA como leida
#   PATCH  /leer-todas        - marca TODAS las del usuario como leidas
#   DELETE /{id}              - elimina UNA (propia o admin)
#
# Reglas de autorizacion (forzadas server-side, no bypaseables):
# - GET/PATCH/DELETE: cada usuario ve y modifica SOLO sus notificaciones,
#   salvo que sea ASU (Administrador) que ve/modifica todas.
# - POST: solo ASU y GS pueden crear notificaciones via HTTP. La idea es
#   que el sistema automatico (jobs de stock, devoluciones, ventas) usa
#   el ORM directo, y la creacion manual es para alertas operativas
#   (avisar a un vendedor, etc.).
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.notificaciones.models import Notificacion, TIPOS_NOTIFICACION
from app.modules.usuarios.models import Usuario
from app.schemas.notificacion import (
    NotificacionCreate,
    NotificacionListado,
    NotificacionMasivaResponse,
    NotificacionResponse,
)

router = APIRouter()


def _envelope(data, message: str = "Operacion exitosa", **extra) -> dict:
    """Envelope estandar del backend: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _es_admin(usuario: Usuario) -> bool:
    """True si el usuario es Administrador (ve/modifica cualquier notificacion)."""
    nombre = usuario.rol.nombre_rol if usuario.rol else ""
    return nombre in ("ASU", "ADMIN")


def _buscar_notificacion(
    db: Session, id_notificacion: int
) -> Notificacion:
    """404 consistente para endpoints con path param."""
    n = db.get(Notificacion, id_notificacion)
    if not n:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe la notificacion con id {id_notificacion}.",
        )
    return n


def _validar_acceso(n: Notificacion, usuario: Usuario) -> None:
    """403 si el usuario no tiene acceso a esta notificacion (y no es admin)."""
    if _es_admin(usuario):
        return
    if str(n.id_usuario) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene acceso a esta notificacion.",
        )


# ---------------------------------------------------------------------------
# GET / - listado paginado del usuario autenticado
# ---------------------------------------------------------------------------
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_notificaciones(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    solo_no_leidas: bool = Query(
        default=False,
        description="Si true, devuelve solo las no leidas (badge del header).",
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU10: Bandeja de notificaciones del usuario autenticado.

    Visibilidad:
    - Usuario normal: solo ve las suyas.
    - ASU: ve TODAS las del sistema (útil para supervision).
    """
    query = db.query(Notificacion)
    if not _es_admin(usuario):
        query = query.filter(Notificacion.id_usuario == usuario.id_usuario)
    if solo_no_leidas:
        query = query.filter(Notificacion.leida.is_(False))

    # total_no_leidas: lo consultamos SIEMPRE (alimenta el badge del header).
    q_no_leidas = db.query(func.count(Notificacion.id_notificacion)).filter(
        Notificacion.leida.is_(False)
    )
    if not _es_admin(usuario):
        q_no_leidas = q_no_leidas.filter(
            Notificacion.id_usuario == usuario.id_usuario
        )
    total_no_leidas = int(q_no_leidas.scalar() or 0)

    total = query.count()
    items = (
        query.order_by(Notificacion.fecha_creacion.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return _envelope(
        [NotificacionResponse.model_validate(n) for n in items],
        total=total,
        total_no_leidas=total_no_leidas,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit if total > 0 else 0,
    )


# ---------------------------------------------------------------------------
# GET /contador-no-leidas - atajo para el badge del header
# ---------------------------------------------------------------------------
@router.get("/contador-no-leidas", response_model=None)
def contador_no_leidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Devuelve SOLO el contador de no leidas (chequeo barato del header).

    El frontend puede llamarlo cada 60s sin pagar el costo del listado
    completo. Mismo criterio de visibilidad que el listado (admin ve el
    total global; el resto ve solo el propio).
    """
    q = db.query(func.count(Notificacion.id_notificacion)).filter(
        Notificacion.leida.is_(False)
    )
    if not _es_admin(usuario):
        q = q.filter(Notificacion.id_usuario == usuario.id_usuario)
    total = int(q.scalar() or 0)
    return _envelope({"total_no_leidas": total})


# ---------------------------------------------------------------------------
# POST / - crear notificacion (ASU/GS) — individual o masiva
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_notificacion(
    payload: NotificacionCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Crea una notificacion (individual o masiva) — solo ASU/GS.

    Modos (mutuamente excluyentes, validados por `NotificacionCreate`):
    - Individual: `id_usuario` UUID, `enviar_a_todos=False` (default).
    - Masivo:     `enviar_a_todos=True` -> fan-out a TODOS los usuarios
                  activos del sistema (una fila por destinatario, en una
                  sola transaccion atomica). `id_usuario` se ignora.

    RBAC: solo Administrador (ASU/ADMIN) o Gerente de Sucursal (GS).
    El sistema automatico (jobs de stock, devoluciones, ventas) usa
    `app.modules.notificaciones.service.emitir()` con ORM directo y
    bypasea este endpoint.
    """
    nombre_rol = usuario.rol.nombre_rol if usuario.rol else ""
    if nombre_rol not in ("ASU", "ADMIN", "GS"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo Administrador o Gerente de Sucursal pueden crear "
                "notificaciones manualmente."
            ),
        )

    # El Literal de Pydantic ya limita el tipo, pero dejamos el CHECK
    # explicito a modo de red de seguridad contra futuros refactors.
    if payload.tipo not in TIPOS_NOTIFICACION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Tipo invalido '{payload.tipo}'. Valores permitidos: "
                f"{', '.join(TIPOS_NOTIFICACION)}."
            ),
        )

    titulo = payload.titulo.strip()
    mensaje = payload.mensaje.strip()

    # --- MODO MASIVO -------------------------------------------------------
    if payload.enviar_a_todos:
        destinatarios = (
            db.query(Usuario.id_usuario)
            .filter(Usuario.estado.is_(True))
            .order_by(Usuario.id_usuario.asc())
            .all()
        )
        # Tupla -> lista plana de UUIDs.
        ids: list[UUID] = [row[0] for row in destinatarios]

        if not ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "No hay usuarios activos en el sistema para recibir "
                    "la notificacion masiva."
                ),
            )

        # bulk_save_objects emite un solo INSERT con VALUES multiples
        # (1 round-trip) en vez de N INSERTs individuales. Mas eficiente
        # que un loop con `db.add()` y respeta la transaccion unica.
        nuevas = [
            Notificacion(
                id_usuario=uid,
                titulo=titulo,
                mensaje=mensaje,
                tipo=payload.tipo,
                referencia_tipo=payload.referencia_tipo,
                referencia_id=payload.referencia_id,
            )
            for uid in ids
        ]
        try:
            db.bulk_save_objects(nuevas)
            db.commit()
        except Exception:
            db.rollback()
            raise

        body = NotificacionMasivaResponse(
            creadas=len(ids),
            destinatarios=ids,
            omitidos_inactivos=0,
        )
        return _envelope(
            body.model_dump(mode="json"),
            message=(
                f"Notificacion masiva enviada a {len(ids)} usuario(s) "
                f"activo(s)."
            ),
        )

    # --- MODO INDIVIDUAL ---------------------------------------------------
    # El validator del schema ya garantiza que id_usuario NO es None aca.
    destinatario = db.get(Usuario, payload.id_usuario)
    if not destinatario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el usuario destinatario con id {payload.id_usuario}.",
        )
    if not destinatario.estado:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El destinatario esta inactivo. No se le pueden enviar notificaciones.",
        )

    noti = Notificacion(
        id_usuario=payload.id_usuario,
        titulo=titulo,
        mensaje=mensaje,
        tipo=payload.tipo,
        referencia_tipo=payload.referencia_tipo,
        referencia_id=payload.referencia_id,
    )
    db.add(noti)
    db.commit()
    db.refresh(noti)

    return _envelope(
        NotificacionResponse.model_validate(noti),
        message="Notificacion creada correctamente.",
    )


# ---------------------------------------------------------------------------
# GET /{id} - detalle
# ---------------------------------------------------------------------------
@router.get("/{id_notificacion}", response_model=None)
def obtener_notificacion(
    id_notificacion: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Detalle de una notificacion (con validacion de acceso)."""
    n = _buscar_notificacion(db, id_notificacion)
    _validar_acceso(n, usuario)
    return _envelope(NotificacionResponse.model_validate(n))


# ---------------------------------------------------------------------------
# PATCH /{id}/leer - marcar una como leida
# ---------------------------------------------------------------------------
@router.patch("/{id_notificacion}/leer", response_model=None)
def marcar_leida(
    id_notificacion: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Marca una notificacion como leida (idempotente)."""
    n = _buscar_notificacion(db, id_notificacion)
    _validar_acceso(n, usuario)
    if not n.leida:
        n.leida = True
        db.commit()
        db.refresh(n)
    return _envelope(
        NotificacionResponse.model_validate(n),
        message="Notificacion marcada como leida.",
    )


# ---------------------------------------------------------------------------
# PATCH /leer-todas - marcar todas como leidas
# ---------------------------------------------------------------------------
@router.patch("/leer-todas", response_model=None)
def marcar_todas_leidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Marca TODAS las notificaciones del usuario como leidas.

    Solo afecta al usuario autenticado (un admin NO marca las de otros
    con un solo endpoint - eso seria destructivo; si quiere, las marca
    una por una). Devuelve la cantidad afectada.
    """
    q = db.query(Notificacion).filter(Notificacion.leida.is_(False))
    if not _es_admin(usuario):
        q = q.filter(Notificacion.id_usuario == usuario.id_usuario)
    count = q.update({Notificacion.leida: True}, synchronize_session=False)
    db.commit()
    return _envelope(
        {"marcadas": count},
        message=f"Se marcaron {count} notificacion(es) como leidas.",
    )


# ---------------------------------------------------------------------------
# DELETE /{id} - eliminar
# ---------------------------------------------------------------------------
@router.delete("/{id_notificacion}", response_model=None)
def eliminar_notificacion(
    id_notificacion: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU10: Elimina una notificacion (propia o admin).

    Borrado fisico: la notificacion desaparece de la bandeja. El cascade
    en la FK no aplica aca porque borramos la fila, no el usuario.
    """
    n = _buscar_notificacion(db, id_notificacion)
    _validar_acceso(n, usuario)
    db.delete(n)
    db.commit()
    return _envelope(
        {"id_notificacion": id_notificacion},
        message="Notificacion eliminada.",
    )
