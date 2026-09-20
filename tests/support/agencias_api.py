# Base compartida de las pruebas de API de CU19 (agencias-reparto) contra la BD
# LOCAL de pruebas. Importar SOLO despues de `local_db.configure()`.
#
# Cada test corre en una transaccion externa que SIEMPRE se revierte: la sesion
# (get_db sobreescrito) usa SAVEPOINTs, asi que los commit del service no dejan
# datos. La autenticacion NO se simula: se firman JWT reales con la SECRET_KEY
# de la app para usuarios reales (creados en esa transaccion) de cada rol, y se
# recorre get_current_user -> require_roles -> service.
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.database import engine
from app.core.security import crear_token_acceso
from app.main import app
from tests.support import local_db

BASE = "/api/v1/agencias-reparto"
ROLES = ("ASU", "GS", "D", "V", "C")


def bd_lista() -> bool:
    """True solo si el engine apunta a la BD local `_test` con la migracion CU19."""
    if local_db.modo_supabase_readonly() or not local_db.es_bd_de_pruebas_local():
        return False
    try:
        with engine.connect() as c:
            return bool(c.execute(text("select to_regclass('public.agencias_reparto') is not null")).scalar())
    except Exception:
        return False


def payload(**extra) -> dict:
    datos = {
        "razon_social": "Andes Express",
        "nit": "1020304050",
        "correo_facturacion": "facturas@andes-express.com",
        "direccion_fiscal": "Av. Fiscal 123",
    }
    datos.update(extra)
    return datos


class BaseAgenciasAPI(unittest.TestCase):
    def setUp(self):
        self.conn = engine.connect()
        self.trans = self.conn.begin()
        self.db = Session(bind=self.conn, join_transaction_mode="create_savepoint")
        app.dependency_overrides[get_db] = lambda: self.db  # solo la sesion; la auth es real
        self.client = TestClient(app)
        self._usuarios: dict[str, uuid.UUID] = {}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.trans.rollback()
        self.conn.close()

    # -- usuarios y tokens reales ------------------------------------------
    def _rol_id(self, nombre: str):
        rid = self.db.execute(text("select id_rol from roles where nombre_rol=:n"), {"n": nombre}).scalar()
        if rid is None:
            rid = uuid.uuid4()
            self.db.execute(text("insert into roles (id_rol, nombre_rol) values (:i,:n)"), {"i": rid, "n": nombre})
        return rid

    def crear_usuario(self, rol: str, activo: bool = True) -> uuid.UUID:
        uid = uuid.uuid4()
        self.db.execute(
            text("insert into usuarios (id_usuario,nombre,correo,password,estado,id_rol,intentos_fallidos) "
                 "values (:u,'T',:c,'x',:e,:r,0)"),
            {"u": uid, "c": f"{rol.lower()}.{uid}@andes-express.com", "e": activo, "r": self._rol_id(rol)},
        )
        self.db.commit()
        return uid

    def headers(self, rol: str) -> dict:
        if rol not in self._usuarios:
            self._usuarios[rol] = self.crear_usuario(rol)
        token = crear_token_acceso({"sub": str(self._usuarios[rol]), "rol": rol})
        return {"Authorization": f"Bearer {token}"}

    # -- atajos HTTP --------------------------------------------------------
    def req(self, metodo, ruta="", rol=None, headers=None, **kw):
        h = headers if headers is not None else (self.headers(rol) if rol else {})
        return self.client.request(metodo, f"{BASE}{ruta}", headers=h, **kw)

    def crear(self, rol="GS", **extra) -> dict:
        r = self.req("POST", "", rol, json=payload(**extra))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    # -- fixture de envio real con agencia ---------------------------------
    def envio_con_agencia(self, id_agencia: int) -> int:
        q = lambda sql, **p: self.db.execute(text(sql), p)  # noqa: E731
        u = self.crear_usuario("C")
        v = q("insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
              "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,10,0,'QR','PAGADO',:k,'DOMICILIO',"
              "'N','c@andes-express.com','1','d','c') returning id_venta", u=u, k="T" + uuid.uuid4().hex[:8]).scalar()
        suc = q("select codigo_sucursal from sucursales limit 1").scalar()
        ciudad = q("select id from ciudades limit 1").scalar()
        z = q("insert into agencia_zonas (id_agencia,id_ciudad) values (:a,:c) returning id_zona",
              a=id_agencia, c=ciudad).scalar()
        t = q("insert into agencia_tarifas (id_zona,criterio,rango_min,costo,vigente_desde) "
              "values (:z,'PESO',0,10,'2026-01-01') returning id_tarifa", z=z).scalar()
        e = q("insert into envios (id_venta,estado,codigo_sucursal,id_agencia,id_tarifa_aplicada,"
              "costo_agencia,peso_kg,volumen_m3) values (:v,'ASIGNADO',:s,:a,:t,10,1,1) returning id_envio",
              v=v, s=suc, a=id_agencia, t=t).scalar()
        self.db.commit()
        return e
