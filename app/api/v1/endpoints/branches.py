# backend/app/api/v1/endpoints/branches.py
# CU17 — Gestión de Sucursales: GET/POST/PUT/DELETE sobre /api/v1/sucursales
# + catálogo de ciudades (GET /api/v1/ciudades) en el mismo módulo.
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.empresa.models import Ciudad, Empresa, Sucursal
from app.modules.usuarios.models import Rol, Usuario
from app.schemas.sucursal import (
    CiudadRead,
    GerenteResumen,
    SucursalCreate,
    SucursalRead,
    SucursalUpdate,
)
from app.schemas.usuario import UsuarioResponse

# Router principal: /api/v1/sucursales
router = APIRouter()

# Router del catálogo de ciudades: /api/v1/ciudades (dropdown del modal)
ciudades_router = APIRouter()


def _envelope(data, **extra) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    res = {"status": "success", "data": data, "message": "Operación exitosa"}
    res.update(extra)
    return res


def _obtener_empresa(db: Session) -> Empresa:
    """Primera empresa (singleton CU16); crea placeholder si no existe."""
    empresa = db.query(Empresa).order_by(Empresa.id).first()
    if empresa:
        return empresa
    empresa = Empresa(razon_social="Empresa sin configurar", nit="SIN-NIT-000", is_active=True)
    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa


def _buscar_sucursal(db: Session, codigo_sucursal: int) -> Sucursal:
    """404 consistente para todos los endpoints con path param."""
    sucursal = db.get(Sucursal, codigo_sucursal)
    if not sucursal:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la sucursal con código {codigo_sucursal}.",
        )
    return sucursal


def _validar_ciudad(db: Session, id_ciudad: int) -> Ciudad:
    """404 si la ciudad del payload no existe en el catálogo."""
    ciudad = db.get(Ciudad, id_ciudad)
    if not ciudad:
        raise HTTPException(
            status_code=404,
            detail=f"No existe la ciudad con id {id_ciudad} en el catálogo.",
        )
    return ciudad


def _validar_nombre_unico(db: Session, empresa_id: int, nombre: str, excluir_id: int | None = None) -> None:
    """409 si otra sucursal de la misma empresa ya usa ese nombre."""
    query = db.query(Sucursal).filter(
        Sucursal.empresa_id == empresa_id, Sucursal.nombre == nombre
    )
    if excluir_id is not None:
        query = query.filter(Sucursal.codigo_sucursal != excluir_id)
    if query.first():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una sucursal llamada '{nombre}' en esta empresa.",
        )


def _validar_gerente_disponible(
    db: Session, id_gerente: UUID | None, excluir_sucursal_id: int | None = None
) -> Usuario | None:
    """Valida que el usuario exista, tenga rol GS y no sea ya titular de otra sucursal activa (1 a 1)."""
    if id_gerente is None:
        return None
    gerente = db.get(Usuario, id_gerente)
    if not gerente:
        raise HTTPException(
            status_code=404,
            detail=f"No existe el usuario con id {id_gerente} para asignar como gerente.",
        )
    if not gerente.rol or gerente.rol.nombre_rol.upper() != "GS":
        raise HTTPException(
            status_code=400,
            detail=f"El usuario '{gerente.nombre} {gerente.apellido or ''}' no tiene rol de Gerente de Sucursal (GS).",
        )
    # Restricción 1 a 1: no puede ser titular de otra sucursal activa
    query_otra = db.query(Sucursal).filter(
        Sucursal.id_gerente == id_gerente,
        Sucursal.is_active == True,
    )
    if excluir_sucursal_id is not None:
        query_otra = query_otra.filter(Sucursal.codigo_sucursal != excluir_sucursal_id)

    otra_sucursal = query_otra.first()
    if otra_sucursal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El gerente '{gerente.nombre} {gerente.apellido or ''}' ya está asignado "
                f"como titular exclusivo en la sucursal '{otra_sucursal.nombre}' (Código {otra_sucursal.codigo_sucursal})."
            ),
        )
    return gerente


def _serializar_sucursal(s: Sucursal) -> dict:
    gerente_data = None
    if s.gerente:
        gerente_data = {
            "id_usuario": s.gerente.id_usuario,
            "nombre": s.gerente.nombre,
            "apellido": s.gerente.apellido,
            "correo": s.gerente.correo,
            "estado": s.gerente.estado,
        }
    return {
        "codigo_sucursal": s.codigo_sucursal,
        "nombre": s.nombre,
        "direccion": s.direccion,
        "telefono": s.telefono,
        "horario_atencion": s.horario_atencion,
        "is_active": s.is_active,
        "ciudad": CiudadRead.model_validate(s.ciudad),
        "empresa_id": s.empresa_id,
        "id_gerente": s.id_gerente,
        "gerente": gerente_data,
        "total_personal": len(s.personal) if s.personal else 0,
        "fecha_actualizacion": s.fecha_actualizacion,
    }


