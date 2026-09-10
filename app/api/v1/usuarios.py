# backend/app/api/v1/usuarios.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security import obtener_hash_password
from app.models.usuario import Usuario
from app.modules.usuarios.models import Permiso, Rol
from app.schemas.permiso import PermisoRead
from app.schemas.rol import RolCreate, RolPermisosUpdate, RolRead
from app.schemas.usuario import UsuarioCreate, UsuarioResponse, UsuarioUpdate

# CU3 Gestión de Usuarios: montado en /api/v1/usuarios (main.py).
router = APIRouter()

# Catálogos de solo lectura para las pantallas de administración. Se montan
# directo bajo /api/v1 (GET /roles y GET /permisos) porque los consumen
# varias vistas (usuarios, roles, permisos), no solo la de usuarios.
catalogos_router = APIRouter()


def _envelope(data) -> dict:
    """Envelope estándar del backend: {status, data, message}."""
    return {"status": "success", "data": data, "message": "Operación exitosa"}


@router.get("/", response_model=None)
def listar_usuarios(db: Session = Depends(get_db)):
    """CU3: Lista todos los usuarios activos (sin password)."""
    usuarios = db.query(Usuario).all()
    return _envelope(
        [UsuarioResponse.model_validate(u) for u in usuarios]
    )


@router.post("/", status_code=status.HTTP_201_CREATED)
def crear_usuario(usuario_in: UsuarioCreate, db: Session = Depends(get_db)):
    # 1. Verificar si el correo ya está registrado
    usuario_existente = db.query(Usuario).filter(Usuario.correo == usuario_in.correo).first()
    if usuario_existente:
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado en el sistema.",
        )

    # 2. Validar que el rol exista (es FK a la tabla roles)
    rol = db.query(Rol).filter(Rol.nombre_rol == usuario_in.nombre_rol).first()
    if not rol:
        raise HTTPException(
            status_code=400,
            detail=f"El rol '{usuario_in.nombre_rol}' no existe. Regístrelo primero.",
        )

    # 3. Encriptar la contraseña
    password_encriptada = obtener_hash_password(usuario_in.password)

    # 4. Crear el objeto ORM (id_rol es la FK; la relación rol se resuelve sola)
    nuevo_usuario = Usuario(
        nombre=usuario_in.nombre,
        apellido=usuario_in.apellido,
        correo=usuario_in.correo,
        password=password_encriptada,
        estado=True,
        id_rol=rol.id_rol,
        intentos_fallidos=0,
    )

    # 5. Guardar en PostgreSQL
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)

    return _envelope(UsuarioResponse.model_validate(nuevo_usuario))


@router.patch("/{id_usuario}/toggle-status")
def alternar_estado_usuario(id_usuario: UUID, db: Session = Depends(get_db)):
    """CU3: Activa/inactiva un usuario alternando su campo `estado`."""
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


@router.put("/{id_usuario}")
def actualizar_usuario(
    id_usuario: UUID,
    usuario_in: UsuarioUpdate,
    db: Session = Depends(get_db),
):
    """CU3: Actualiza nombre, apellido, correo, rol (y opcionalmente password/estado).

    None en el payload significa "no cambiar" ese campo (PATCH semantics sobre PUT).
    """
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


# ---------------------------------------------------------------------------
# Catálogos de roles y permisos (consumidos por las vistas admin de Angular)
# ---------------------------------------------------------------------------

@catalogos_router.get("/roles", response_model=None)
def listar_roles(db: Session = Depends(get_db)):
    """CU4 (lectura): Lista todos los roles con sus permisos heredados."""
    roles = db.query(Rol).order_by(Rol.nombre_rol).all()
    return _envelope([RolRead.model_validate(r) for r in roles])


@catalogos_router.post("/roles", status_code=status.HTTP_201_CREATED)
def crear_rol(rol_in: RolCreate, db: Session = Depends(get_db)):
    """CU4: Crea un rol con sus permisos (matriz de la vista unificada)."""
    # 1. Nombre de rol único
    if db.query(Rol).filter(Rol.nombre_rol == rol_in.nombre_rol).first():
        raise HTTPException(
            status_code=400,
            detail=f"El rol '{rol_in.nombre_rol}' ya existe.",
        )

    # 2. Validar que todos los permisos existan antes de tocar la DB
    permisos = _obtener_permisos_validados(db, rol_in.permiso_ids)

    rol = Rol(nombre_rol=rol_in.nombre_rol, descripcion=rol_in.descripcion)
    rol.permisos = permisos
    db.add(rol)
    db.commit()
    db.refresh(rol)

    return _envelope(RolRead.model_validate(rol))


@catalogos_router.put("/roles/{id_rol}/permisos", response_model=None)
def actualizar_permisos_rol(
    id_rol: UUID,
    payload: RolPermisosUpdate,
    db: Session = Depends(get_db),
):
    """CU4+CU5: Reemplaza los permisos de un rol (matriz de checkboxes).

    Estrategia replace: la lista enviada ES el estado final del rol.
    """
    rol = db.get(Rol, id_rol)
    if not rol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol no encontrado",
        )

    permisos = _obtener_permisos_validados(db, payload.permiso_ids)
    rol.permisos = permisos
    db.commit()
    db.refresh(rol)

    return _envelope(RolRead.model_validate(rol))


def _obtener_permisos_validados(db: Session, permiso_ids: list[int]) -> list[Permiso]:
    """Resuelve y valida la lista de permisos; 400 si alguno no existe."""
    if not permiso_ids:
        return []
    permisos = db.query(Permiso).filter(Permiso.id.in_(permiso_ids)).all()
    if len(permisos) != len(set(permiso_ids)):
        encontrados = {p.id for p in permisos}
        faltantes = sorted(set(permiso_ids) - encontrados)
        raise HTTPException(
            status_code=400,
            detail=f"Permisos inexistentes: {faltantes}",
        )
    return permisos


@catalogos_router.get("/permisos", response_model=None)
def listar_permisos(db: Session = Depends(get_db)):
    """CU5 (lectura): Lista los permisos agrupados por módulo.

    El frontend renderiza un bloque por módulo (Usuarios, Ventas, ...), así
    que agrupamos aquí y evitamos lógica de agrupamiento en el cliente.
    """
    permisos = db.query(Permiso).order_by(Permiso.modulo, Permiso.id).all()
    agrupados: dict[str, list[PermisoRead]] = {}
    for permiso in permisos:
        agrupados.setdefault(permiso.modulo, []).append(
            PermisoRead.model_validate(permiso)
        )

    return _envelope(agrupados)
