# backend/app/api/v1/endpoints/envios.py
# CU18 - Gestion de Envio: /api/v1/envios
#
# Router delgado: resuelve parametros HTTP, delega TODA la logica (maquina
# de estados, permisos por rol, historial, notificaciones, transacciones) en
# app/modules/delivery/service.py y envuelve la respuesta.
#
# Ciclo: PREPARANDO -> LISTO_ENVIO -> ASIGNADO -> EN_RUTA -> ENTREGADO, con
# INTENTO_FALLIDO -> REPROGRAMADO -> EN_RUTA como excepcion y CANCELADO como
# salida terminal. Los codigos HTTP los emite el service:
# 400 operacion invalida, 403 permisos, 404 no existe, 409 conflicto de estado.
#
# IMPORTANTE (orden de rutas): las rutas estaticas (/repartidores,
# /por-venta/{id}) se declaran ANTES de /{id_envio}; si no, Starlette las
# interpretaria como un id de envio.
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.delivery import asignacion_agencia_service, service
from app.modules.usuarios.models import Usuario
from app.schemas.envio import (
    AsignarEnvioPayload,
    CambiarEstadoPayload,
    ConfirmarPreparacionPayload,
    EnvioCreatePayload,
    IntentoFallidoPayload,
    ReprogramarEnvioPayload,
)

router = APIRouter()


