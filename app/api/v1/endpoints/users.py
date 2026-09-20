# backend/app/api/v1/endpoints/users.py
# CU3 — Gestión de Usuarios: GET/POST/PUT/toggle-status sobre /api/v1/usuarios.
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, get_db
from app.core.security import obtener_hash_password
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.models import Rol
from app.schemas.usuario import UsuarioCreate, UsuarioResponse, UsuarioUpdate

router = APIRouter()


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


def _validar_admin(usuario: Usuario) -> None:
    """Verifica que el usuario autenticado tenga rol de administrador (ASU o ADMIN)."""
    nombre_rol = usuario.rol.nombre_rol.upper() if usuario.rol and usuario.rol.nombre_rol else ""
    if nombre_rol not in ("ASU", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido a administradores.",
        )


@router.get("", response_model=None)
@router.get("/", response_model=None)
def listar_usuarios(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU3: Lista todos los usuarios (sin password) — solo Administrador."""
    _validar_admin(current_user)
    usuarios = db.query(Usuario).all()
    return _envelope([UsuarioResponse.model_validate(u) for u in usuarios])


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def crear_usuario(
    usuario_in: UsuarioCreate,
    db: Session = Depends(get_db),
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """CU3: Registra un usuario.

    - Clientes (rol 'C'): autoregistro público permitido (para app mobile y tienda).
    - Staff / Administradores (ASU, GS, V): requiere token de Administrador.
    """
    rol_solicitado = usuario_in.nombre_rol.upper() if usuario_in.nombre_rol else ""
    if rol_solicitado != "C":
        if credenciales is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de acceso requerido para registrar usuarios de staff.",
            )
        current_user = get_current_user(credenciales, db)
        _validar_admin(current_user)
    # 1. Correo único
    if db.query(Usuario).filter(Usuario.correo == usuario_in.correo).first():
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado en el sistema.",
        )

    # 2. Rol existente (es FK a la tabla roles)
    rol = db.query(Rol).filter(Rol.nombre_rol == usuario_in.nombre_rol).first()
    if not rol:
        raise HTTPException(
            status_code=400,
            detail=f"El rol '{usuario_in.nombre_rol}' no existe. Regístrelo primero.",
        )

    # 3. Hash + INSERT
    usuario = Usuario(
        nombre=usuario_in.nombre,
        apellido=usuario_in.apellido,
        correo=usuario_in.correo,
        password=obtener_hash_password(usuario_in.password),
        estado=True,
        id_rol=rol.id_rol,
        intentos_fallidos=0,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    return _envelope(UsuarioResponse.model_validate(usuario))


@router.patch("/{id_usuario}/toggle-status", response_model=None)
def alternar_estado_usuario(
    id_usuario: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU3: Activa/inactiva un usuario alternando su campo `estado` — solo Administrador."""
    _validar_admin(current_user)
    usuario = db.get(Usuario, id_usuario)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado",
        )

    usuario.estado = not usuario.estado
    db.commit()
    db.refresh(usuario)

    return _envelope(UsuarioResponse.model_validate(usuario))


@router.put("/{id_usuario}", response_model=None)
def actualizar_usuario(
    id_usuario: UUID,
    usuario_in: UsuarioUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """CU3: Actualiza nombre, apellido, correo, rol (y opcionalmente password/estado) — solo Administrador.

    None en el payload significa "no cambiar" ese campo (PATCH semantics sobre PUT).
    """
    _validar_admin(current_user)
    usuario = db.get(Usuario, id_usuario)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado",
        )

    # Correo: validar unicidad si viene en el payload
    if usuario_in.correo is not None and usuario_in.correo != usuario.correo:
        existente = db.query(Usuario).filter(Usuario.correo == usuario_in.correo).first()
        if existente:
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado en el sistema.",
            )
        usuario.correo = usuario_in.correo

    # Rol: update-by-rol_id (el create usa nombre_rol; ver UsuarioUpdate)
    if usuario_in.rol_id is not None and usuario_in.rol_id != usuario.id_rol:
        rol = db.get(Rol, usuario_in.rol_id)
        if not rol:
            raise HTTPException(
                status_code=400,
                detail=f"El rol con id '{usuario_in.rol_id}' no existe.",
            )
        usuario.id_rol = usuario_in.rol_id

    # Campos de texto: solo se pisan si vienen en el payload
    if usuario_in.nombre is not None:
        usuario.nombre = usuario_in.nombre
    if usuario_in.apellido is not None:
        usuario.apellido = usuario_in.apellido
    if usuario_in.password:
        usuario.password = obtener_hash_password(usuario_in.password)
    if usuario_in.estado is not None:
        usuario.estado = usuario_in.estado

    db.commit()
    db.refresh(usuario)

    return _envelope(UsuarioResponse.model_validate(usuario))
