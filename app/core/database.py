# backend/app/core/database.py
import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Cadena de resolución de la URL de la DB (documentada también en config.py):
#   1. os.environ["DATABASE_URL"]  -> override explícito (shell/CI/producción).
#   2. Settings().database_url     -> línea DATABASE_URL del .env si existiera,
#      o las partes DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME del .env
#      compuestas como postgresql://user:pass@host:port/dbname (dialecto
#      psycopg2). Sin .env, se usan los fallbacks locales de Settings.
# NOTA: ya NO hay credenciales hardcodeadas en este archivo.
# ---------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL") or get_settings().database_url

engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Esta sesión es la que inyectaremos en nuestros endpoints para hacer consultas
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Convención de nombres para constraints (recomendada por la documentación de
# SQLAlchemy para PostgreSQL + Alembic). Genera: pk_usuarios, uq_usuarios_email,
# fk_usuarios_rol_id_roles, etc.
# IMPORTANTE: las tablas roles/usuarios YA existen en la DB tienda_ropa con
# nombres de constraint preexistentes (roles_pkey, usuarios_pkey,
# fk_usuario_id_rol, ix_roles_nombre_rol, ix_usuarios_correo, ...) que NO
# siguen esta convención. Las migraciones de CREATE TABLE nuevas sí la usan.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa ÚNICA compartida por todos los módulos.

    Un solo metadata garantiza que las foreign keys cruzadas entre paquetes
    (usuarios -> roles, sucursales -> empresas) se resuelvan dentro del mismo
    registry, y que `Base.metadata.create_all()` cree TODAS las tablas.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