@router.get("/candidatos-gerentes", response_model=None)
def listar_candidatos_gerentes(db: Session = Depends(get_db)):
    """Lista todos los usuarios con rol GS para asignación de gerente titular."""
    gerentes = (
        db.query(Usuario)
        .join(Usuario.rol)
        .filter(Rol.nombre_rol == "GS", Usuario.estado == True)
        .all()
    )
    resultado = []
    for g in gerentes:
        sucursal_titular = (
            db.query(Sucursal)
            .filter(Sucursal.id_gerente == g.id_usuario, Sucursal.is_active == True)
            .first()
        )
        resultado.append(
            {
                "id_usuario": str(g.id_usuario),
                "nombre": g.nombre,
                "apellido": g.apellido,
                "correo": g.correo,
                "sucursal_asignada_codigo": sucursal_titular.codigo_sucursal if sucursal_titular else g.id_sucursal,
                "sucursal_asignada_nombre": sucursal_titular.nombre if sucursal_titular else (g.sucursal.nombre if g.sucursal else None),
                "disponible": sucursal_titular is None,
            }
        )
    return _envelope(resultado)


@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_sucursales(db: Session = Depends(get_db)):
    """CU17: Lista de sucursales con la ciudad asociada y datos de gerente titular."""
    sucursales = (
        db.query(Sucursal)
        .order_by(Sucursal.id_ciudad, Sucursal.codigo_sucursal)
        .all()
    )
    return _envelope([_serializar_sucursal(s) for s in sucursales])


@router.get("/{codigo_sucursal}/personal", response_model=None)
def listar_personal_sucursal(codigo_sucursal: int, db: Session = Depends(get_db)):
    """CU17: Lista el personal adscrito a una sucursal específica."""
    _buscar_sucursal(db, codigo_sucursal)
    usuarios = (
        db.query(Usuario)
        .filter(Usuario.id_sucursal == codigo_sucursal)
        .all()
    )
    return _envelope([UsuarioResponse.model_validate(u) for u in usuarios])


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_sucursal(sucursal_in: SucursalCreate, db: Session = Depends(get_db)):
    """CU17: Registra una nueva sucursal con ciudad y opcionalmente asigna gerente titular."""
    _validar_ciudad(db, sucursal_in.id_ciudad)
    empresa = _obtener_empresa(db)
    _validar_nombre_unico(db, empresa.id, sucursal_in.nombre)
    gerente = _validar_gerente_disponible(db, sucursal_in.id_gerente)

    sucursal = Sucursal(
        empresa_id=empresa.id,
        id_ciudad=sucursal_in.id_ciudad,
        nombre=sucursal_in.nombre,
        direccion=sucursal_in.direccion,
        telefono=sucursal_in.telefono,
        horario_atencion=sucursal_in.horario_atencion,
        id_gerente=sucursal_in.id_gerente,
        is_active=True,
    )
    db.add(sucursal)
    db.commit()
    db.refresh(sucursal)

    # Si se asignó un gerente titular, vincularlo a esta sucursal
    if gerente:
        gerente.id_sucursal = sucursal.codigo_sucursal
        db.commit()
        db.refresh(sucursal)

    return _envelope(_serializar_sucursal(sucursal))


@router.put("/{codigo_sucursal}", response_model=None)
def actualizar_sucursal(
    codigo_sucursal: int, sucursal_in: SucursalUpdate, db: Session = Depends(get_db)
):
    """CU17: Edita nombre, dirección, ciudad, horario o asignación de gerente titular."""
    sucursal = _buscar_sucursal(db, codigo_sucursal)

    if sucursal_in.id_ciudad is not None:
        _validar_ciudad(db, sucursal_in.id_ciudad)
        sucursal.id_ciudad = sucursal_in.id_ciudad
    if sucursal_in.nombre is not None and sucursal_in.nombre != sucursal.nombre:
        _validar_nombre_unico(
            db, sucursal.empresa_id, sucursal_in.nombre, excluir_id=sucursal.codigo_sucursal
        )
        sucursal.nombre = sucursal_in.nombre
    if sucursal_in.direccion is not None:
        sucursal.direccion = sucursal_in.direccion
    if sucursal_in.telefono is not None:
        sucursal.telefono = sucursal_in.telefono
    if sucursal_in.horario_atencion is not None:
        sucursal.horario_atencion = sucursal_in.horario_atencion

    if sucursal_in.id_gerente is not None:
        gerente = _validar_gerente_disponible(
            db, sucursal_in.id_gerente, excluir_sucursal_id=sucursal.codigo_sucursal
        )
        sucursal.id_gerente = sucursal_in.id_gerente
        if gerente:
            gerente.id_sucursal = sucursal.codigo_sucursal

    db.commit()
    db.refresh(sucursal)

    return _envelope(_serializar_sucursal(sucursal))


@router.delete("/{codigo_sucursal}", response_model=None)
def desactivar_sucursal(codigo_sucursal: int, db: Session = Depends(get_db)):
    """CU17: Desactiva la sucursal (soft delete, is_active=False)."""
    sucursal = _buscar_sucursal(db, codigo_sucursal)

    if not sucursal.is_active:
        raise HTTPException(
            status_code=409,
            detail=f"La sucursal '{sucursal.nombre}' ya está desactivada.",
        )

    sucursal.is_active = False
    db.commit()
    db.refresh(sucursal)

    return _envelope(_serializar_sucursal(sucursal))


# ---------------------------------------------------------------------------
# Catálogo de ciudades (mismo módulo de sucursales: dropdown del modal)
# ---------------------------------------------------------------------------

@ciudades_router.get("", response_model=None)
@ciudades_router.get("/", response_model=None)
def listar_ciudades(db: Session = Depends(get_db)):
    """CU17: Catálogo de ciudades ordenadas por departamento."""
    ciudades = (
        db.query(Ciudad).order_by(Ciudad.departamento, Ciudad.nombre).all()
    )
    return _envelope([CiudadRead.model_validate(c) for c in ciudades])

