# Integracion CU19 fase 4 - AUTORIZACION de /api/v1/agencias-reparto con JWT
# REALES sobre la BD LOCAL de pruebas (jamas Supabase). No se simula el usuario:
# se firman tokens con la SECRET_KEY de la app para usuarios de cada rol y se
# recorre get_current_user -> require_roles -> service.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_authz.py
#
# Matriz verificada:
#                   GET (lista/detalle)   POST/PUT/PATCH/DELETE
#   ASU, GS               200/201                 200/201
#   D                     200 (*)                 403
#   V, C                  403                     403
#   sin token / invalido  401                     401
#   (*) D solo habilitadas y sin datos de facturacion (ver test_agencias_endpoints).
#   401 y 403 se resuelven ANTES de validar el body.
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
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista, payload  # noqa: E402


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class AgenciasAuthz(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.id = self.crear("GS")["id_agencia"]  # agencia existente para las rutas /{id}

    def operaciones(self):
        """(nombre, metodo, ruta, kwargs, es_escritura). Cada una valida con un
        body correcto para ASU/GS."""
        i = self.id
        return [
            ("listar", "GET", "", {}, False),
            ("listar/", "GET", "/", {}, False),
            ("detalle", "GET", f"/{i}", {}, False),
            ("crear", "POST", "", {"json": payload(razon_social="Nueva", nit="7770001112")}, True),
            ("actualizar", "PUT", f"/{i}", {"json": {"telefono": "70000000"}}, True),
            ("estado", "PATCH", f"/{i}/estado", {"json": {"is_active": False}}, True),
            ("eliminar", "DELETE", f"/{i}", {}, True),
        ]

    # -- ASU / GS: acceso completo -----------------------------------------
    def test_asu_y_gs_acceden_a_todo(self):
        for rol in ("ASU", "GS"):
            for nombre, metodo, ruta, kw, escritura in self.operaciones():
                with self.subTest(rol=rol, op=nombre):
                    if nombre == "crear":
                        kw = {"json": payload(razon_social=f"Nueva {rol}", nit="7770001112" if rol == "GS" else "7770001113")}
                    if nombre == "eliminar":
                        id_tmp = self.crear(rol, razon_social=f"Temporal {rol}", nit=f"88800011{ '1' if rol == 'GS' else '2'}")["id_agencia"]
                        ruta = f"/{id_tmp}"
                    r = self.req(metodo, ruta, rol, **kw)
                    self.assertIn(r.status_code, (200, 201), (rol, nombre, r.text))
                    self.assertEqual(r.json()["status"], "success")
                    self.assertEqual(r.status_code, 201 if nombre == "crear" else 200)

    # -- D: solo lectura -----------------------------------------------------
    def test_d_lee_y_no_escribe(self):
        for nombre, metodo, ruta, kw, escritura in self.operaciones():
            with self.subTest(op=nombre):
                r = self.req(metodo, ruta, "D", **kw)
                self.assertEqual(r.status_code, 403 if escritura else 200, (nombre, r.text))
                if escritura:
                    self.assertIn("Gerente de Sucursal", r.json()["detail"])

    def test_d_no_modifica_nada_al_ser_rechazado(self):
        antes = self.req("GET", f"/{self.id}", "GS").json()["data"]
        self.req("PUT", f"/{self.id}", "D", json={"razon_social": "Hackeada"})
        self.req("PATCH", f"/{self.id}/estado", "D", json={"is_active": False})
        self.req("DELETE", f"/{self.id}", "D")
        despues = self.req("GET", f"/{self.id}", "GS").json()["data"]
        self.assertEqual(antes, despues)

    # -- V / C: sin acceso ----------------------------------------------------
    def test_v_y_c_reciben_403_en_todo(self):
        for rol in ("V", "C"):
            for nombre, metodo, ruta, kw, _ in self.operaciones():
                with self.subTest(rol=rol, op=nombre):
                    r = self.req(metodo, ruta, rol, **kw)
                    self.assertEqual(r.status_code, 403, (rol, nombre, r.text))
                    self.assertIn("detail", r.json())

    # -- 401: sin autenticacion valida -----------------------------------------
    def test_sin_token_401_en_todo(self):
        for nombre, metodo, ruta, kw, _ in self.operaciones():
            with self.subTest(op=nombre):
                r = self.req(metodo, ruta, headers={}, **kw)
                self.assertEqual(r.status_code, 401, (nombre, r.text))
                self.assertEqual(r.headers.get("www-authenticate"), "Bearer")

    def test_tokens_invalidos_401(self):
        uid = str(self.crear_usuario("GS"))
        futuro = datetime.now(timezone.utc) + timedelta(hours=1)
        pasado = datetime.now(timezone.utc) - timedelta(hours=1)
        casos = {
            "basura": "esto-no-es-un-jwt",
            "firma_ajena": jwt.encode({"sub": uid, "exp": futuro}, "otra-clave-secreta-de-32-bytes-min!!", algorithm=ALGORITHM),
            "expirado": jwt.encode({"sub": uid, "exp": pasado}, SECRET_KEY, algorithm=ALGORITHM),
            "usuario_inexistente": crear_token_acceso({"sub": str(uuid.uuid4()), "rol": "GS"}),
        }
        for nombre, token in casos.items():
            for op, metodo, ruta, kw, _ in self.operaciones():
                with self.subTest(token=nombre, op=op):
                    r = self.req(metodo, ruta, headers={"Authorization": f"Bearer {token}"}, **kw)
                    self.assertEqual(r.status_code, 401, (nombre, op, r.text))

    def test_esquema_de_autorizacion_incorrecto_401(self):
        r = self.req("GET", "", headers={"Authorization": "Basic abc"})
        self.assertEqual(r.status_code, 401)

    def test_usuario_inactivo_403(self):
        uid = self.crear_usuario("GS", activo=False)
        token = crear_token_acceso({"sub": str(uid), "rol": "GS"})
        r = self.req("GET", "", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 403)

    def test_el_rol_sale_de_la_bd_no_del_claim_del_token(self):
        """Un token de V que declara rol=GS sigue siendo V (403)."""
        uid = self.crear_usuario("V")
        token = crear_token_acceso({"sub": str(uid), "rol": "GS"})
        r = self.req("POST", "", headers={"Authorization": f"Bearer {token}"}, json=payload())
        self.assertEqual(r.status_code, 403)

    # -- orden: la autorizacion va antes que la validacion ----------------------
    def test_401_y_403_antes_que_422(self):
        malo = {"json": {"nit": 123}}  # body invalido
        self.assertEqual(self.req("POST", "", headers={}, **malo).status_code, 401)
        self.assertEqual(self.req("POST", "", "V", **malo).status_code, 403)
        self.assertEqual(self.req("POST", "", "D", **malo).status_code, 403)
        self.assertEqual(self.req("PUT", f"/{self.id}", "C", json={"razon_social": "  "}).status_code, 403)
        self.assertEqual(self.req("POST", "", "GS", **malo).status_code, 422)

    def test_403_antes_que_404(self):
        self.assertEqual(self.req("DELETE", "/999999", "V").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999", "D").status_code, 403)
        self.assertEqual(self.req("DELETE", "/999999", "GS").status_code, 404)


if __name__ == "__main__":
    unittest.main()
