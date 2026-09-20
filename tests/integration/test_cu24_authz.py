# Integracion CU24 - AUTORIZACION y operaciones de /api/v1/colecciones y /api/v1/temporadas
# con JWT REALES sobre la BD LOCAL de pruebas (jamas Supabase). Reutiliza la base de
# tests/support/agencias_api.py (transaccion revertida + tokens firmados con SECRET_KEY).
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_cu24_authz.py
# Matriz: ASU -> 200/201 | GS, V, D, C -> 403 | sin token / token invalido -> 401.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from app.core.database import engine  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI  # noqa: E402

COL = "/api/v1/colecciones"
TEMP = "/api/v1/temporadas"
FECHAS = {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-03-01"}


def bd_lista() -> bool:
    if local_db.modo_supabase_readonly() or not local_db.es_bd_de_pruebas_local():
        return False
    try:
        with engine.connect() as c:
            return bool(c.execute(text(
                "select to_regclass('public.colecciones') is not null "
                "and to_regclass('public.temporadas') is not null")).scalar())
    except Exception:
        return False


@unittest.skipUnless(bd_lista(), "BD local con colecciones/temporadas no disponible (ver cabecera)")
class CU24Authz(BaseAgenciasAPI):
    def http(self, metodo, ruta, rol=None, **kw):
        h = self.headers(rol) if rol else {}
        return self.client.request(metodo, ruta, headers=h, **kw)

    def crear_base(self):
        col = self.http("POST", COL, "ASU", json={"nombre_coleccion": "Col CU24 test"})
        self.assertEqual(col.status_code, 201, col.text)
        idc = col.json()["data"]["id_coleccion"]
        tmp = self.http("POST", TEMP, "ASU", json={"nombre_temporada": "Temp CU24 test", "id_coleccion": idc, **FECHAS})
        self.assertEqual(tmp.status_code, 201, tmp.text)
        return idc, tmp.json()["data"]["id_temporada"]

    def operaciones(self, idc, idt):
        return [
            ("colecciones listar", "GET", COL, {}),
            ("colecciones crear", "POST", COL, {"json": {"nombre_coleccion": "Otra CU24 test"}}),
            ("colecciones editar", "PUT", f"{COL}/{idc}", {"json": {"nombre_coleccion": "Editada CU24 test"}}),
            ("temporadas listar", "GET", TEMP, {}),
            ("temporadas crear", "POST", TEMP, {"json": {"nombre_temporada": "Otra T", **FECHAS}}),
            ("temporadas editar", "PUT", f"{TEMP}/{idt}", {"json": {"nombre_temporada": "Editada T"}}),
            ("temporadas eliminar", "DELETE", f"{TEMP}/{idt}", {}),
            ("colecciones eliminar", "DELETE", f"{COL}/{idc}", {}),  # tras eliminar la temporada
        ]

    def test_asu_ejecuta_todas_las_operaciones(self):
        idc, idt = self.crear_base()
        for nombre, metodo, ruta, kw in self.operaciones(idc, idt):
            r = self.http(metodo, ruta, "ASU", **kw)
            self.assertIn(r.status_code, (200, 201), (nombre, r.text))

    def test_otros_roles_403_y_no_modifican(self):
        idc, idt = self.crear_base()
        for rol in ("GS", "V", "D", "C"):
            for nombre, metodo, ruta, kw in self.operaciones(idc, idt):
                with self.subTest(rol=rol, op=nombre):
                    self.assertEqual(self.http(metodo, ruta, rol, **kw).status_code, 403)
        listado = self.http("GET", COL, "ASU")
        self.assertEqual(listado.status_code, 200)
        self.assertIn("Col CU24 test", [c["nombre_coleccion"] for c in listado.json()["data"]])

    def test_sin_token_o_token_invalido_401(self):
        idc, idt = self.crear_base()
        for nombre, metodo, ruta, kw in self.operaciones(idc, idt):
            with self.subTest(op=nombre):
                self.assertEqual(self.http(metodo, ruta, **kw).status_code, 401)
                r = self.client.request(metodo, ruta, headers={"Authorization": "Bearer basura"}, **kw)
                self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
