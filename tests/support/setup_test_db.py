# Construye la BD LOCAL de pruebas de CU20 desde cero.
#
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/support/setup_test_db.py             # crea (aborta si ya existe)
#   python tests/support/setup_test_db.py --recreate  # DROP + CREATE + todo
#
# Pasos:
#   1. Candados (tests/support/local_db.py): solo localhost y BD terminada en _test.
#   2. CREATE DATABASE (en el servidor local; nunca toca otras bases).
#   3. Bootstrap de `roles` y `usuarios` con su forma ANTERIOR a Alembic. Ninguna
#      migracion las crea (la primera solo les agrega columnas y FKs), asi que
#      `alembic upgrade head` no funciona sobre una BD vacia. Es la UNICA DDL
#      manual; se validó contra Supabase (roles/usuarios menos las columnas que
#      agregan las migraciones).
#   4. `alembic upgrade` con las 17 migraciones existentes SIN modificar, por
#      etapas, insertando entre ellas los "hechos previos" de la BD real (ver
#      ETAPAS): la empresa de CU16 y el proveedor id=7 que CU6 referencia.
#   5. Seed determinista (tests/support/seed_cu20.py).
import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import psycopg2  # noqa: E402

from tests.support import local_db  # noqa: E402

# Forma pre-Alembic de las dos tablas base (columnas, PK, FK e indices como en
# la BD real; fecha_creacion/descripcion/ultima_conexion las agregan las migraciones).
BASELINE_DDL = """
CREATE TABLE roles (
    id_rol      uuid        NOT NULL,
    nombre_rol  varchar(50) NOT NULL,
    CONSTRAINT roles_pkey PRIMARY KEY (id_rol)
);
CREATE UNIQUE INDEX ix_roles_nombre_rol ON roles (nombre_rol);

CREATE TABLE usuarios (
    id_usuario        uuid         NOT NULL,
    nombre            varchar(100) NOT NULL,
    correo            varchar(100) NOT NULL,
    password          varchar(255) NOT NULL,
    estado            boolean      NOT NULL,
    id_rol            uuid         NOT NULL,
    intentos_fallidos integer      NOT NULL,
    bloqueado_hasta   timestamptz,
    apellido          varchar(100),
    CONSTRAINT usuarios_pkey PRIMARY KEY (id_usuario),
    CONSTRAINT fk_usuario_id_rol FOREIGN KEY (id_rol) REFERENCES roles (id_rol)
);
CREATE UNIQUE INDEX ix_usuarios_correo ON usuarios (correo);
CREATE INDEX ix_usuarios_id_rol ON usuarios (id_rol);
"""


# Etapas de migracion. La BD real tiene "hechos previos" que las migraciones
# dan por sentados pero que NO crean (se hicieron a mano/por API). Se emulan
# entre etapas, sin modificar ninguna migracion:
#   1. hasta `empresas` (816f55fba873): CU17 siembra sucursales con
#      `INSERT ... SELECT FROM empresas LIMIT 1` ("la empresa existe por CU16").
#   2. hasta `proveedores` (c3d7e9a1f4b2): CU6 siembra productos con
#      id_proveedor = 7 hardcodeado; en la BD real ese proveedor existe (los
#      ids reales son 2,3,4,5,7), en una BD nueva CU23 solo crea los ids 1..5.
#   3. hasta head.
ETAPAS = [
    (
        "816f55fba873",
        "INSERT INTO empresas (razon_social, nit, is_active) "
        "VALUES ('Empresa de Pruebas CU20 S.A.', '000000000001', true)",
        "empresa previa (CU16)",
    ),
    (
        "c3d7e9a1f4b2",
        "INSERT INTO proveedores (id_proveedor, nombre, nit_rut, estado) "
        "VALUES (7, 'Proveedor 7 de pruebas CU20', '1020304057', 'Activo')",
        "proveedor id=7 que CU6 referencia",
    ),
]


def _conn(url: str, dbname: str | None = None):
    p = urlparse(url)
    return psycopg2.connect(
        host=p.hostname,
        port=p.port or 5432,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        dbname=dbname or p.path.lstrip("/"),
        options="-c lc_messages=C",  # errores ASCII (el servidor Windows usa cp1252)
        connect_timeout=10,
    )


def _paso(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true", help="DROP + CREATE de la BD de pruebas")
    args = ap.parse_args()

    url = local_db.url_de_pruebas()
    if not url:
        print("Defina TEST_DATABASE_URL (ver cabecera del script).", file=sys.stderr)
        return 2
    nombre = urlparse(url).path.lstrip("/")
    print(f"BD de pruebas: {nombre} @ {urlparse(url).hostname}:{urlparse(url).port or 5432}")

    _paso("1/5 Crear la base de datos")
    admin = _conn(url, "postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,))
        existe = cur.fetchone() is not None
        if existe and not args.recreate:
            print(f"La base {nombre!r} YA existe. Use --recreate para reconstruirla.", file=sys.stderr)
            return 3
        if existe:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (nombre,))
            cur.execute(f'DROP DATABASE "{nombre}"')
            print(f"  DROP DATABASE {nombre}")
        cur.execute(f'CREATE DATABASE "{nombre}" ENCODING \'UTF8\' TEMPLATE template0')
        print(f"  CREATE DATABASE {nombre}")
    admin.close()

    _paso("2/5 Bootstrap de roles y usuarios (base pre-Alembic)")
    with _conn(url) as conn:
        with conn.cursor() as cur:
            cur.execute(BASELINE_DDL)
    print("  roles, usuarios creadas")

    env = {**os.environ, "DATABASE_URL": url}
    _paso("3/5 alembic upgrade (migraciones existentes, sin modificar) por etapas")
    for revision, sql_previo, descripcion in ETAPAS:
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=BACKEND, env=env)
        if r.returncode:
            print(f"alembic upgrade {revision} FALLO", file=sys.stderr)
            return r.returncode
        with _conn(url) as conn, conn.cursor() as cur:
            cur.execute(sql_previo)
        print(f"  hecho previo insertado: {descripcion}")
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env)
    if r.returncode:
        print("alembic upgrade head FALLO", file=sys.stderr)
        return r.returncode

    _paso("4/5 Seed determinista de CU20")
    r = subprocess.run([sys.executable, "-m", "tests.support.seed_cu20"], cwd=BACKEND, env=env)
    if r.returncode:
        print("seed FALLO", file=sys.stderr)
        return r.returncode

    _paso("5/5 Resumen")
    with _conn(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        print("  alembic_version:", [x[0] for x in cur.fetchall()])
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        print("  tablas:", cur.fetchone()[0])
    print("\nBD de pruebas lista.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
