# Integracion (usa la BD configurada, SOLO LECTURA): comprueba que
# GET /api/v1/reportes/rendimiento-vendedores conserva su contrato tras
# corregir _rango_default(): mismo envelope, mismos campos, mismos permisos.
# Usa la BD LOCAL de pruebas (TEST_DATABASE_URL; ver tests/support/local_db.py).
# Se omite si la BD no responde. Ejecutar desde la raiz del backend:
#   python tests/integration/test_reportes_rendimiento_vendedores.py
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # BD local de pruebas (o se omite); ANTES de importar `app`

import app.main  # noqa: F401,E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.reporte import (  # noqa: E402
    RendimientoVendedoresResponse,
    VendedorRendimientoItem,
)

URL = "/api/v1/reportes/rendimiento-vendedores"


def _usuario(rol: str):
    return SimpleNamespace(rol=SimpleNamespace(nombre_rol=rol))


def _db_disponible() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@unittest.skipUnless(_db_disponible(), "BD no disponible")
class RendimientoVendedoresContratoTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def _get(self, rol: str, **params):
        app.dependency_overrides[get_current_user] = lambda: _usuario(rol)
        return TestClient(app).get(URL, params=params)

    def test_esquema_y_rango_por_defecto(self):
        r = self._get("GS")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"status", "data", "message"})
        data = body["data"]
        self.assertEqual(set(data), set(RendimientoVendedoresResponse.model_fields))
        desde = date.fromisoformat(data["fecha_desde"])
        hasta = date.fromisoformat(data["fecha_hasta"])
        self.assertEqual((hasta - desde).days, 29)
        self.assertEqual(hasta, datetime.now(timezone.utc).date())  # UTC, no la zona local
        RendimientoVendedoresResponse.model_validate(data)
        for item in data["items"]:
            self.assertEqual(set(item), set(VendedorRendimientoItem.model_fields))

    def test_rango_explicito_y_cruce_de_anio(self):
        r = self._get("ASU", fecha_desde="2025-12-17", fecha_hasta="2026-01-15")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["fecha_desde"], "2025-12-17")

    def test_rango_invertido_sigue_dando_422(self):
        hoy = date.today()
        r = self._get("GS", fecha_desde=str(hoy), fecha_hasta=str(hoy - timedelta(days=1)))
        self.assertEqual(r.status_code, 422)

    def test_permisos_sin_cambios(self):
        self.assertEqual(self._get("V").status_code, 403)
        self.assertEqual(self._get("C").status_code, 403)
        self.assertEqual(self._get("ASU").status_code, 200)


if __name__ == "__main__":
    unittest.main()
