# Pruebas unitarias AISLADAS (sin base de datos) del RBAC del CU6 (Gestionar productos).
# Regla: la GESTION (POST/PUT/DELETE) es solo de ASU; la CONSULTA (GET) no cambia.
# Sin token -> 401 en gestion; cualquier otro rol -> 403. ASU supera la autorizacion.
# Ejecutar desde la raiz del backend:  python tests/unit/test_cu6_rbac.py
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra mappers y rutas)
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402

PROD = "/api/v1/productos"
BODY = {
    "nombre": "Camisa Test", "id_categoria": 1, "precio_venta": 10, "stock_total": 1,
    "tallas": [1], "colores": [1],
}

# Operaciones de GESTION existentes del CU6
GESTION = [
    ("crear", "POST", PROD, BODY),
    ("crear (barra)", "POST", PROD + "/", BODY),
    ("editar", "PUT", f"{PROD}/1", {"nombre": "Otra"}),
    ("eliminar", "DELETE", f"{PROD}/1", None),
]


def usuario(rol: str | None) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test", estado=True,
        rol=Rol(nombre_rol=rol) if rol else None,
    )


class CU6SoloASUGestiona(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[get_db] = lambda: mock.MagicMock()
        self.addCleanup(app.dependency_overrides.clear)

    def como(self, rol):
        app.dependency_overrides[get_current_user] = lambda: usuario(rol)

    def llamar(self, metodo, ruta, body):
        return self.client.request(metodo, ruta, json=body) if body else self.client.request(metodo, ruta)

    def test_sin_token_401_en_gestion(self):
        for nombre, metodo, ruta, body in GESTION:
            with self.subTest(op=nombre):
                self.assertEqual(self.llamar(metodo, ruta, body).status_code, 401)

    def test_cliente_y_otros_roles_403_en_gestion(self):
        for rol in ("C", "GS", "V", "D", "ADMIN", None):
            self.como(rol)
            for nombre, metodo, ruta, body in GESTION:
                with self.subTest(rol=rol, op=nombre):
                    r = self.llamar(metodo, ruta, body)
                    self.assertEqual(r.status_code, 403, (rol, nombre, r.text))

    def test_403_antes_de_validar_el_body(self):
        self.como("C")
        self.assertEqual(self.client.post(PROD, json={}).status_code, 403)

    def test_asu_supera_la_autorizacion_en_gestion(self):
        self.como("ASU")
        for nombre, metodo, ruta, body in GESTION:
            with self.subTest(op=nombre):
                r = self.llamar(metodo, ruta, body)
                self.assertNotIn(r.status_code, (401, 403), (nombre, r.text))

    def test_consulta_no_exige_rol_ni_token(self):
        # Comportamiento existente de GET: se mantiene (Cliente, ASU y anonimo no reciben 401/403).
        for rol in (None, "C", "ASU"):
            if rol:
                self.como(rol)
            with self.subTest(rol=rol):
                r = self.client.get(PROD)
                self.assertNotIn(r.status_code, (401, 403), r.text)


if __name__ == "__main__":
    unittest.main()
