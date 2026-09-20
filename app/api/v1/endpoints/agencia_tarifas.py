# backend/app/api/v1/endpoints/agencia_tarifas.py
# CU19 - tarifas de una zona de cobertura:
# /api/v1/agencias-reparto/{id_agencia}/zonas/{id_zona}/tarifas
#
# Router delgado: autoriza por rol con las MISMAS dependencias que los routers de
# agencias y zonas (require_roles) y delega toda la logica en
# app/modules/delivery/tarifas_service.py (que repite la autorizacion y valida
# contra el usuario/rol de la BD, no contra el claim del token).
#
# Autorizacion:
#   GET                    -> ASU, GS, D (D: solo tarifas de agencias habilitadas)
#   POST / PUT / DELETE    -> ASU, GS
#   V, C -> 403 | sin token / token invalido -> 401
#
# Alcance: solo el CRUD de tarifas. La cotizacion (GET /{id}/cotizacion, en
# agencias.py) las consume; la asignacion a envios es PATCH /envios/{id}/asignar.
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.endpoints.agencias import _envelope, _lectura, _solo_admin
from app.modules.delivery import tarifas_service as service
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_tarifa import TarifaCreate, TarifaUpdate

router = APIRouter()


# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (leccion CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_tarifas(
    id_agencia: int,
    id_zona: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
):
    """CU19: Tarifas de la zona (ordenadas por criterio, rango y vigencia)."""
    tarifas = service.listar_tarifas(db, usuario, id_agencia, id_zona)
    return _envelope(
        [service.serializar_tarifa(t, id_agencia) for t in tarifas], total=len(tarifas)
    )


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_tarifa(
    id_agencia: int,
    id_zona: int,
    payload: TarifaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Agrega una tarifa (criterio PESO|VOLUMEN, rango [min, max), costo y
    vigencia). 400 si la agencia esta deshabilitada, 404 si agencia o zona no
    existen, 409 si se solapa con otra tarifa activa de la zona y criterio."""
    tarifa = service.crear_tarifa(db, usuario, id_agencia, id_zona, payload)
    return _envelope(service.serializar_tarifa(tarifa, id_agencia), message="Tarifa registrada.")


@router.get("/{id_tarifa}", response_model=None)
def obtener_tarifa(
    id_agencia: int,
    id_zona: int,
    id_tarifa: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
):
    """CU19: Detalle de una tarifa. 404 si no existe o es de otra zona/agencia."""
    tarifa = service.obtener_tarifa(db, usuario, id_agencia, id_zona, id_tarifa)
    return _envelope(service.serializar_tarifa(tarifa, id_agencia))


@router.put("/{id_tarifa}", response_model=None)
def actualizar_tarifa(
    id_agencia: int,
    id_zona: int,
    id_tarifa: int,
    payload: TarifaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Cambia solo los campos enviados. `rango_max` / `vigente_hasta` en
    null explicito = tramo abierto / sin fin. Body vacio -> 400; agencia
    deshabilitada -> 400; solapamiento -> 409."""
    tarifa = service.actualizar_tarifa(db, usuario, id_agencia, id_zona, id_tarifa, payload)
    return _envelope(service.serializar_tarifa(tarifa, id_agencia), message="Tarifa actualizada.")


@router.delete("/{id_tarifa}", response_model=None)
def eliminar_tarifa(
    id_agencia: int,
    id_zona: int,
    id_tarifa: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Elimina la tarifa. 409 si algun envio la referencia (desactivela)."""
    descripcion = service.eliminar_tarifa(db, usuario, id_agencia, id_zona, id_tarifa)
    return _envelope(None, message=f"Tarifa {descripcion} eliminada correctamente.")
