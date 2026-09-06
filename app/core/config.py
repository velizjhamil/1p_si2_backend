# backend/app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración central — lee las variables desde .env (pydantic-settings)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Base de datos
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str

    # Seguridad JWT + RNF01 (bloqueo temporal tras N intentos fallidos)
    SECRET_KEY: str  # requerida — app/core/seed.py la genera y agrega a .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    MAX_INTENTOS_LOGIN: int = 5
    BLOQUEO_MINUTOS: int = 15

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
