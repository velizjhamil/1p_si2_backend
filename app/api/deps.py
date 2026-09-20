# backend/app/api/deps.py
# Dependencias compartidas de la API: inyección de DB y autenticación JWT.
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt as pyjwt
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import ALGORITHM, SECRET_KEY
from app.modules.usuarios.models import Usuario

# Esquema Bearer para Swagger (auto_error=False: los 401/403 los generan
# los endpoints/deps con detail consistente con el resto del backend).
bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    """Sesión de SQLAlchemy por request (yield + close en el finally)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    """Dependencia JWT: valida el token Bearer y retorna el Usuario real.

    CU1 (login) genera tokens con {"sub": id_usuario, "rol": nombre_rol};
    esta dependencia decodifica, valida expiración y resuelve el usuario.
    - 401: sin token, token malformado/expirado o usuario inexistente.
    - 403: cuenta existente pero inactiva.
    """
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido (Authorization: Bearer).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = pyjwt.decode(credenciales.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Inicie sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario = db.get(Usuario, payload.get("sub"))
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El usuario del token ya no existe.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo.",
        )
    return usuario


def require_roles(*roles: str, detail: str | None = None):
    """Dependencia de autorización por rol (RBAC): solo pasan los roles indicados.

    - 401: sin token / token inválido (lo resuelve get_current_user).
    - 403: usuario autenticado cuyo rol NO está en `roles`.
    No hay excepción para ASU: si el rol no está listado, no accede.

    Uso: `usuario: Usuario = Depends(require_roles("C"))`.
    """

    def _dependencia(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        rol = usuario.rol.nombre_rol if usuario.rol else ""
        if rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail
                or f"Acceso restringido a los roles: {', '.join(roles)}.",
            )
        return usuario

    return _dependencia
