# Pruebas unitarias del RBAC de Roles y Permisos (/api/v1/roles y /api/v1/permisos).
# Regla:
# - GET /api/v1/roles permitido para ASU, ADMIN y GS (para asignación de personal).
# - GET /api/v1/roles denegado (403) para V, D, C.
# - Mutaciones (POST, PUT, DELETE) y GET /permisos restringidos exclusivamente a ASU / ADMIN.
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402

ROLES_URL = "/api/v1/roles"
PERMISOS_URL = "/api/v1/permisos"
FAKE_ID = "00000000-0000-0000-0000-000000000001"

OPERACIONES_SOLO_ADMIN = [
    ("crear rol", "POST", ROLES_URL, {"nombre_rol": "TestRol", "descripcion": "Desc", "permiso_ids": []}),
    ("editar rol", "PUT", f"{ROLES_URL}/{FAKE_ID}", {"nombre_rol": "TestRolMod"}),
    ("eliminar rol", "DELETE", f"{ROLES_URL}/{FAKE_ID}", None),
    ("editar permisos rol", "PUT", f"{ROLES_URL}/{FAKE_ID}/permisos", {"permiso_ids": []}),
    ("listar permisos matriz", "GET", PERMISOS_URL, None),
]


def usuario(rol: str | None) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(),
        nombre="Test",
        apellido="User",
        correo=f"{rol}@test.com",
        estado=True,
        rol=Rol(nombre_rol=rol) if rol else None,
    )


class TestRolesRBAC(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        mock_db = mock.MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = []
        app.dependency_overrides[get_db] = lambda: mock_db
        self.addCleanup(app.dependency_overrides.clear)

    def como(self, rol: str | None):
        app.dependency_overrides[get_current_user] = lambda: usuario(rol)

    def llamar(self, metodo, ruta, body=None):
        return self.client.request(metodo, ruta, json=body) if body else self.client.request(metodo, ruta)

    def test_sin_token_retorna_401(self):
        r = self.llamar("GET", ROLES_URL)
        self.assertEqual(r.status_code, 401)
        for nombre, metodo, ruta, body in OPERACIONES_SOLO_ADMIN:
            with self.subTest(op=nombre):
                r = self.llamar(metodo, ruta, body)
                self.assertEqual(r.status_code, 401)

    def test_get_roles_autorizado_para_asu_admin_y_gs(self):
        for rol in ("ASU", "ADMIN", "GS"):
            self.como(rol)
            with self.subTest(rol=rol):
                r = self.llamar("GET", ROLES_URL)
                self.assertNotIn(r.status_code, (401, 403), f"{rol} debe poder listar roles")
                self.assertEqual(r.status_code, 200)

    def test_get_roles_denegado_para_v_d_c(self):
        for rol in ("V", "D", "C"):
            self.como(rol)
            with self.subTest(rol=rol):
                r = self.llamar("GET", ROLES_URL)
                self.assertEqual(r.status_code, 403, f"{rol} NO debe poder listar roles")

    def test_mutaciones_denegadas_para_gs_v_d_c(self):
        for rol in ("GS", "V", "D", "C"):
            self.como(rol)
            for nombre, metodo, ruta, body in OPERACIONES_SOLO_ADMIN:
                with self.subTest(rol=rol, op=nombre):
                    r = self.llamar(metodo, ruta, body)
                    self.assertEqual(r.status_code, 403, f"{rol} no debe ejecutar {nombre}")


if __name__ == "__main__":
    unittest.main()
