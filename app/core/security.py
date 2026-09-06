# backend/app/core/security.py
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# Contexto passlib con bcrypt (combo verificado: passlib 1.7.4 + bcrypt 3.2.2)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Límite del algoritmo bcrypt: solo procesa los primeros 72 bytes
MAX_BCRYPT_BYTES = 72


def _password_bytes(password: str) -> bytes:
    """Codifica a UTF-8 y trunca a 72 bytes.

    Se usa de forma SIMÉTRICA en hash y verify para que un password
    largo verifique correctamente contra su propio hash.
    """
    return password.encode("utf-8")[:MAX_BCRYPT_BYTES]


def hash_password(password: str) -> str:
    """Hashea un password con bcrypt (truncado simétrico a 72 bytes)."""
    return pwd_context.hash(_password_bytes(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica un password plano contra su hash bcrypt."""
    try:
        return pwd_context.verify(_password_bytes(plain_password), hashed_password)
    except ValueError:
        return False


def crear_token_acceso(data: dict) -> str:
    """Crea un JWT firmado con la SECRET_KEY y expiración configurable."""
    to_encode = data.copy()
    expira = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expira, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decodificar_token(token: str) -> dict:
    """Decodifica y valida un JWT.

    Lanza jwt.ExpiredSignatureError si expiró,
    jwt.InvalidTokenError si la firma o el formato son inválidos.
    """
    return jwt.decode(
        token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
