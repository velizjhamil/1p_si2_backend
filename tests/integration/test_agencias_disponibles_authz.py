# Integracion CU19 fase 7 - AUTORIZACION de GET /api/v1/agencias-reparto/disponibles
# con JWT REALES sobre la BD LOCAL de pruebas (jamas Supabase ni Render). No se
# simula el usuario: se firman tokens con la SECRET_KEY de la app y se recorre
# get_current_user -> require_roles -> service (que consulta rol y estado en BD).
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_disponibles_authz.py
#
#   ASU, GS, D            200 (misma respuesta: sin NIT ni facturacion)
#   V, C                  403
#   sin token / invalido  401 | usuario inactivo 403
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
from tests.support.agencias_api import BASE, BaseAgenciasAPI, bd_lista  # noqa: E402

PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal")


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class DisponiblesAuthz(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        self.assertEqual(self.req("POST", f"/{self.ag}/zonas", "GS", json={"id_ciudad": 2}).status_code, 201)

    def disp(self, rol=None, headers=None, ciudad="La Paz"):
        return self.req("GET", "/disponibles", rol, headers=headers, params={"ciudad": ciudad})

    def test_asu_gs_y_d_consultan_con_la_misma_respuesta(self):
        cuerpos = {}
        for rol in ("ASU", "GS", "D"):
            r = self.disp(rol)
            self.assertEqual(r.status_code, 200, (rol, r.text))
            self.assertEqual(r.json()["status"], "success")
            self.assertEqual([a["id_agencia"] for a in r.json()["data"]], [self.ag], rol)
            cuerpos[rol] = r.json()
        self.assertEqual(cuerpos["ASU"], cuerpos["GS"])
        self.assertEqual(cuerpos["GS"], cuerpos["D"])          # D: misma privacidad que el resto

    def test_nadie_recibe_datos_de_facturacion(self):
        for rol in ("ASU", "GS", "D"):
            r = self.disp(rol)
            for campo in PRIVADOS:
                self.assertNotIn(campo, r.text, rol)
            self.assertNotIn("1020304050", r.text, rol)          # ni el valor del NIT

    def test_v_y_c_reciben_403(self):
        for rol in ("V", "C"):
            r = self.disp(rol)
            self.assertEqual(r.status_code, 403, (rol, r.text))
            self.assertNotIn("data", r.json())

    def test_sin_token_401(self):
        r = self.disp(headers={})
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.headers.get("www-authenticate"), "Bearer")

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
            with self.subTest(token=tipo):
                r = self.disp(headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(r.status_code, 401, (tipo, r.text))
        self.assertEqual(self.disp(headers={"Authorization": "Basic abc"}).status_code, 401)

    def test_usuario_inactivo_403(self):
        uid = self.crear_usuario("GS", activo=False)
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'GS'})}"}
        self.assertEqual(self.disp(headers=h).status_code, 403)

    def test_rol_real_de_la_bd_no_el_claim_del_token(self):
        """Tokens que DECLARAN un rol con acceso pero cuyo usuario real es V/C no
        pasan; y declarar V siendo GS/D reales no quita el acceso."""
        for real, declarado in (("V", "GS"), ("C", "ASU"), ("C", "D")):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': declarado})}"}
            with self.subTest(real=real, declarado=declarado):
                self.assertEqual(self.disp(headers=h).status_code, 403)
        for real in ("GS", "D", "ASU"):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'V'})}"}
            with self.subTest(real=real, declarado="V"):
                r = self.disp(headers=h)
                self.assertEqual(r.status_code, 200, r.text)
                for campo in PRIVADOS:
                    self.assertNotIn(campo, r.text)

    def test_401_y_403_antes_que_404_409_y_422(self):
        self.assertEqual(self.disp(headers={}, ciudad="Atlantida").status_code, 401)
        self.assertEqual(self.req("GET", "/disponibles", headers={}).status_code, 401)          # sin parametro
        for rol in ("V", "C"):
            self.assertEqual(self.disp(rol, ciudad="Atlantida").status_code, 403, rol)          # ciudad inexistente
            self.assertEqual(self.req("GET", "/disponibles", rol).status_code, 403, rol)        # sin parametro
            self.assertEqual(self.disp(rol, ciudad="   ").status_code, 403, rol)                # ciudad vacia
        self.assertEqual(self.disp("GS", ciudad="Atlantida").status_code, 404)

    def test_solo_get_y_no_hay_escritura(self):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = self.req(metodo, "/disponibles", "GS", json={})
            self.assertIn(r.status_code, (404, 405, 422), (metodo, r.text))
        self.assertNotEqual(self.req("GET", "/disponibles", "GS", params={"ciudad": "La Paz"}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
