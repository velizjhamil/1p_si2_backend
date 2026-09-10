# backend/app/core/security.py
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

# Configuración del algoritmo de hash
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Llave secreta para firmar los JWT y parámetros del token: ahora provienen
# de app/core/config.py (Settings + .env), sin valores hardcodeados.
_settings = get_settings()
SECRET_KEY = _settings.SECRET_KEY
ALGORITHM = _settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = _settings.ACCESS_TOKEN_EXPIRE_MINUTES
ACCESS_TOKEN_EXPIRE_SECONDS = ACCESS_TOKEN_EXPIRE_MINUTES * 60


def verificar_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def obtener_hash_password(password: str) -> str:
    # Truncamos de forma segura a 72 bytes para evitar el ValueError de bcrypt
    encoded_password = password.encode("utf-8")[:72]
    return pwd_context.hash(encoded_password)


def crear_token_acceso(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
