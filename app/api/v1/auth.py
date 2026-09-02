# backend/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.schemas.auth import LoginRequest, TokenResponse
from app.models.usuario import Usuario
from app.api.dependencies import get_db
from app.core.security import verificar_password, crear_token_acceso

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
def iniciar_sesion(credenciales: LoginRequest, db: Session = Depends(get_db)):
    # 1. Buscar el usuario en la base de datos real
    usuario = db.query(Usuario).filter(Usuario.email == credenciales.email).first()

    # 2. Validar existencia y contrastar el hash de la contraseña
    if not usuario or not verificar_password(credenciales.password, usuario.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos"
        )
    
    # 3. Validar que la cuenta esté activa
    if not usuario.is_active:
        raise HTTPException(status_code=400, detail="Usuario inactivo")

    # 4. Generar el JWT real firmado
    token = crear_token_acceso(data={"sub": usuario.email, "rol": usuario.rol})

    return TokenResponse(
        access_token=token,
        token_type="Bearer",
        rol=usuario.rol
    )