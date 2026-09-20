# Pruebas unitarias AISLADAS (sin base de datos) del RBAC del CU24: temporadas y colecciones.
# Regla: SOLO ASU gestiona el CU24. Sin token -> 401; cualquier otro rol -> 403.
# El usuario y la sesion se simulan con dependency_overrides; para ASU se comprueba que
# la peticion supera la autorizacion (llega al handler: ni 401 ni 403).
# Ejecutar desde la raiz del backend:  python tests/unit/test_cu24_rbac.py
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

COL = "/api/v1/colecciones"
TEMP = "/api/v1/temporadas"
FECHAS = {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-03-01"}

# (nombre, metodo, ruta, body) - todas las operaciones existentes del CU24
OPERACIONES = [
    ("colecciones listar", "GET", COL, None),
    ("colecciones crear", "POST", COL, {"nombre_coleccion": "Verano"}),
    ("colecciones editar", "PUT", f"{COL}/1", {"nombre_coleccion": "Invierno"}),
    ("colecciones eliminar", "DELETE", f"{COL}/1", None),
    ("temporadas listar", "GET", TEMP, None),
    ("temporadas crear", "POST", TEMP, {"nombre_temporada": "T1", **FECHAS}),
    ("temporadas editar", "PUT", f"{TEMP}/1", {"nombre_temporada": "T2"}),
    ("temporadas eliminar", "DELETE", f"{TEMP}/1", None),
]


def usuario(rol: str | None) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test", estado=True,
        rol=Rol(nombre_rol=rol) if rol else None,
    )


class CU24SoloASU(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[get_db] = lambda: mock.MagicMock()
        self.addCleanup(app.dependency_overrides.clear)

    def como(self, rol: str | None):
        app.dependency_overrides[get_current_user] = lambda: usuario(rol)

    def llamar(self, metodo, ruta, body):
        return self.client.request(metodo, ruta, json=body) if body else self.client.request(metodo, ruta)

    def test_sin_token_401_en_todas_las_operaciones(self):
        for nombre, metodo, ruta, body in OPERACIONES:
            with self.subTest(op=nombre):
                r = self.llamar(metodo, ruta, body)
                self.assertEqual(r.status_code, 401, (nombre, r.text))

    def test_otros_roles_403_en_todas_las_operaciones(self):
        for rol in ("GS", "V", "D", "C", "ADMIN", None):
            self.como(rol)
            for nombre, metodo, ruta, body in OPERACIONES:
                with self.subTest(rol=rol, op=nombre):
                    r = self.llamar(metodo, ruta, body)
                    self.assertEqual(r.status_code, 403, (rol, nombre, r.text))

    def test_403_antes_de_validar_el_body(self):
        self.como("GS")
        self.assertEqual(self.client.post(COL, json={}).status_code, 403)
        self.assertEqual(self.client.post(TEMP, json={}).status_code, 403)

    def test_asu_supera_la_autorizacion_en_todas_las_operaciones(self):
        self.como("ASU")
        for nombre, metodo, ruta, body in OPERACIONES:
            with self.subTest(op=nombre):
                r = self.llamar(metodo, ruta, body)
                self.assertNotIn(r.status_code, (401, 403), (nombre, r.text))


if __name__ == "__main__":
    unittest.main()
