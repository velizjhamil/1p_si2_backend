# backend/app/core/security.py
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

# Configuración del algoritmo de hash
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Llave secreta para firmar los JWT (¡En producción esto debe ir en el archivo .env!)
SECRET_KEY = "clave_super_secreta_atention"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

def verificar_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def obtener_hash_password(password):
    return pwd_context.hash(password)

def crear_token_acceso(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def obtener_hash_password(password: str):
    # Truncamos de forma segura a 72 bytes para evitar el ValueError de bcrypt
    encoded_password = password.encode('utf-8')[:72]
    return pwd_context.hash(encoded_password)