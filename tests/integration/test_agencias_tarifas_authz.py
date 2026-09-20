# Integracion CU19 fase 6 - AUTORIZACION de
# /api/v1/agencias-reparto/{id}/zonas/{id_zona}/tarifas con JWT REALES sobre la
# BD LOCAL de pruebas (jamas Supabase ni Render). No se simula el usuario: se
# firman tokens con la SECRET_KEY de la app y se recorre get_current_user ->
# require_roles -> service (que consulta rol y estado en BD).
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_tarifas_authz.py
#
#                   GET (lista/detalle)                       POST/PUT/DELETE
#   ASU, GS               200                                     200/201
#   D                     200 (solo agencias habilitadas; 404 si no)   403
#   V, C                  403                                     403
#   sin token / invalido  401                                     401
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

TARIFA = {"criterio": "PESO", "rango_min": "0", "rango_max": "5", "costo": "10", "vigente_desde": "2026-01-01"}


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class TarifasAuthz(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        self.zona = self.req("POST", f"/{self.ag}/zonas", "GS", json={"id_ciudad": 2}).json()["data"]["id_zona"]
        self.t = f"/{self.ag}/zonas/{self.zona}/tarifas"
        r = self.req("POST", self.t, "GS", json=TARIFA)
        self.assertEqual(r.status_code, 201, r.text)
        self.tarifa = r.json()["data"]["id_tarifa"]

    def operaciones(self):
        """(nombre, metodo, ruta, kwargs, es_escritura)."""
        nueva = dict(TARIFA, rango_min="5", rango_max="10")
        return [
            ("listar", "GET", self.t, {}, False),
            ("listar/", "GET", self.t + "/", {}, False),
            ("detalle", "GET", f"{self.t}/{self.tarifa}", {}, False),
            ("crear", "POST", self.t, {"json": nueva}, True),
            ("actualizar", "PUT", f"{self.t}/{self.tarifa}", {"json": {"costo": "12"}}, True),
            ("eliminar", "DELETE", f"{self.t}/{self.tarifa}", {}, True),
        ]

    def test_asu_y_gs_acceden_a_todo(self):
        for rol in ("ASU", "GS"):
            ag = self.crear("GS", razon_social=f"Ag {rol}", nit="5550001112" if rol == "ASU" else "5550001113")["id_agencia"]
            z = self.req("POST", f"/{ag}/zonas", rol, json={"id_ciudad": 2}).json()["data"]["id_zona"]
            base = f"/{ag}/zonas/{z}/tarifas"
            creada = self.req("POST", base, rol, json=TARIFA)
            self.assertEqual(creada.status_code, 201, (rol, creada.text))
            t = creada.json()["data"]["id_tarifa"]
            for nombre, metodo, ruta, kw in [
                ("listar", "GET", base, {}),
                ("detalle", "GET", f"{base}/{t}", {}),
                ("actualizar", "PUT", f"{base}/{t}", {"json": {"costo": "12"}}),
                ("eliminar", "DELETE", f"{base}/{t}", {}),
            ]:
                with self.subTest(rol=rol, op=nombre):
                    r = self.req(metodo, ruta, rol, **kw)
                    self.assertEqual(r.status_code, 200, (rol, nombre, r.text))
                    self.assertEqual(r.json()["status"], "success")

    def test_d_solo_lectura(self):
        for nombre, metodo, ruta, kw, escritura in self.operaciones():
            with self.subTest(op=nombre):
                r = self.req(metodo, ruta, "D", **kw)
                self.assertEqual(r.status_code, 403 if escritura else 200, (nombre, r.text))

    def test_d_rechazado_no_modifica_nada(self):
        antes = self.req("GET", self.t, "GS").json()
        self.req("POST", self.t, "D", json=dict(TARIFA, rango_min="5", rango_max="9"))
        self.req("PUT", f"{self.t}/{self.tarifa}", "D", json={"costo": "999"})
        self.req("DELETE", f"{self.t}/{self.tarifa}", "D")
        self.assertEqual(antes, self.req("GET", self.t, "GS").json())

    def test_d_no_ve_tarifas_de_agencia_deshabilitada_pero_gs_si(self):
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": False})
        self.assertEqual(self.req("GET", self.t, "D").status_code, 404)
        self.assertEqual(self.req("GET", f"{self.t}/{self.tarifa}", "D").status_code, 404)
        self.assertEqual(self.req("GET", self.t, "GS").status_code, 200)         # historico visible para ASU/GS
        self.assertEqual(self.req("GET", f"{self.t}/{self.tarifa}", "ASU").status_code, 200)

    def test_v_y_c_reciben_403_en_todo(self):
        for rol in ("V", "C"):
            for nombre, metodo, ruta, kw, _ in self.operaciones():
                with self.subTest(rol=rol, op=nombre):
                    self.assertEqual(self.req(metodo, ruta, rol, **kw).status_code, 403)

    def test_sin_token_401(self):
        for nombre, metodo, ruta, kw, _ in self.operaciones():
            with self.subTest(op=nombre):
                self.assertEqual(self.req(metodo, ruta, headers={}, **kw).status_code, 401)

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
        self.assertEqual(self.req("GET", self.t, headers=h).status_code, 403)
        self.assertEqual(self.req("POST", self.t, headers=h, json=dict(TARIFA, rango_min="5", rango_max="9")).status_code, 403)
        self.assertEqual(self.req("DELETE", f"{self.t}/{self.tarifa}", headers=h).status_code, 403)

    def test_rol_real_de_la_bd_no_el_claim_del_token(self):
        """Tokens que DECLARAN rol GS/ASU pero cuyo usuario real es V/C/D no
        obtienen privilegios; y declarar V siendo GS real no quita acceso."""
        nueva = dict(TARIFA, rango_min="5", rango_max="9")
        for real, declarado in (("V", "GS"), ("C", "GS"), ("D", "ASU")):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': declarado})}"}
            with self.subTest(real=real, declarado=declarado):
                self.assertEqual(self.req("POST", self.t, headers=h, json=nueva).status_code, 403)
                self.assertEqual(self.req("PUT", f"{self.t}/{self.tarifa}", headers=h, json={"costo": "1"}).status_code, 403)
                self.assertEqual(self.req("DELETE", f"{self.t}/{self.tarifa}", headers=h).status_code, 403)
        self.assertEqual(self.req("GET", f"{self.t}/{self.tarifa}", "GS").json()["data"]["costo"], 10.0)  # intacta
        uid_gs = self.crear_usuario("GS")
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid_gs), 'rol': 'V'})}"}
        self.assertEqual(self.req("GET", self.t, headers=h).status_code, 200)
        self.assertEqual(self.req("POST", self.t, headers=h, json=nueva).status_code, 201)

    def test_orden_401_403_antes_que_422_y_404(self):
        malo = {"json": {"criterio": "PESADO"}}
        self.assertEqual(self.req("POST", self.t, headers={}, **malo).status_code, 401)
        self.assertEqual(self.req("POST", self.t, "V", **malo).status_code, 403)
        self.assertEqual(self.req("POST", self.t, "D", **malo).status_code, 403)
        self.assertEqual(self.req("POST", self.t, "GS", **malo).status_code, 422)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1/tarifas/1", "V").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1/tarifas/1", "D").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999/zonas/1/tarifas/1", "GS").status_code, 404)


if __name__ == "__main__":
    unittest.main()
