# backend/app/api/v1/endpoints/agencia_zonas.py
# CU19 - zonas de cobertura de agencias: /api/v1/agencias-reparto/{id_agencia}/zonas
#
# Router delgado: autoriza por rol con las MISMAS dependencias que el router de
# agencias (require_roles) y delega toda la logica en
# app/modules/delivery/zonas_service.py (que repite la autorizacion y valida
# contra el usuario/rol de la BD, no contra el claim del token).
#
# Autorizacion:
#   GET                    -> ASU, GS, D (D: solo zonas de agencias habilitadas)
#   POST / PUT / DELETE    -> ASU, GS
#   V, C -> 403 | sin token / token invalido -> 401
#
# Alcance: solo zonas. Las tarifas de cada zona viven en agencia_tarifas.py; la
# disponibilidad por ciudad y la cotizacion, en agencias.py.
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.endpoints.agencias import _envelope, _lectura, _solo_admin
from app.modules.delivery import zonas_service as service
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_zona import ZonaCreate, ZonaUpdate

router = APIRouter()


# Rutas duales ("") y ("/"): sin la barra extra Starlette responde 307 que
# con CORS + Authorization degrada a error en el navegador (leccion CU16).
@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_zonas(
    id_agencia: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
):
    """CU19: Zonas de cobertura de la agencia (ordenadas por ciudad y subzona)."""
    zonas = service.listar_zonas(db, usuario, id_agencia)
    return _envelope([service.serializar_zona(z) for z in zonas], total=len(zonas))


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_zona(
    id_agencia: int,
    payload: ZonaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Agrega una zona (ciudad + subzona opcional). 400 si la agencia
    esta deshabilitada, 404 si la agencia o la ciudad no existen, 409 si la
    zona ya existe o el nombre de ciudad es ambiguo."""
    zona = service.crear_zona(db, usuario, id_agencia, payload)
    return _envelope(service.serializar_zona(zona), message="Zona de cobertura registrada.")


@router.get("/{id_zona}", response_model=None)
def obtener_zona(
    id_agencia: int,
    id_zona: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_lectura),
):
    """CU19: Detalle de una zona. 404 si no existe o es de otra agencia."""
    return _envelope(service.serializar_zona(service.obtener_zona(db, usuario, id_agencia, id_zona)))


@router.put("/{id_zona}", response_model=None)
def actualizar_zona(
    id_agencia: int,
    id_zona: int,
    payload: ZonaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Cambia ciudad y/o subzona (ausente = no cambiar). Body vacio ->
    400; agencia deshabilitada -> 400; cobertura duplicada -> 409."""
    zona = service.actualizar_zona(db, usuario, id_agencia, id_zona, payload)
    return _envelope(service.serializar_zona(zona), message="Zona de cobertura actualizada.")


@router.delete("/{id_zona}", response_model=None)
def eliminar_zona(
    id_agencia: int,
    id_zona: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_solo_admin),
):
    """CU19: Elimina la zona. 409 si tiene tarifas asociadas."""
    descripcion = service.eliminar_zona(db, usuario, id_agencia, id_zona)
    return _envelope(None, message=f"Zona '{descripcion}' eliminada correctamente.")
