# backend/app/models/usuario.py
# Compatibility shim: the canonical model definitions now live in
# app/modules/usuarios/models.py (modular layout from the package diagram).
# This re-export keeps existing imports (`from app.models.usuario import ...`)
# working while guaranteeing a single table definition in the shared Base.
from app.modules.usuarios.models import Permiso, Rol, Usuario

__all__ = ["Usuario", "Rol", "Permiso"]
