# Integracion CU19 fase 5 - AUTORIZACION de /api/v1/agencias-reparto/{id}/zonas
# con JWT REALES sobre la BD LOCAL de pruebas (jamas Supabase ni Render). No se
# simula el usuario: se firman tokens con la SECRET_KEY de la app y se recorre
# get_current_user -> require_roles -> service (que consulta rol y estado en BD).
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_zonas_authz.py
#
#                   GET (lista/detalle)            POST/PUT/DELETE
#   ASU, GS               200                          200/201
#   D                     200 (solo agencias habilitadas, 404 si no)   403
#   V, C                  403                          403
#   sin token / invalido  401                          401
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import jwt  # noqa: E402

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from app.core.security import ALGORITHM, SECRET_KEY, crear_token_acceso  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ZonasAuthz(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        r = self.req("POST", f"/{self.ag}/zonas", "GS", json={"id_ciudad": 2, "nombre_zona": "Sopocachi"})
        self.assertEqual(r.status_code, 201, r.text)
        self.zona = r.json()["data"]["id_zona"]
        self.z = f"/{self.ag}/zonas"

    def operaciones(self):
        """(nombre, metodo, ruta, kwargs, es_escritura)."""
        return [
            ("listar", "GET", self.z, {}, False),
            ("listar/", "GET", self.z + "/", {}, False),
            ("detalle", "GET", f"{self.z}/{self.zona}", {}, False),
            ("crear", "POST", self.z, {"json": {"id_ciudad": 3}}, True),
            ("actualizar", "PUT", f"{self.z}/{self.zona}", {"json": {"nombre_zona": "Miraflores"}}, True),
            ("eliminar", "DELETE", f"{self.z}/{self.zona}", {}, True),
        ]

    def test_asu_y_gs_acceden_a_todo(self):
        for rol in ("ASU", "GS"):
            ag = self.crear("GS", razon_social=f"Ag {rol}", nit="5550001112" if rol == "ASU" else "5550001113")["id_agencia"]
            zona = self.req("POST", f"/{ag}/zonas", rol, json={"id_ciudad": 2, "nombre_zona": "Centro"}).json()["data"]["id_zona"]
            for nombre, metodo, ruta, kw in [
                ("listar", "GET", f"/{ag}/zonas", {}),
                ("detalle", "GET", f"/{ag}/zonas/{zona}", {}),
                ("crear", "POST", f"/{ag}/zonas", {"json": {"id_ciudad": 3}}),
                ("actualizar", "PUT", f"/{ag}/zonas/{zona}", {"json": {"nombre_zona": "Norte"}}),
                ("eliminar", "DELETE", f"/{ag}/zonas/{zona}", {}),
            ]:
                with self.subTest(rol=rol, op=nombre):
                    r = self.req(metodo, ruta, rol, **kw)
                    self.assertEqual(r.status_code, 201 if nombre == "crear" else 200, (rol, nombre, r.text))
                    self.assertEqual(r.json()["status"], "success")

    def test_d_solo_lectura(self):
        for nombre, metodo, ruta, kw, escritura in self.operaciones():
            with self.subTest(op=nombre):
                r = self.req(metodo, ruta, "D", **kw)
                self.assertEqual(r.status_code, 403 if escritura else 200, (nombre, r.text))

    def test_d_rechazado_no_modifica_nada(self):
        antes = self.req("GET", self.z, "GS").json()
        self.req("POST", self.z, "D", json={"id_ciudad": 3})
        self.req("PUT", f"{self.z}/{self.zona}", "D", json={"nombre_zona": "Hackeada"})
        self.req("DELETE", f"{self.z}/{self.zona}", "D")
        self.assertEqual(antes, self.req("GET", self.z, "GS").json())

    def test_d_no_ve_zonas_de_agencia_deshabilitada(self):
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": False})
        self.assertEqual(self.req("GET", self.z, "D").status_code, 404)
        self.assertEqual(self.req("GET", f"{self.z}/{self.zona}", "D").status_code, 404)
        self.assertEqual(self.req("GET", self.z, "GS").status_code, 200)  # GS si

    def test_v_y_c_reciben_403_en_todo(self):
        for rol in ("V", "C"):
            for nombre, metodo, ruta, kw, _ in self.operaciones():
                with self.subTest(rol=rol, op=nombre):
                    self.assertEqual(self.req(metodo, ruta, rol, **kw).status_code, 403)

    def test_sin_token_401(self):
        for nombre, metodo, ruta, kw, _ in self.operaciones():
            with self.subTest(op=nombre):
                r = self.req(metodo, ruta, headers={}, **kw)
                self.assertEqual(r.status_code, 401, (nombre, r.text))

    def test_tokens_invalidos_401(self):
        uid = str(self.crear_usuario("GS"))
        futuro = datetime.now(timezone.utc) + timedelta(hours=1)
        pasado = datetime.now(timezone.utc) - timedelta(hours=1)
        casos = {
            "basura": "no-es-un-jwt",
            "firma_ajena": jwt.encode({"sub": uid, "exp": futuro}, "otra-clave-secreta-de-32-bytes-min!!", algorithm=ALGORITHM),
            "expirado": jwt.encode({"sub": uid, "exp": pasado}, SECRET_KEY, algorithm=ALGORITHM),
            "usuario_inexistente": crear_token_acceso({"sub": str(uuid.uuid4()), "rol": "GS"}),
        }
        for tipo, token in casos.items():
            for op, metodo, ruta, kw, _ in self.operaciones():
                with self.subTest(token=tipo, op=op):
                    r = self.req(metodo, ruta, headers={"Authorization": f"Bearer {token}"}, **kw)
                    self.assertEqual(r.status_code, 401, (tipo, op, r.text))

    def test_usuario_inactivo_403_en_lectura_y_escritura(self):
        uid = self.crear_usuario("GS", activo=False)
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'GS'})}"}
        self.assertEqual(self.req("GET", self.z, headers=h).status_code, 403)
        self.assertEqual(self.req("POST", self.z, headers=h, json={"id_ciudad": 3}).status_code, 403)

    def test_rol_real_de_la_bd_no_el_claim_del_token(self):
        """Tokens que DECLARAN rol GS pero cuyo usuario real es V/C/D no obtienen
        privilegios; y un token que declara V para un GS real si funciona."""
        for real in ("V", "C"):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'GS'})}"}
            self.assertEqual(self.req("GET", self.z, headers=h).status_code, 403, real)
            self.assertEqual(self.req("POST", self.z, headers=h, json={"id_ciudad": 3}).status_code, 403, real)
        uid_d = self.crear_usuario("D")
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid_d), 'rol': 'ASU'})}"}
        self.assertEqual(self.req("DELETE", f"{self.z}/{self.zona}", headers=h).status_code, 403)
        self.assertEqual(self.req("GET", f"{self.z}/{self.zona}", "GS").status_code, 200)  # la zona sigue
        uid_gs = self.crear_usuario("GS")
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid_gs), 'rol': 'V'})}"}
        self.assertEqual(self.req("GET", self.z, headers=h).status_code, 200)  # el claim no le quita acceso

    def test_orden_401_403_antes_que_422_y_404(self):
        malo = {"json": {"id_ciudad": 0}}
        self.assertEqual(self.req("POST", self.z, headers={}, **malo).status_code, 401)
        self.assertEqual(self.req("POST", self.z, "V", **malo).status_code, 403)
        self.assertEqual(self.req("POST", self.z, "D", **malo).status_code, 403)
        self.assertEqual(self.req("POST", self.z, "GS", **malo).status_code, 422)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1", "V").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1", "D").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1", "GS").status_code, 404)


if __name__ == "__main__":
    unittest.main()
