# backend/app/services/auth_service.py
"""Lógica de negocio de autenticación — CU1 (Iniciar sesión) y CU2 (Cerrar sesión).

Los servicios reciben la sesión de BD como parámetro; nunca crean la suya.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import crear_token_acceso, decodificar_token, verify_password
from app.models.usuario import Usuario

# ---------------------------------------------------------------------------
# CU2 — lista de revocación de tokens (logout)
# ---------------------------------------------------------------------------
# Limitación conocida: blocklist EN MEMORIA — se limpia al reiniciar el proceso.
# Aceptable para este proyecto académico; en producción usar Redis u otra tienda
# compartida para que funcione con múltiples workers.
_jti_bloqueados: set[str] = set()


def revocar_token(jti: str) -> None:
    """Marca el jti de un token como revocado (logout)."""
    _jti_bloqueados.add(jti)


def token_revocado(jti: str) -> bool:
    """Indica si un jti fue revocado (sesión cerrada)."""
    return jti in _jti_bloqueados


# ---------------------------------------------------------------------------
# CU1 — Iniciar sesión
# ---------------------------------------------------------------------------
def autenticar_usuario(db: Session, correo: str, password: str) -> Usuario:
    """Autentica credenciales aplicando bloqueo temporal (RNF01).

    - No revela si el correo existe (mensaje genérico 401).
    - Bloquea la cuenta MAX_INTENTOS_LOGIN intentos fallidos durante
      BLOQUEO_MINUTOS (HTTP 423).
    - Usuarios con estado False reciben 403.
    """
    usuario = db.query(Usuario).filter(Usuario.correo == correo).first()
    if usuario is None:
        # Mensaje genérico: evita enumeración de usuarios
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    ahora = datetime.now(timezone.utc)

    # Bloqueo activo → 423
    if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta > ahora:
        minutos_restantes = math.ceil(
            (usuario.bloqueado_hasta - ahora).total_seconds() / 60
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                "Cuenta bloqueada temporalmente por intentos fallidos, "
                f"intente en {minutos_restantes} minutos"
            ),
        )

    # Bloqueo vencido → reiniciar contadores y continuar
    if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta <= ahora:
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None

    # Contraseña incorrecta → contar intento, bloquear si alcanza el máximo
    if not verify_password(password, usuario.password):
        usuario.intentos_fallidos += 1
        if usuario.intentos_fallidos >= settings.MAX_INTENTOS_LOGIN:
            usuario.bloqueado_hasta = ahora + timedelta(
                minutes=settings.BLOQUEO_MINUTOS
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=(
                    "Cuenta bloqueada temporalmente por intentos fallidos, "
                    f"intente en {settings.BLOQUEO_MINUTOS} minutos"
                ),
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    # Usuario inactivo (contraseña correcta) → 403
    if not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    # Éxito: reiniciar contadores y devolver el usuario
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.commit()
    return usuario


def generar_token(usuario: Usuario) -> dict:
    """Genera el payload de respuesta del login: token + expiración + usuario."""
    payload = {
        "sub": str(usuario.id_usuario),
        "correo": usuario.correo,
        "rol": usuario.rol.nombre_rol if usuario.rol else None,
        "jti": uuid.uuid4().hex,
    }
    token = crear_token_acceso(payload)
    return {
        "access_token": token,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": usuario,
    }


# ---------------------------------------------------------------------------
# CU2 — Cerrar sesión
# ---------------------------------------------------------------------------
def cerrar_sesion(jti: str) -> None:
    """Revoca el token actual (logout)."""
    revocar_token(jti)


# ---------------------------------------------------------------------------
# Usuario actual — usado por la dependencia get_current_user
# ---------------------------------------------------------------------------
def obtener_usuario_actual(db: Session, token: str) -> Usuario:
    """Valida el JWT y devuelve el Usuario vigente.

    - Token expirado → 401 "Token expirado"
    - Token inválido → 401 "Token inválido"
    - jti revocado (logout) → 401 "Sesión cerrada"
    - Usuario eliminado → 401
    - Usuario inactivo (estado False) → 403 "Usuario inactivo"
    """
    try:
        payload = decodificar_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
        )
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    jti = payload.get("jti")
    if jti and token_revocado(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión cerrada",
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    try:
        usuario_id = uuid.UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    if not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    return usuario
