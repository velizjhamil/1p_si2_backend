# backend/app/api/v1/auth.py
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security import ACCESS_TOKEN_EXPIRE_SECONDS, crear_token_acceso, verificar_password
from app.models.usuario import Usuario
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.usuario import RolResponse, UsuarioResponse

router = APIRouter()

# CU1: política de bloqueo temporal tras intentos fallidos consecutivos
MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15


def _envelope(data: dict) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


@router.post("/login")
def iniciar_sesion(credenciales: LoginRequest, db: Session = Depends(get_db)):
    # 1. Buscar el usuario en la base de datos real
    usuario = db.query(Usuario).filter(Usuario.correo == credenciales.correo).first()

    # 2. Validar existencia y contrastar el hash de la contraseña
    if not usuario or not verificar_password(credenciales.password, usuario.password):
        # Registrar el intento fallido (solo si el usuario existe)
        if usuario:
            usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
            if usuario.intentos_fallidos >= MAX_INTENTOS_FALLIDOS:
                usuario.bloqueado_hasta = datetime.now(timezone.utc) + timedelta(
                    minutes=BLOQUEO_MINUTOS
                )
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    # 3. Validar que la cuenta no esté bloqueada temporalmente (CU1)
    if usuario.bloqueado_hasta and usuario.bloqueado_hasta > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Cuenta bloqueada temporalmente. Intente después de {usuario.bloqueado_hasta:%H:%M}",
        )

    # 4. Validar que la cuenta esté activa
    if not usuario.estado:
        raise HTTPException(status_code=400, detail="Usuario inactivo")

    # 5. Login exitoso: reiniciar contador de intentos fallidos
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.commit()

    # 6. Generar el JWT real firmado (el rol viaja como nombre legible)
    token = crear_token_acceso(
        data={"sub": str(usuario.id_usuario), "rol": usuario.rol.nombre_rol}
    )

    return _envelope(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_SECONDS,
            "user": UsuarioResponse.model_validate(usuario),
        }
    )
