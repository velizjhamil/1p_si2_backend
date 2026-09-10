# backend/app/api/v1/empresa.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.modules.empresa.models import Empresa
from app.schemas.empresa import EmpresaRead, EmpresaUpdate

router = APIRouter()

# CU16: perfil institucional — la tienda tiene UNA empresa (registro único).
# GET/PUT operan sobre el primer registro existente; si no hay ninguno, GET
# devuelve null y PUT crea el primero (upsert del singleton).


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


def _obtener_o_crear_empresa(db: Session) -> Empresa:
    """Retorna la primera empresa; la crea con placeholders si no existe."""
    empresa = db.query(Empresa).order_by(Empresa.id).first()
    if empresa:
        return empresa

    # Singleton vacío: GET /empresa sin datos debe responder null-data pero
    # PUT necesita una fila sobre la que hacer UPDATE. Nit placeholder único.
    empresa = Empresa(
        razon_social="Empresa sin configurar",
        nit="SIN-NIT-000",
        is_active=True,
    )
    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa


# Rutas duales ("") y ("/"): el frontend llama /api/v1/empresa SIN barra
# final y Starlette solo registraba /api/v1/empresa/ (exigiendo la barra),
# respondiendo 307 redirect — que con CORS + Authorization en el navegador
# degrada a error. Con ambas rutas registradas, cada path responde 200
# directo, sin redirect.
@router.get("", response_model=None)
@router.get("/", response_model=None)
def obtener_empresa(db: Session = Depends(get_db)):
    """CU16: Retorna los datos de la empresa (primer registro)."""
    empresa = db.query(Empresa).order_by(Empresa.id).first()
    return _envelope(EmpresaRead.model_validate(empresa) if empresa else None)


@router.put("", response_model=None)
@router.put("/", response_model=None)
def actualizar_empresa(empresa_in: EmpresaUpdate, db: Session = Depends(get_db)):
    """CU16: Actualiza (o crea) la información general de la empresa."""
    empresa = _obtener_o_crear_empresa(db)

    # NIT: validar unicidad si viene en el payload y difiere del actual
    if empresa_in.nit is not None and empresa_in.nit != empresa.nit:
        existente = db.query(Empresa).filter(Empresa.nit == empresa_in.nit).first()
        if existente and existente.id != empresa.id:
            raise HTTPException(
                status_code=400,
                detail=f"El NIT '{empresa_in.nit}' ya pertenece a otra empresa.",
            )
        empresa.nit = empresa_in.nit

    # Campos opcionales: solo se pisan si vienen en el payload
    if empresa_in.razon_social is not None:
        empresa.razon_social = empresa_in.razon_social
    if empresa_in.direccion is not None:
        empresa.direccion = empresa_in.direccion
    if empresa_in.telefono is not None:
        empresa.telefono = empresa_in.telefono
    if empresa_in.email is not None:
        empresa.email = empresa_in.email
    if empresa_in.ciudad is not None:
        empresa.ciudad = empresa_in.ciudad
    if empresa_in.logo_url is not None:
        empresa.logo_url = empresa_in.logo_url

    db.commit()
    db.refresh(empresa)

    return _envelope(EmpresaRead.model_validate(empresa))
