# Integracion CU20 - AUTORIZACION de reportes con JWT REALES (BD solo lectura).
#
# A diferencia de test_reportes_endpoints.py (que sobrescribe
# get_current_user), aqui NO se simula el usuario: se firman tokens con la
# SECRET_KEY de la app para un usuario real de cada rol y se recorre toda la
# cadena get_current_user -> require_roles. Se omite si la BD no responde o si
# no hay un usuario activo del rol. Ejecutar desde la raiz del backend:
#   python tests/integration/test_reportes_authz.py
#
# Contrato verificado:
#   sin token / token invalido / expirado / usuario inexistente -> 401
#   rol V, C, D                                                  -> 403
#   rol GS, ASU                                                  -> 200
#   GS/ASU con parametros invalidos                              -> 422
#   401 y 403 se resuelven ANTES de validar parametros y de ejecutar el service.
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import jwt  # noqa: E402

from tests.support import local_db  # noqa: E402

local_db.configure()  # BD local de pruebas (o se omite); ANTES de importar `app`

import app.main  # noqa: F401,E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import get_db  # noqa: E402
from app.api.v1.endpoints import reportes as reportes_ep  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import ALGORITHM, SECRET_KEY, crear_token_acceso  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.reportes import service  # noqa: E402

BASE = "/api/v1/reportes"
NUEVOS = ("ventas", "productos-mas-vendidos", "inventario", "devoluciones")
TODOS = NUEVOS + ("rendimiento-vendedores",)
CON_DATOS = {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-12-31"}
DETALLE_403 = "Su rol no tiene acceso al panel de reportes."

# Parametros invalidos por endpoint (todos fallan la validacion de FastAPI/service).
INVALIDOS = {
    "ventas": {"categoria_id": 0},
    "productos-mas-vendidos": {"top": 0},
    "inventario": {"limite": 0},
    "devoluciones": {"fecha_inicio": "ayer"},
    "rendimiento-vendedores": {"fecha_desde": "ayer"},
}


def _db_disponible() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@unittest.skipUnless(_db_disponible(), "BD no disponible")
class ReportesAuthzTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.execute(text("SET TRANSACTION READ ONLY"))
        app.dependency_overrides[get_db] = lambda: self.db  # solo la sesion; la auth es real
        self.client = TestClient(app)
        self.usuarios = {
            rol: uid
            for rol, uid in self.db.execute(
                text(
                    """select distinct on (r.nombre_rol) r.nombre_rol, u.id_usuario
                       from usuarios u join roles r on r.id_rol = u.id_rol
                       where u.estado order by r.nombre_rol, u.fecha_creacion"""
                )
            )
        }

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.rollback()
        self.db.close()

    # -- utilidades --------------------------------------------------------
    def headers(self, rol: str) -> dict:
        if rol not in self.usuarios:
            self.skipTest(f"no hay usuario activo con rol {rol}")
        token = crear_token_acceso({"sub": str(self.usuarios[rol]), "rol": rol})
        return {"Authorization": f"Bearer {token}"}

    def get(self, ruta, rol=None, headers=None, **params):
        h = headers if headers is not None else (self.headers(rol) if rol else {})
        return self.client.get(f"{BASE}/{ruta}", params=params, headers=h)

    # -- 200: roles autorizados --------------------------------------------
    def test_gs_y_asu_consultan_los_reportes_con_token_real(self):
        for rol in ("GS", "ASU"):
            for ep in TODOS:
                r = self.get(ep, rol, **(CON_DATOS if ep in NUEVOS else {}))
                self.assertEqual(r.status_code, 200, (rol, ep, r.text))
                self.assertEqual(r.json()["status"], "success")

    # -- 403: roles no autorizados -----------------------------------------
    def test_v_c_d_reciben_403_en_todos_los_reportes(self):
        for rol in ("V", "C", "D"):
            for ep in TODOS:
                r = self.get(ep, rol)
                self.assertEqual(r.status_code, 403, (rol, ep))
                self.assertEqual(r.json()["detail"], DETALLE_403, (rol, ep))

    def test_403_se_resuelve_antes_de_validar_parametros(self):
        # Con parametros invalidos un rol no autorizado debe ver 403, no 422.
        for rol in ("V", "C", "D"):
            for ep in TODOS:
                r = self.get(ep, rol, **INVALIDOS[ep])
                self.assertEqual(r.status_code, 403, (rol, ep, r.text))

    def test_403_no_ejecuta_la_logica_del_reporte(self):
        with mock.patch.object(service, "construir_filtros") as filtros, \
             mock.patch.object(service, "reporte_ventas") as ventas, \
             mock.patch.object(service, "reporte_productos_mas_vendidos") as top, \
             mock.patch.object(service, "reporte_inventario") as inv, \
             mock.patch.object(service, "reporte_devoluciones") as dev:
            for rol in ("V", "C", "D"):
                for ep in NUEVOS:
                    self.assertEqual(self.get(ep, rol, **CON_DATOS).status_code, 403)
            for m in (filtros, ventas, top, inv, dev):
                m.assert_not_called()

    # -- 401: sin autenticacion ---------------------------------------------
    def test_sin_token_401_con_www_authenticate(self):
        for ep in TODOS:
            r = self.get(ep)
            self.assertEqual(r.status_code, 401, ep)
            self.assertEqual(r.headers.get("www-authenticate"), "Bearer", ep)

    def test_401_se_resuelve_antes_de_validar_parametros(self):
        for ep in TODOS:
            self.assertEqual(self.get(ep, **INVALIDOS[ep]).status_code, 401, ep)

    def test_token_invalido_expirado_o_de_usuario_inexistente_401(self):
        ahora = datetime.now(timezone.utc)
        base = {"sub": str(self.usuarios.get("GS", uuid.uuid4())), "rol": "GS"}
        tokens = {
            "basura": "no-es-un-jwt",
            "firma_ajena": jwt.encode({**base, "exp": ahora + timedelta(hours=1)}, "otra-clave-" * 4, algorithm=ALGORITHM),
            "expirado": jwt.encode({**base, "exp": ahora - timedelta(minutes=1)}, SECRET_KEY, algorithm=ALGORITHM),
            "usuario_inexistente": crear_token_acceso({"sub": str(uuid.uuid4()), "rol": "GS"}),
        }
        for nombre, token in tokens.items():
            for ep in NUEVOS:
                r = self.get(ep, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(r.status_code, 401, (nombre, ep, r.text))

    def test_esquema_de_autorizacion_distinto_de_bearer_401(self):
        for ep in NUEVOS:
            r = self.get(ep, headers={"Authorization": "Basic Zm9vOmJhcg=="})
            self.assertEqual(r.status_code, 401, ep)

    # -- 422: autorizado con parametros invalidos ---------------------------
    def test_gs_y_asu_con_parametros_invalidos_422(self):
        for rol in ("GS", "ASU"):
            for ep in TODOS:
                r = self.get(ep, rol, **INVALIDOS[ep])
                self.assertEqual(r.status_code, 422, (rol, ep, r.text))

    def test_422_de_filtros_del_service_tambien_para_roles_autorizados(self):
        casos = [
            {"fecha_inicio": "2026-10-01", "fecha_fin": "2026-09-01"},  # rango invertido
            {"categoria_id": 99999999},  # no existe
            {"canal_venta": "TIENDA"},  # canal invalido
        ]
        for rol in ("GS", "ASU"):
            for ep in NUEVOS:
                for params in casos:
                    r = self.get(ep, rol, **params)
                    self.assertEqual(r.status_code, 422, (rol, ep, params, r.text))
        r = self.get("rendimiento-vendedores", "GS", fecha_desde="2026-10-01", fecha_hasta="2026-09-01")
        self.assertEqual(r.status_code, 422)

    # -- cobertura estructural ----------------------------------------------
    def test_todas_las_rutas_de_reportes_tienen_la_dependencia_de_acceso(self):
        rutas = [r for r in reportes_ep.router.routes if hasattr(r, "dependencies")]
        self.assertEqual(len(rutas), len(TODOS))
        for r in rutas:
            self.assertIn(
                reportes_ep._acceso_reportes,
                [d.dependency for d in r.dependencies],
                r.path,
            )

    # -- lectura pura --------------------------------------------------------
    def test_consultar_reportes_no_modifica_datos(self):
        sql = ("select (select count(*) from ventas), (select count(*) from detalle_ventas), "
               "(select coalesce(sum(stock_total),0) from productos), (select count(*) from devoluciones), "
               "(select count(*) from movimientos_inventario), (select count(*) from usuarios), "
               "(select max(ultima_conexion) from usuarios)")
        antes = self.db.execute(text(sql)).all()
        for rol in ("GS", "ASU", "V", "C", "D"):
            for ep in TODOS:
                self.get(ep, rol, **(CON_DATOS if ep in NUEVOS else {}))
        self.assertEqual(antes, self.db.execute(text(sql)).all())


if __name__ == "__main__":
    unittest.main()
