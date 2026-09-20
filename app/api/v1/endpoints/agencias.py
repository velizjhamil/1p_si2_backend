# backend/app/api/v1/endpoints/agencias.py
# CU19 - Gestion de Agencias de Reparto: /api/v1/agencias-reparto
#
# Router delgado: resuelve parametros HTTP, autoriza por rol con require_roles
# y delega TODA la logica (validaciones, duplicados, visibilidad por rol,
# transacciones) en app/modules/delivery/agencias_service.py, que ademas repite
# la autorizacion como defensa en profundidad. Los codigos HTTP los emite el
# service: 403 permisos, 404 no existe/no visible para D, 409 duplicado o
# envios asociados, 422 datos invalidos, 400 update sin campos.
#
# Autorizacion (require_roles):
#   escritura (POST/PUT/PATCH/DELETE) -> ASU, GS
#   lectura   (GET)                   -> ASU, GS, D  (D: solo habilitadas y sin
#                                        datos de facturacion; lo aplica el service)
#   V, C -> 403 | sin token / token invalido -> 401
#
# Alcance: CRUD de agencias + GET /disponibles (agencias que cubren una ciudad,
# ver disponibilidad_service) + GET /{id}/cotizacion (costo de agencia para una
# ciudad, peso y volumen, ver cotizacion_service; solo lectura). Zonas y tarifas
# viven en sus propios routers; la asignacion a envios es PATCH /envios/{id}/asignar
# (CU18, ampliado; ver asignacion_agencia_service).
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.modules.delivery import agencias_service as service
from app.modules.delivery import cotizacion_service, disponibilidad_service
from app.modules.usuarios.models import Usuario
from app.schemas.agencia import AgenciaCreate, AgenciaEstadoPayload, AgenciaUpdate

router = APIRouter()

_solo_admin = require_roles(
    "ASU",
    "GS",
    detail="Solo Gerente de Sucursal o Administrador gestionan agencias de reparto.",
)
_lectura = require_roles(
    "ASU",
    "GS",
    "D",
    detail="No tiene acceso a las agencias de reparto.",
)


def _envelope(data, message: str = "Operacion exitosa", **extra) -> dict:
    """Envelope estandar del backend: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# GET / - listado paginado con visibilidad por rol
# ---------------------------------------------------------------------------
# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (leccion CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_agencias(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
    q: str | None = Query(
        default=None, description="Busca por razon social, contacto o NIT (D no busca por NIT)"
    ),
    is_active: bool | None = Query(
        default=None, description="Filtra por habilitacion (D siempre ve solo las habilitadas)"
    ),
    # `le`: tope tecnico; sin el, page=10**30 desborda el OFFSET (bigint) y da un 500.
    page: int = Query(default=1, ge=1, le=100_000),
    limit: int = Query(default=20, ge=1, le=100),
):
    """CU19: ASU/GS ven todas las agencias; D solo las habilitadas y sin datos
    de facturacion. V/C reciben 403."""
    items, total = service.listar_agencias(
        db, usuario, q=q, is_active=is_active, page=page, limit=limit
    )
    return _envelope(
        [service.serializar_agencia(a, usuario) for a in items],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


# ---------------------------------------------------------------------------
# POST / - crear (ASU/GS)
# ---------------------------------------------------------------------------
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_agencia(
    payload: AgenciaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Registra una agencia habilitada. 409 si el NIT o la razon social
    ya existen; 422 si los datos de facturacion no son validos."""
    agencia = service.crear_agencia(db, usuario, payload)
    return _envelope(
        service.serializar_detalle(db, agencia, usuario),
        message="Agencia registrada y habilitada correctamente.",
    )


# ---------------------------------------------------------------------------
# GET /disponibles - agencias habilitadas que cubren una ciudad
# ---------------------------------------------------------------------------
# IMPORTANTE (orden de rutas): esta ruta estatica se declara ANTES de
# /{id_agencia}; si no, Starlette interpretaria "disponibles" como un id.
@router.get("/disponibles", response_model=None)
def agencias_disponibles(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
    ciudad: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="Nombre de la ciudad (sin distinguir mayusculas ni tildes)",
    ),
):
    """CU19: Agencias HABILITADAS con al menos una zona en la ciudad (con o sin
    subzona; con o sin tarifas). Solo disponibilidad: no cotiza ni asigna.
    404 si la ciudad no existe, 409 si el nombre es ambiguo, 422 si viene vacio.
    ASU/GS/D; V/C reciben 403. Sin NIT ni datos de facturacion."""
    ciudad_, filas = disponibilidad_service.agencias_disponibles(db, usuario, ciudad)
    nombre = ciudad_.nombre
    return _envelope(
        [disponibilidad_service.serializar_disponible(f) for f in filas],
        message=(
            f"Agencias disponibles para {nombre}."
            if filas
            else f"No hay agencias de reparto disponibles para {nombre}."
        ),
        total=len(filas),
        ciudad=disponibilidad_service.serializar_ciudad(ciudad_),
    )


