# backend/app/core/config.py
"""Configuración central del backend (pydantic-settings).

Lee variables desde el entorno del proceso y desde el archivo `.env` ubicado
en la RAÍZ del backend. La ruta del `.env` se resuelve de forma ABSOLUTA a
partir de este archivo (app/core/config.py), por lo que funciona igual se
ejecute uvicorn, alembic o el seed desde cualquier CWD.

Cadena de resolución de la URL de la DB (misma cadena en database.py y en
alembic/env.py):
    1. `os.environ["DATABASE_URL"]`  -> override explícito del shell/CI/producción.
    2. `Settings().DATABASE_URL`     -> línea DATABASE_URL del .env (si existiera).
    3. Partes DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME (.env o fallbacks de
       abajo) compuestas como postgresql://user:pass@host:port/dbname
       (dialecto psycopg2) — el .env real del proyecto define estas claves.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del backend: app/core/config.py -> app/core -> app -> <backend root>
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    """Todas las variables de entorno del backend con fallbacks sanos.

    Si el `.env` no existe o falta una clave, la app sigue funcionando con
    los valores por defecto (PostgreSQL local tienda_ropa).
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # el .env puede tener claves ajenas a Settings
    )

    # --- Base de datos (partes; el .env real define estas claves) ---
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "admin123"  # fallback local; .env la sobreescribe
    DB_NAME: str = "tienda_ropa"
    # URL completa opcional: si está definida (env o .env) TIENE prioridad
    # sobre las partes DB_* de arriba.
    DATABASE_URL: str | None = None

    # --- JWT (app/core/security.py) ---
    SECRET_KEY: str = "clave_super_secreta_atention"  # fallback; .env la sobreescribe
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # --- CORS (app/main.py) ---
    CORS_ORIGINS: list[str] = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    @property
    def database_url(self) -> str:
        """URL psycopg2 completa: DATABASE_URL explícita o partes DB_*."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    """Settings singleton: evita releer el .env en cada import."""
    return Settings()
