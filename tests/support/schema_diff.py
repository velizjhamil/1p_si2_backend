# Compara el ESQUEMA de la BD local de pruebas contra el de la BD del .env
# (Supabase). Solo lectura en ambas (sesion READ ONLY). Sirve para comprobar
# que migraciones + bootstrap reproducen la BD real.
#
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/support/schema_diff.py
#
# Compara: columnas (tipo, longitud, nulabilidad, default), constraints
# (PK/FK/UNIQUE/CHECK, sin los NOT NULL que PG 18 lista aparte) e indices de
# todas las tablas de `public`.
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from tests.support import local_db  # noqa: E402

COLUMNAS = """
SELECT table_name, column_name, data_type, character_maximum_length, numeric_precision,
       numeric_scale, is_nullable, column_default
FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2
"""
CONSTRAINTS = """
SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
WHERE n.nspname = 'public' AND c.contype <> 'n' ORDER BY 1, 2
"""
INDICES = "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2"


def _normaliza_constraint(fila: tuple) -> tuple:
    """PG 18 formatea distinto los CHECK ... ANY(ARRAY[...]) que PG 15/17 (Supabase):
    se compara el contenido, no el formato."""
    tabla, nombre, definicion = fila
    limpio = re.sub(r"::[a-z ]+(\[\])?", "", definicion)
    limpio = re.sub(r"[()\[\]\s]|ARRAY", "", limpio)
    return (tabla, nombre, limpio)


def _leer(conn) -> dict[str, set]:
    conn.set_session(readonly=True)
    cur = conn.cursor()
    out = {}
    for nombre, sql in (("columna", COLUMNAS), ("constraint", CONSTRAINTS), ("indice", INDICES)):
        cur.execute(sql)
        filas = {tuple(map(str, r)) for r in cur.fetchall()}
        out[nombre] = {_normaliza_constraint(f) for f in filas} if nombre == "constraint" else filas
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    out["tabla"] = {(r[0],) for r in cur.fetchall()}
    return out


def main() -> int:
    url = local_db.url_de_pruebas()
    if not url:
        print("Defina TEST_DATABASE_URL.", file=sys.stderr)
        return 2
    p = urlparse(url)
    local = psycopg2.connect(host=p.hostname, port=p.port or 5432, user=unquote(p.username or ""),
                             password=unquote(p.password or ""), dbname=p.path.lstrip("/"),
                             options="-c lc_messages=C")
    remoto = psycopg2.connect(get_settings().database_url, options="-c lc_messages=C")
    a, b = _leer(local), _leer(remoto)
    hay = False
    for tipo in ("tabla", "columna", "constraint", "indice"):
        solo_local, solo_remoto = sorted(a[tipo] - b[tipo]), sorted(b[tipo] - a[tipo])
        print(f"{tipo:11} local={len(a[tipo]):4} supabase={len(b[tipo]):4}  "
              f"solo_local={len(solo_local)} solo_supabase={len(solo_remoto)}")
        for x in solo_local:
            print("   + solo LOCAL   :", " | ".join(x)); hay = True
        for x in solo_remoto:
            print("   - solo SUPABASE:", " | ".join(x)); hay = True
    print("\nESQUEMAS IDENTICOS" if not hay else "\nHAY DIFERENCIAS (ver arriba)")
    return 1 if hay else 0


if __name__ == "__main__":
    raise SystemExit(main())
