# backend/app/routers/auth_router.py
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decodificar_token
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.usuario import Usuario
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.schemas.common import ApiResponse
from app.services import auth_service

router = APIRouter()
bearer_scheme = HTTPBearer(auto_error=False)


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(credenciales: LoginRequest, db: Session = Depends(get_db)):
    """CU1 — Iniciar sesión (todos los actores)."""
    usuario = auth_service.autenticar_usuario(
        db, credenciales.correo, credenciales.password
    )
    token_data = auth_service.generar_token(usuario)
    return ApiResponse(
        data=TokenResponse(
            access_token=token_data["access_token"],
            expires_in=token_data["expires_in"],
            user=UserResponse.model_validate(token_data["user"]),
        ),
        message="Sesión iniciada correctamente",
    )


@router.post("/logout", response_model=ApiResponse)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU2 — Cerrar sesión (todos los actores). Revoca el token actual."""
    # get_current_user ya validó el token; extraemos el jti para revocarlo
    payload = decodificar_token(credentials.credentials)
    auth_service.cerrar_sesion(payload.get("jti", ""))
    return ApiResponse(message="Sesión cerrada correctamente")


@router.get("/me", response_model=ApiResponse[UserResponse])
def me(usuario_actual: Usuario = Depends(get_current_user)):
    """Devuelve el usuario autenticado (ejercita get_current_user)."""
    return ApiResponse(data=UserResponse.model_validate(usuario_actual))