def _envelope(data, message: str = "Operacion exitosa", **extra) -> dict:
    """Envelope estandar del backend: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# GET / - listado con visibilidad por rol y filtros
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (leccion CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_envios(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
    estado: str | None = Query(
        default=None,
        description="PREPARANDO | LISTO_ENVIO | ASIGNADO | EN_RUTA | ENTREGADO | "
        "INTENTO_FALLIDO | REPROGRAMADO | CANCELADO",
    ),
    q: str | None = Query(default=None, description="Busca por codigo de venta o cliente"),
    codigo_sucursal: int | None = Query(default=None, ge=1),
    id_repartidor: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU18: Listado paginado. ASU/GS ven todo; D solo lo asignado a el;
    C solo los envios de sus ventas; V recibe 403."""
    items, total = service.listar_envios(
        db,
        usuario,
        estado=estado,
        q=q,
        codigo_sucursal=codigo_sucursal,
        id_repartidor=id_repartidor,
        page=page,
        limit=limit,
    )
    return _envelope(
        [service.serializar_envio(e, usuario) for e in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


# ---------------------------------------------------------------------------
# POST / - iniciar envio manualmente (ventas a domicilio previas a CU18)
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def iniciar_envio(
    payload: EnvioCreatePayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: Crea el envio de una venta a DOMICILIO PAGADO que no lo tiene
    (solo ASU/GS). El checkout online ya lo crea automaticamente."""
    envio = service.iniciar_envio_manual(db, usuario, payload.id_venta, payload.observacion)
    return _envelope(
        service.serializar_envio(envio, usuario), message="Envio iniciado correctamente."
    )


# ---------------------------------------------------------------------------
# GET /repartidores - repartidores (rol D) disponibles para asignar
# ---------------------------------------------------------------------------
@router.get("/repartidores", response_model=None)
def listar_repartidores(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: Usuarios activos con rol D y su carga actual (solo ASU/GS)."""
    rol = usuario.rol.nombre_rol if usuario.rol else ""
    if rol not in service.ROLES_ADMIN_ENVIO:
        raise HTTPException(
            status_code=403,
            detail="Solo Gerente de Sucursal o Administrador consultan repartidores.",
        )
    return _envelope(service.listar_repartidores(db))


# ---------------------------------------------------------------------------
# GET /por-venta/{id_venta} - envio de un pedido
# ---------------------------------------------------------------------------
@router.get("/por-venta/{id_venta}", response_model=None)
def obtener_envio_por_venta(
    id_venta: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: Envio asociado a una venta (404 si la venta no tiene envio).

    El cliente solo puede consultar el de sus propias ventas."""
    envio = service.obtener_envio_por_venta(db, id_venta)
    service.validar_acceso(envio, usuario)
    return _envelope(service.serializar_envio(envio, usuario))


# ---------------------------------------------------------------------------
# GET /{id} - detalle
# ---------------------------------------------------------------------------
@router.get("/{id_envio}", response_model=None)
def obtener_envio(
    id_envio: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: Detalle del envio (pedido, entrega, repartidor, sucursal, fechas)."""
    envio = service.obtener_envio(db, id_envio)
    service.validar_acceso(envio, usuario)
    return _envelope(service.serializar_envio(envio, usuario))


# ---------------------------------------------------------------------------
# GET /{id}/historial - bitacora cronologica
# ---------------------------------------------------------------------------
@router.get("/{id_envio}/historial", response_model=None)
def obtener_historial(
    id_envio: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: Historial de estados en orden cronologico (incluye intentos
    fallidos y reprogramaciones; nunca se sobrescribe)."""
    envio = service.obtener_envio(db, id_envio)
    service.validar_acceso(envio, usuario)
    return _envelope(service.serializar_historial(envio))


# ---------------------------------------------------------------------------
# PATCH - transiciones del flujo
# ---------------------------------------------------------------------------
@router.patch("/{id_envio}/confirmar-preparacion", response_model=None)
def confirmar_preparacion(
    id_envio: int,
    payload: ConfirmarPreparacionPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: PREPARANDO -> LISTO_ENVIO fijando la sucursal responsable (ASU/GS)."""
    envio = service.confirmar_preparacion(db, usuario, id_envio, payload)
    return _envelope(
        service.serializar_envio(envio, usuario),
        message="Preparacion confirmada. El envio esta listo para asignar.",
    )


@router.patch("/{id_envio}/asignar", response_model=None)
def asignar_repartidor(
    id_envio: int,
    payload: AsignarEnvioPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: LISTO_ENVIO -> ASIGNADO con un repartidor (rol D activo) (ASU/GS).

    CU19: alternativamente con una AGENCIA de reparto (`id_agencia` + `peso_kg` +
    `volumen_m3`, exclusivo con `id_repartidor`): cotiza con las tarifas vigentes
    y guarda tarifa aplicada, costo de agencia y medidas en el envio."""
    if payload.id_agencia is not None:
        envio = asignacion_agencia_service.asignar_agencia(db, usuario, id_envio, payload)
        return _envelope(
            service.serializar_envio(envio, usuario),
            message=f"Agencia '{envio.agencia.razon_social}' asignada.",
        )
    envio = service.asignar_repartidor(db, usuario, id_envio, payload)
    return _envelope(
        service.serializar_envio(envio, usuario), message="Repartidor asignado."
    )


@router.patch("/{id_envio}/estado", response_model=None)
def cambiar_estado(
    id_envio: int,
    payload: CambiarEstadoPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: EN_RUTA | ENTREGADO | CANCELADO segun las transiciones validas.

    EN_RUTA y ENTREGADO notifican al cliente. D solo opera envios asignados
    a el y no cancela."""
    envio = service.cambiar_estado(db, usuario, id_envio, payload)
    return _envelope(
        service.serializar_envio(envio, usuario),
        message=f"Estado actualizado a {envio.estado}.",
    )


@router.patch("/{id_envio}/intento-fallido", response_model=None)
def registrar_intento_fallido(
    id_envio: int,
    payload: IntentoFallidoPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: EN_RUTA -> INTENTO_FALLIDO con motivo. Habilita la reprogramacion."""
    envio = service.registrar_intento_fallido(db, usuario, id_envio, payload)
    return _envelope(
        service.serializar_envio(envio, usuario),
        message="Intento fallido registrado. Puede reprogramar la entrega.",
    )


@router.patch("/{id_envio}/reprogramar", response_model=None)
def reprogramar_entrega(
    id_envio: int,
    payload: ReprogramarEnvioPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """CU18: INTENTO_FALLIDO -> REPROGRAMADO con nueva fecha futura."""
    envio = service.reprogramar_entrega(db, usuario, id_envio, payload)
    return _envelope(
        service.serializar_envio(envio, usuario), message="Entrega reprogramada."
    )
