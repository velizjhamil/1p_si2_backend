# backend/app/modules/usuarios/seed.py
"""Seed del módulo usuarios (Step 1) — catálogo de permisos y asignación por rol.

Ejecutable e IDEMPOTENTE (seguro de re-ejecutar):
    python -m app.modules.usuarios.seed

Hace tres cosas, todas con semántica check-then-insert (equivalente a
INSERT ... ON CONFLICT DO NOTHING sobre la clave única `permisos.nombre`):
  1. Inserta el catálogo de permisos agrupado por módulo (solo si no existe).
  2. Completa la `descripcion` de los 5 roles reales (ASU/GS/V/C/D) SOLO si
     está vacía — un re-run NO pisa descripciones editadas a mano.
  3. Asigna permisos a cada rol según la matriz; solo inserta la asociación
     rol_permiso si aún no existe.

Política de permisos por rol (matriz de Step 1):
  ASU -> TODOS los permisos (superadministrador).
  GS  -> gestión de sucursal: usuarios.ver, sucursales.*, inventario.*,
         ventas.ver/crear, reportes.ver, delivery.*.
  V   -> ventas.ver, ventas.crear, inventario.ver.
  C   -> SIN permisos de administración (cliente; solo su propia experiencia).
  D   -> delivery.ver, delivery.gestionar.

NO toca: ultima_conexion (queda NULL hasta el primer login), passwords,
usuarios, ni la tabla usuario_permiso (permisos directos = Step 2).
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.usuarios.models import Permiso, Rol, rol_permiso

# (nombre, descripcion, modulo) — 21 permisos en 7 módulos
CATALOGO_PERMISOS: list[tuple[str, str, str]] = [
    # Módulo: Usuarios (CU3)
    ("usuarios.ver", "Ver el listado de usuarios del sistema", "Usuarios"),
    ("usuarios.crear", "Crear nuevos usuarios", "Usuarios"),
    ("usuarios.editar", "Editar datos de usuarios existentes", "Usuarios"),
    ("usuarios.inactivar", "Inactivar usuarios (baja lógica, no borrado físico)", "Usuarios"),
    # Módulo: Roles y Permisos (CU4/CU5)
    ("roles.ver", "Ver el listado de roles", "Roles y Permisos"),
    ("roles.crear", "Crear nuevos roles", "Roles y Permisos"),
    ("roles.editar", "Editar roles y su matriz de permisos", "Roles y Permisos"),
    ("roles.eliminar", "Eliminar roles sin usuarios asignados", "Roles y Permisos"),
    ("permisos.ver", "Ver el catálogo de permisos disponibles", "Roles y Permisos"),
    # Módulo: Inventario
    ("inventario.ver", "Ver stock e inventario de productos", "Inventario"),
    ("inventario.crear", "Registrar productos y entradas de stock", "Inventario"),
    ("inventario.editar", "Editar inventario, precios y stock", "Inventario"),
    # Módulo: Sucursales (CU17)
    ("sucursales.ver", "Ver sucursales de la empresa", "Sucursales"),
    ("sucursales.crear", "Crear sucursales", "Sucursales"),
    ("sucursales.editar", "Editar sucursales", "Sucursales"),
    # Módulo: Ventas
    ("ventas.ver", "Ver ventas e historial de ventas", "Ventas"),
    ("ventas.crear", "Registrar ventas", "Ventas"),
    # Módulo: Reportes
    ("reportes.ver", "Ver reportes de gestión", "Reportes"),
    ("reportes.exportar", "Exportar reportes (PDF/Excel)", "Reportes"),
    # Módulo: Delivery
    ("delivery.ver", "Ver pedidos en delivery", "Delivery"),
    ("delivery.gestionar", "Gestionar estado de los deliveries", "Delivery"),
]

# Descripciones legibles de los 5 roles reales de la DB
DESCRIPCIONES_ROLES: dict[str, str] = {
    "ASU": "Administrador del Sistema",
    "GS": "Gestor de Sucursal",
    "V": "Vendedor",
    "C": "Cliente",
    "D": "Delivery",
}

# Matriz rol -> permisos (ASU se resuelve aparte: todos)
PERMISOS_POR_ROL: dict[str, list[str]] = {
    "GS": [
        "usuarios.ver",
        "sucursales.ver",
        "sucursales.crear",
        "sucursales.editar",
        "inventario.ver",
        "inventario.crear",
        "inventario.editar",
        "ventas.ver",
        "ventas.crear",
        "reportes.ver",
        "delivery.ver",
        "delivery.gestionar",
    ],
    "V": [
        "ventas.ver",
        "ventas.crear",
        "inventario.ver",
    ],
    "C": [],  # cliente: sin permisos de administración
    "D": [
        "delivery.ver",
        "delivery.gestionar",
    ],
}


def run() -> None:
    """Ejecuta el seed de forma idempotente e imprime un resumen."""
    db = SessionLocal()
    try:
        # --- 1. Catálogo de permisos: check-then-insert por nombre único ---
        permisos_nuevos = 0
        for nombre, descripcion, modulo in CATALOGO_PERMISOS:
            existe = db.query(Permiso).filter(Permiso.nombre == nombre).first()
            if existe is None:
                db.add(Permiso(nombre=nombre, descripcion=descripcion, modulo=modulo))
                permisos_nuevos += 1
        db.flush()  # asigna IDs antes de armar la matriz

        todos_permisos = db.query(Permiso).all()
        permiso_por_nombre = {p.nombre: p.id for p in todos_permisos}

        # --- 2. Descripciones de roles: solo si están vacías ---
        roles = db.query(Rol).all()
        descripciones_completadas = 0
        for rol in roles:
            if rol.nombre_rol in DESCRIPCIONES_ROLES and not rol.descripcion:
                rol.descripcion = DESCRIPCIONES_ROLES[rol.nombre_rol]
                descripciones_completadas += 1

        # --- 3. Matriz rol_permiso: check-then-insert por (rol_id, permiso_id) ---
        asignaciones_nuevas = 0
        resumen_por_rol: dict[str, int] = {}
        for rol in roles:
            if rol.nombre_rol == "ASU":
                nombres_objetivo = [p.nombre for p in todos_permisos]
            else:
                nombres_objetivo = PERMISOS_POR_ROL.get(rol.nombre_rol, [])

            for nombre in nombres_objetivo:
                permiso_id = permiso_por_nombre[nombre]
                ya_tiene = db.execute(
                    select(rol_permiso.c.permiso_id).where(
                        rol_permiso.c.rol_id == rol.id_rol,
                        rol_permiso.c.permiso_id == permiso_id,
                    )
                ).first()
                if ya_tiene is None:
                    db.execute(
                        rol_permiso.insert().values(rol_id=rol.id_rol, permiso_id=permiso_id)
                    )
                    asignaciones_nuevas += 1

            resumen_por_rol[rol.nombre_rol] = db.execute(
                select(rol_permiso.c.permiso_id).where(rol_permiso.c.rol_id == rol.id_rol)
            ).fetchall().__len__()

        db.commit()

        # --- Resumen ---
        print("=" * 60)
        print("SEED USUARIOS/ROLES/PERMISOS — completado (idempotente)")
        print("=" * 60)
        print(f"Permisos en catálogo : {len(todos_permisos)} (nuevos esta corrida: {permisos_nuevos})")
        print(f"Descripciones de rol : {descripciones_completadas} completadas esta corrida")
        print(f"Asignaciones nuevas  : {asignaciones_nuevas}")
        print("-" * 60)
        for nombre_rol in sorted(resumen_por_rol):
            print(f"  Rol {nombre_rol:<4} -> {resumen_por_rol[nombre_rol]} permisos")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    run()
