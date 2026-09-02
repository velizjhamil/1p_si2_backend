# backend/app/api/v1/usuarios.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.schemas.usuario import UsuarioCreate, UsuarioResponse
from app.models.usuario import Usuario
from app.api.dependencies import get_db
from app.core.security import obtener_hash_password

router = APIRouter()

@router.post("/", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
def crear_usuario(usuario_in: UsuarioCreate, db: Session = Depends(get_db)):
    # 1. Verificar si el correo ya está registrado
    usuario_existente = db.query(Usuario).filter(Usuario.email == usuario_in.email).first()
    if usuario_existente:
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado en el sistema."
        )
    
    # 2. Encriptar la contraseña
    password_encriptada = obtener_hash_password(usuario_in.password)
    
    # 3. Crear el objeto ORM
    nuevo_usuario = Usuario(
        email=usuario_in.email,
        password_hash=password_encriptada,
        rol=usuario_in.rol
    )
    
    # 4. Guardar en PostgreSQL
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario) # Refresca para obtener el ID generado por la BD
    
    return nuevo_usuario