# backend/app/models/__init__.py
# Re-exporta los modelos para que TODO import de app.models.* registre
# ambos mappers — evita InvalidRequestError en relationships por string
# cuando se importa un modelo aislado (patrón canónico SQLAlchemy 2.0).
from app.models.rol import Rol
from app.models.usuario import Usuario

__all__ = ["Rol", "Usuario"]
