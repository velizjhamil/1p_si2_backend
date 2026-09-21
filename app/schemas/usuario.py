# backend/app/schemas/usuario.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# Rol anidado en las respuestas del usuario.
# NOTA de compatibilidad: el frontend Angular lee `user.rol.nombre_rol` tras
# el login — NO renombrar ni quitar campos de aquí (contrato del auth).
class RolResponse(BaseModel):
    id_rol: UUID  # SQLAlchemy devuelve UUID; Pydantic lo serializa a string en JSON
    nombre_rol: str

    model_config = ConfigDict(from_attributes=True)


# Datos requeridos para crear un usuario (CU3)
class UsuarioCreate(BaseModel):
    nombre: str
    apellido: str | None = None
    correo: EmailStr
    password: str = Field(min_length=6)
    nombre_rol: str  # Nombre del rol: ASU, GS, V, C o D (create-by-rol-name)
    estado: bool = True
    id_sucursal: int | None = None


# Actualización parcial (Step 2, PATCH/PUT): None = "no cambiar".
# El password llega en texto plano y se hashea al guardar en el service.
class UsuarioUpdate(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    correo: EmailStr | None = None
    password: str | None = None  # hash con obtener_hash_password() al persistir
    rol_id: UUID | None = None  # update-by-rol_id (difiere de create, que usa nombre_rol)
    estado: bool | None = None
    id_sucursal: int | None = None


# Fila liviana para tablas/grillas del frontend (Step 2: GET /api/v1/usuarios).
# fecha_creacion es None mientras la tabla `usuarios` no tenga esa columna
# (el modelo mapea solo ultima_conexion); el default la valida a None.
class UsuarioList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: UUID
    nombre: str
    apellido: str | None = None
    correo: EmailStr
    estado: bool
    rol: RolResponse
    id_sucursal: int | None = None
    sucursal_nombre: str | None = None
    fecha_creacion: datetime | None = None
    ultima_conexion: datetime | None = None


# Datos que devolveremos como respuesta (ocultamos el password).
# EXTENDIDA (Step 1): se agregan rol_id, fecha_creacion y ultima_conexion.
# Los campos originales (id_usuario..rol) quedan intactos — el login del
# frontend consume user.rol.nombre_rol y NO debe romperse.
class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: UUID  # Pydantic serializa UUID a string en JSON
    nombre: str
    apellido: str | None = None
    correo: EmailStr
    estado: bool
    rol: RolResponse
    # El ORM expone la FK como `id_rol`; el alias de validación mapea ambos
    # mundos y el JSON final emite la clave "rol_id".
    rol_id: UUID = Field(validation_alias="id_rol")
    id_sucursal: int | None = None
    sucursal_nombre: str | None = None
    # fecha_creacion existe en la tabla usuarios desde la migración step1
    # (server_default now(), backfill automático de las filas existentes)
    fecha_creacion: datetime | None = None
    ultima_conexion: datetime | None = None

