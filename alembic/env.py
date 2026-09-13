# backend/alembic/env.py
"""Entorno de Alembic para la DB tienda_ropa.

Decisiones clave:
- URL de conexión: MISMA cadena que app/core/database.py (os.environ
  DATABASE_URL -> Settings con .env -> fallbacks), nunca hardcoded.
- target_metadata: el Base compartido de app.core.database con TODOS los
  módulos de modelos importados (igual que app/main.py) para que las FKs
  cruzadas entre paquetes se resuelvan en el mismo registry.
- include_object hook: filtra las tablas empresas/sucursales/proveedores.
  Están declaradas en el metadata de los módulos empresa/compras pero sus
  tablas físicas NO existen todavía en la DB (se crearán en pasos futuros
  con sus propias migraciones); sin el filtro, autogenerate las crearía
  junto con el delta de permisos, fuera del alcance de Step 1.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Asegura que el paquete `app` resuelva cuando alembic corre desde la raíz
# del backend (prepend_sys_path = . en alembic.ini ya lo hace; esto es un
# cinturón de seguridad si se invoca desde otro CWD).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Importa Settings PRIMERO (lee el .env) y luego Base; después TODOS los
# módulos de modelos — mismo orden que app/main.py usa para registrar el
# metadata compartido.
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base  # noqa: E402

import app.modules.usuarios.models  # noqa: E402, F401  (roles, permisos, usuarios)
import app.modules.empresa.models  # noqa: E402, F401  (empresas, ciudades, sucursales)
import app.modules.compras.models  # noqa: E402, F401  (proveedores CU23)
import app.modules.inventario.models  # noqa: E402, F401  (categorias CU9, colecciones/temporadas CU24)

# Tablas filtradas del autogenerate: ya no hay ninguna pendiente — empresas,
# sucursales y ciudades se migraron (CU16/CU17) y proveedores llega con su
# propia migración manual CU23 (más seeds, fuera del autogenerate).
TABLES_FILTRADAS: set[str] = set()


def include_object(obj, name, type_, reflected, compare_to):
    """Excluye del autogenerate las tablas fuera del alcance de Step 1.

    También excluye la PK/reflejos de esas tablas (sus FKs apuntan solo a
    tablas dentro del mismo conjunto, así que filtrar por nombre de tabla
    es suficiente).
    """
    if type_ == "table" and name in TABLES_FILTRADAS:
        return False
    return True


# Cadena de resolución de la URL — idéntica a app/core/database.py:
# 1) os.environ["DATABASE_URL"] (override explícito del shell/CI),
# 2) Settings (línea DATABASE_URL del .env si existiera, o las partes
#    DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME del .env),
# 3) fallbacks locales de Settings (localhost/tienda_ropa).
def _database_url() -> str:
    return os.environ.get("DATABASE_URL") or get_settings().database_url


config = context.config

# Anula el placeholder de alembic.ini con la URL resuelta (dialecto psycopg2:
# postgresql://..., NO postgresql+psycopg://).
config.set_main_option("sqlalchemy.url", _database_url())

# Logging desde alembic.ini (si existe la sección).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata compartido para autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin conectarse a la DB (requiere URL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: corre las migraciones contra la DB (autocommit por op)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
