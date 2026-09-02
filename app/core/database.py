# backend/app/core/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Credenciales de conexión (Usuario: postgres, Clave: tu_contraseña_real)
# En un futuro, sacaremos esto a un archivo oculto .env por seguridad
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:admin123@localhost:5432/tienda_ropa"

engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Esta sesión es la que inyectaremos en nuestros endpoints para hacer consultas
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)