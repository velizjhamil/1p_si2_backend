# Pruebas unitarias AISLADAS del RBAC de compra/venta (sin base de datos).
#
# Regla de negocio: SOLO el rol Cliente (C) ejecuta el checkout online.
#   POST /api/v1/ventas/checkout -> únicamente C
#   POST /api/v1/ventas/pos      -> únicamente V / GS / ASU (venta de mostrador, CU11)
# La sesión y el usuario se simulan con dependency_overrides y la lógica de venta
# (_procesar_venta) se reemplaza por un espía: se comprueba QUIÉN llega a ella.
# Ejecutar desde la raíz del backend:
#   python tests/unit/test_ventas_rbac.py
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra mappers y rutas)
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user, get_db, require_roles  # noqa: E402
from app.api.v1.endpoints import ventas as ventas_ep  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402

CHECKOUT = "/api/v1/ventas/checkout"
POS = "/api/v1/ventas/pos"
CLIENTE_ID = str(uuid.uuid4())

PAYLOAD = {
    "items": [{"producto_id": 1, "cantidad": 1}],
    "metodo_pago": "EFECTIVO",
    "datos_entrega": {
        "nombre_cliente": "Cliente Demo", "correo": "c@x.com", "telefono": "70000000",
        "direccion": "Av. 1 esquina 2", "ciudad": "Santa Cruz",
    },
}


def usuario(rol: str | None) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test", estado=True,
        rol=Rol(nombre_rol=rol) if rol else None,
    )


class BaseRBAC(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[get_db] = lambda: mock.Mock()
        # espía: reemplaza la lógica de venta; devuelve un marcador y registra con qué modo se llamó
        patcher = mock.patch.object(ventas_ep, "_procesar_venta", return_value={"llego": True})
        self.espia = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(app.dependency_overrides.clear)

    def como(self, rol: str | None):
        app.dependency_overrides[get_current_user] = lambda: usuario(rol)


class CheckoutSoloCliente(BaseRBAC):
    def test_cliente_llega_al_checkout_en_modo_online(self):
        self.como("C")
        r = self.client.post(CHECKOUT, json=PAYLOAD)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.espia.call_args.args[3], "ONLINE")

    def test_todos_los_demas_roles_reciben_403_y_nunca_llegan_a_la_venta(self):
        for rol in ("ASU", "GS", "V", "D"):
            with self.subTest(rol=rol):
                self.como(rol)
                r = self.client.post(CHECKOUT, json=PAYLOAD)
                self.assertEqual(r.status_code, 403)
                self.assertIn("Solo el rol Cliente", r.json()["detail"])
                self.assertIn("/api/v1/ventas/pos", r.json()["detail"])   # orienta al flujo correcto
        self.espia.assert_not_called()

    def test_sin_token_401(self):
        r = self.client.post(CHECKOUT, json=PAYLOAD)          # sin override: get_current_user real
        self.assertEqual(r.status_code, 401)
        self.espia.assert_not_called()

    def test_usuario_sin_rol_403(self):
        self.como(None)
        self.assertEqual(self.client.post(CHECKOUT, json=PAYLOAD).status_code, 403)

    def test_cliente_no_puede_forzar_modo_pos_en_checkout(self):
        self.como("C")
        r = self.client.post(CHECKOUT, json={**PAYLOAD, "tipo_venta": "POS", "id_cliente_override": CLIENTE_ID})
        self.assertEqual(r.status_code, 403)
        self.espia.assert_not_called()

    def test_vendedor_con_cliente_override_tampoco_compra_por_checkout(self):
        self.como("V")
        r = self.client.post(CHECKOUT, json={**PAYLOAD, "id_cliente_override": CLIENTE_ID})
        self.assertEqual(r.status_code, 403)
        self.espia.assert_not_called()


class PosSoloMostrador(BaseRBAC):
    def test_vendedor_gerente_y_admin_llegan_al_pos_en_modo_pos(self):
        for rol in ("V", "GS", "ASU"):
            with self.subTest(rol=rol):
                self.espia.reset_mock()
                self.como(rol)
                r = self.client.post(POS, json={**PAYLOAD, "tipo_venta": "POS", "id_cliente_override": CLIENTE_ID})
                self.assertEqual(r.status_code, 201)
                self.assertEqual(self.espia.call_args.args[3], "POS")

    def test_el_endpoint_pos_ignora_el_tipo_venta_del_payload(self):
        self.como("V")
        r = self.client.post(POS, json={**PAYLOAD, "tipo_venta": "ONLINE", "id_cliente_override": CLIENTE_ID})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.espia.call_args.args[3], "POS")   # el modo lo fija la RUTA, no el cliente HTTP

    def test_cliente_y_delivery_reciben_403_en_pos(self):
        for rol in ("C", "D"):
            with self.subTest(rol=rol):
                self.como(rol)
                r = self.client.post(POS, json={**PAYLOAD, "tipo_venta": "POS", "id_cliente_override": CLIENTE_ID})
                self.assertEqual(r.status_code, 403)
        self.espia.assert_not_called()

    def test_sin_token_401(self):
        self.assertEqual(self.client.post(POS, json=PAYLOAD).status_code, 401)


class RequireRolesUnitario(unittest.TestCase):
    def test_sin_excepcion_para_asu(self):
        dep = require_roles("C")
        with self.assertRaises(HTTPException) as ctx:
            dep(usuario("ASU"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_rol_permitido_devuelve_el_usuario(self):
        u = usuario("C")
        self.assertIs(require_roles("C", "V")(u), u)

    def test_mensaje_por_defecto_lista_los_roles(self):
        with self.assertRaises(HTTPException) as ctx:
            require_roles("GS", "V")(usuario("C"))
        self.assertIn("GS, V", ctx.exception.detail)


class RutasExpuestas(unittest.TestCase):
    def test_solo_checkout_y_pos_aceptan_post_de_venta(self):
        paths = app.openapi()["paths"]
        posts = sorted(p for p, ops in paths.items() if p.startswith("/api/v1/ventas") and "post" in ops)
        self.assertEqual(posts, ["/api/v1/ventas/checkout", "/api/v1/ventas/pos"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
