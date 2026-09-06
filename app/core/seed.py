# backend/app/core/seed.py
"""Bootstrap de la base de datos para CU1/CU2.

Uso: .venv\\Scripts\\python.exe -m app.core.seed

Pasos:
  a) Agrega SECRET_KEY a .env si falta (generada con secrets.token_hex(32)).
  b) Elimina la tabla legacy `usuarios` (id integer) SOLO si existe y está vacía.
  c) Crea todas las tablas del modelo (usuarios UUID + roles).
  d) Semilla 5 roles (ASU, GS, V, C, D) y el usuario administrador.
  e) Imprime un resumen (nunca imprime secretos ni hashes).

NOTA: los imports de `app.*` se hacen DENTRO de main(), después de asegurar
SECRET_KEY en .env — Settings la requiere al instanciar y en el primer arranque
aún no existe.
"""
import re
import secrets
import sys
from pathlib import Path

ENV_FILE = Path(".env")

ROLES_SEED = ["ASU", "GS", "V", "C", "D"]

ADMIN_CORREO = "admin@attention.com"
ADMIN_NOMBRE = "Administrador"
ADMIN_PASSWORD = "Admin123!"


def asegurar_secret_key() -> None:
    """Agrega SECRET_KEY a .env si no existe (nunca imprime su valor)."""
    contenido = ""
    if ENV_FILE.exists():
        contenido = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"(?m)^\s*SECRET_KEY\s*=", contenido):
        print("[OK] SECRET_KEY ya existe en .env")
        return
    nueva_clave = secrets.token_hex(32)
    with open(ENV_FILE, "a", encoding="utf-8") as f:
        prefix = "" if (not contenido or contenido.endswith("\n")) else "\n"
        f.write(f"{prefix}SECRET_KEY={nueva_clave}\n")
    print("[OK] SECRET_KEY generada y agregada a .env")


def eliminar_tablas_legacy(engine, inspect) -> None:
    """Elimina la tabla legacy `usuarios` solo si existe y está VACÍA.

    Aborta con mensaje claro si contiene filas — nunca destruye datos.
    También elimina `roles` si existiera (proyecto nuevo).
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        from sqlalchemy import text

        if inspector.has_table("usuarios"):
            filas = conn.execute(text("SELECT COUNT(*) FROM usuarios")).scalar()
            if filas > 0:
                print(
                    f"[ABORT] La tabla 'usuarios' contiene {filas} filas. "
                    "El seed no elimina tablas con datos — revise manualmente."
                )
                sys.exit(1)
            conn.execute(text("DROP TABLE usuarios"))
            print("[OK] Tabla legacy 'usuarios' (vacía) eliminada")
        if inspector.has_table("roles"):
            conn.execute(text("DROP TABLE roles"))
            print("[OK] Tabla 'roles' previa eliminada")


def main() -> None:
    print("=== Seed CU1/CU2 — Attention Backend ===")

    # a) SECRET_KEY en .env — ANTES de importar la configuración
    asegurar_secret_key()

    # Imports diferidos: Settings requiere SECRET_KEY ya presente en .env
    from sqlalchemy import inspect

    from app.core.database import SessionLocal, engine
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.rol import Rol
    from app.models.usuario import Usuario

    # b) limpiar tablas legacy/incompatibles
    eliminar_tablas_legacy(engine, inspect)

    # c) crear tablas del modelo actual
    Base.metadata.create_all(bind=engine)
    print("[OK] Tablas creadas: usuarios, roles")

    # d) sembrar roles + admin
    db = SessionLocal()
    try:
        roles = {}
        for codigo in ROLES_SEED:
            rol = Rol(nombre_rol=codigo)
            db.add(rol)
            roles[codigo] = rol
        db.commit()
        print(f"[OK] {len(roles)} roles sembrados: {ROLES_SEED}")

        admin = Usuario(
            nombre=ADMIN_NOMBRE,
            correo=ADMIN_CORREO,
            password=hash_password(ADMIN_PASSWORD),
            estado=True,
            rol=roles["ASU"],
        )
        db.add(admin)
        db.commit()
        print(f"[OK] Administrador creado: {ADMIN_CORREO}")

        # e) resumen
        total_roles = db.query(Rol).count()
        total_usuarios = db.query(Usuario).count()
        print("--- Resumen ---")
        print(f"Roles en BD: {total_roles}")
        print(f"Usuarios en BD: {total_usuarios}")
        print(f"Credenciales admin (dev): {ADMIN_CORREO} / {ADMIN_PASSWORD}")
    finally:
        db.close()

    print("=== Seed completado ===")


if __name__ == "__main__":
    main()
