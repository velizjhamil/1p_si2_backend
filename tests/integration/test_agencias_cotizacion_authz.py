# Integracion CU19 fase 8 - AUTORIZACION de GET /api/v1/agencias-reparto/{id}/cotizacion
# con JWT REALES sobre la BD LOCAL de pruebas (jamas Supabase ni Render). No se
# simula el usuario: se firman tokens con la SECRET_KEY de la app y se recorre
# get_current_user -> require_roles -> service (que consulta rol y estado en BD).
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_cotizacion_authz.py
#
#   ASU, GS, D            200 (misma respuesta, sin NIT ni facturacion)
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
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal")
TARIFA = {"criterio": "PESO", "rango_min": "0", "rango_max": "5", "costo": "10", "vigente_desde": "2020-01-01"}


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class CotizacionAuthz(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        z = self.req("POST", f"/{self.ag}/zonas", "GS", json={"id_ciudad": 2}).json()["data"]["id_zona"]
        r = self.req("POST", f"/{self.ag}/zonas/{z}/tarifas", "GS", json=TARIFA)
        self.assertEqual(r.status_code, 201, r.text)

    def cot(self, rol=None, headers=None, agencia=None, **params):
        p = {"ciudad": "La Paz", "peso_kg": "2", "volumen_m3": "1", **params}
        return self.req("GET", f"/{agencia or self.ag}/cotizacion", rol, headers=headers, params=p)

    def test_asu_gs_y_d_cotizan_con_la_misma_respuesta(self):
        cuerpos = {}
        for rol in ("ASU", "GS", "D"):
            r = self.cot(rol)
            self.assertEqual(r.status_code, 200, (rol, r.text))
            self.assertEqual(r.json()["status"], "success")
            self.assertEqual(r.json()["data"]["costo_agencia"], 10.0)
            cuerpos[rol] = r.json()
        self.assertEqual(cuerpos["ASU"], cuerpos["GS"])
        self.assertEqual(cuerpos["GS"], cuerpos["D"])

    def test_nadie_recibe_datos_de_facturacion(self):
        detalle = self.req("GET", f"/{self.ag}", "GS").json()["data"]
        secretos = [detalle["nit"], detalle["correo_facturacion"], detalle["direccion_fiscal"]]
        for rol in ("ASU", "GS", "D"):
            r = self.cot(rol)
            for campo in PRIVADOS:
                self.assertNotIn(f'"{campo}"', r.text, (rol, campo))
            for valor in secretos:
                self.assertNotIn(valor, r.text, (rol, valor))

    def test_v_y_c_reciben_403(self):
        for rol in ("V", "C"):
            r = self.cot(rol)
            self.assertEqual(r.status_code, 403, (rol, r.text))
            self.assertNotIn("data", r.json())

    def test_sin_token_401(self):
        r = self.cot(headers={})
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
                self.assertEqual(self.cot(headers={"Authorization": f"Bearer {token}"}).status_code, 401, tipo)
        self.assertEqual(self.cot(headers={"Authorization": "Basic abc"}).status_code, 401)

    def test_usuario_inactivo_403(self):
        uid = self.crear_usuario("GS", activo=False)
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'GS'})}"}
        self.assertEqual(self.cot(headers=h).status_code, 403)

    def test_rol_real_de_la_bd_no_el_claim_del_token(self):
        for real, declarado in (("V", "GS"), ("C", "ASU"), ("C", "D")):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': declarado})}"}
            with self.subTest(real=real, declarado=declarado):
                self.assertEqual(self.cot(headers=h).status_code, 403)
        for real in ("GS", "D", "ASU"):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'V'})}"}
            with self.subTest(real=real, declarado="V"):
                r = self.cot(headers=h)
                self.assertEqual(r.status_code, 200, r.text)
                for campo in PRIVADOS:
                    self.assertNotIn(campo, r.text)

    def test_401_y_403_antes_que_404_409_y_422(self):
        for kw in ({"agencia": 999999}, {"ciudad": "Atlantida"}, {"peso_kg": "0"}, {"peso_kg": "NaN"}, {"ciudad": "   "}):
            self.assertEqual(self.cot(headers={}, **kw).status_code, 401, kw)
            for rol in ("V", "C"):
                self.assertEqual(self.cot(rol, **kw).status_code, 403, (rol, kw))
        self.assertEqual(self.req("GET", f"/{self.ag}/cotizacion", "V").status_code, 403)     # sin parametros
        self.assertEqual(self.req("GET", f"/{self.ag}/cotizacion", headers={}).status_code, 401)
        self.assertEqual(self.cot("GS", agencia=999999).status_code, 404)
        self.assertEqual(self.cot("GS", peso_kg="0").status_code, 422)

    def test_es_solo_get(self):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = self.req(metodo, f"/{self.ag}/cotizacion", "GS", json={})
            self.assertIn(r.status_code, (404, 405), (metodo, r.text))


if __name__ == "__main__":
    unittest.main()