# ---------------------------------------------------------------------------
# GET /{id}/cotizacion - costo de agencia para una ciudad, un peso y un volumen
# ---------------------------------------------------------------------------
@router.get("/{id_agencia}/cotizacion", response_model=None)
def cotizar_envio(
    id_agencia: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
    ciudad: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="Nombre de la ciudad (sin distinguir mayusculas ni tildes)",
    ),
    peso_kg: Decimal = Query(
        ..., gt=0, max_digits=10, decimal_places=3, allow_inf_nan=False,
        description="Peso del envio en kg (> 0, hasta 3 decimales)",
    ),
    volumen_m3: Decimal = Query(
        ..., gt=0, max_digits=10, decimal_places=3, allow_inf_nan=False,
        description="Volumen del envio en m3 (> 0, hasta 3 decimales)",
    ),
):
    """CU19: Cotiza el COSTO DE AGENCIA (costo interno, no lo que paga el cliente)
    evaluando tarifas de PESO y de VOLUMEN. Si aplican varias: mayor costo; empate
    -> PESO antes que VOLUMEN; empate -> zona de ciudad completa; si aun hay mas
    de una equivalente -> 409. Solo lectura: no asigna ni guarda nada.
    404 agencia inexistente / sin cobertura en la ciudad / sin tarifa aplicable
    (mensajes distintos), 400 agencia deshabilitada (D: 404), 409 ciudad ambigua o
    tarifas equivalentes, 422 dimensiones invalidas. ASU/GS/D; V/C 403."""
    cotizacion = cotizacion_service.cotizar(db, usuario, id_agencia, ciudad, peso_kg, volumen_m3)
    return _envelope(
        cotizacion,
        message=(
            f"Costo de agencia: {cotizacion['costo_agencia']} "
            f"(criterio {cotizacion['criterio']}, {cotizacion['ciudad']['nombre']})."
        ),
    )


# ---------------------------------------------------------------------------
# GET /{id} - detalle
# ---------------------------------------------------------------------------
@router.get("/{id_agencia}", response_model=None)
def obtener_agencia(
    id_agencia: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
):
    """CU19: Detalle. ASU/GS ven cualquier agencia con datos de facturacion y
    totales; D solo una habilitada y sin datos de facturacion (si esta
    deshabilitada recibe 404)."""
    agencia = service.obtener_agencia(db, usuario, id_agencia)
    return _envelope(service.serializar_detalle(db, agencia, usuario))


# ---------------------------------------------------------------------------
# PUT /{id} - actualizar (ASU/GS)
# ---------------------------------------------------------------------------
@router.put("/{id_agencia}", response_model=None)
def actualizar_agencia(
    id_agencia: int,
    payload: AgenciaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Actualiza solo los campos enviados (ausente = no cambiar). Body
    vacio -> 400. No cambia la habilitacion (usar PATCH /estado)."""
    agencia = service.actualizar_agencia(db, usuario, id_agencia, payload)
    return _envelope(
        service.serializar_detalle(db, agencia, usuario),
        message="Agencia actualizada correctamente.",
    )


# ---------------------------------------------------------------------------
# PATCH /{id}/estado - habilitar / deshabilitar (ASU/GS)
# ---------------------------------------------------------------------------
@router.patch("/{id_agencia}/estado", response_model=None)
def cambiar_estado(
    id_agencia: int,
    payload: AgenciaEstadoPayload,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Habilita o deshabilita. Idempotente. Los envios historicos no se
    modifican; una agencia deshabilitada deja de ofrecerse para nuevas
    asignaciones."""
    agencia = service.cambiar_estado(db, usuario, id_agencia, payload.is_active)
    return _envelope(
        service.serializar_detalle(db, agencia, usuario),
        message="Agencia habilitada." if agencia.is_active else "Agencia deshabilitada.",
    )


# ---------------------------------------------------------------------------
# DELETE /{id} - eliminacion fisica (ASU/GS)
# ---------------------------------------------------------------------------
@router.delete("/{id_agencia}", response_model=None)
def eliminar_agencia(
    id_agencia: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Elimina fisicamente la agencia. 409 si tiene envios asociados
    (se sugiere deshabilitarla)."""
    razon_social = service.eliminar_agencia(db, usuario, id_agencia)
    return _envelope(None, message=f"Agencia '{razon_social}' eliminada correctamente.")
