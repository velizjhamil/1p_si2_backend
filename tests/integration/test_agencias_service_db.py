# Integracion CU19 fase 3: agencias_service contra la BD LOCAL de pruebas.
# Solo corre contra `tienda_ropa_test`; jamas contra Supabase. Cada test usa una
# sesion enlazada a una transaccion externa que SIEMPRE se revierte (los commit
# del service son SAVEPOINTs), asi que no deja datos.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_service_db.py
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

import app.main  # noqa: F401,E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.modules.delivery import agencias_service as svc  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia import AgenciaCreate, AgenciaUpdate  # noqa: E402


def _bd_lista() -> bool:
    if local_db.modo_supabase_readonly() or not local_db.es_bd_de_pruebas_local():
        return False
    try:
        with engine.connect() as c:
            return c.execute(text("select to_regclass('public.agencias_reparto') is not null")).scalar()
    except Exception:
        return False


def usuario(rol: str) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
        estado=True, rol=Rol(nombre_rol=rol),
    )


def datos(**extra) -> AgenciaCreate:
    base = dict(
        razon_social="Andes Express", nit="1020304050",
        correo_facturacion="facturas@andes-express.com", direccion_fiscal="Av. Fiscal 123",
    )
    base.update(extra)
    return AgenciaCreate(**base)


@unittest.skipUnless(_bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ServicioAgenciasBD(unittest.TestCase):
    def setUp(self):
        self.conn = engine.connect()
        self.trans = self.conn.begin()
        self.db = Session(bind=self.conn, join_transaction_mode="create_savepoint")
        self.gs, self.d = usuario("GS"), usuario("D")

    def tearDown(self):
        self.db.close()
        self.trans.rollback()
        self.conn.close()

    def crear(self, **extra):
        return svc.crear_agencia(self.db, self.gs, datos(**extra))

    def envio_con_agencia(self, id_agencia: int) -> int:
        """Envio real asignado a la agencia (SQL crudo, respeta los CHECK)."""
        q = lambda sql, **p: self.db.execute(text(sql), p)  # noqa: E731
        rol = q("select id_rol from roles limit 1").scalar()
        u = uuid.uuid4()
        q("insert into usuarios (id_usuario, nombre, correo, password, estado, id_rol, intentos_fallidos) "
          "values (:u,'T',:c,'x',true,:r,0)", u=u, c=f"{u}@x.test", r=rol)
        v = q("insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
              "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,10,0,'QR','PAGADO',:k,'DOMICILIO',"
              "'N','c@x.test','1','d','c') returning id_venta", u=u, k="T" + uuid.uuid4().hex[:8]).scalar()
        suc = q("select codigo_sucursal from sucursales limit 1").scalar()
        ciudad = q("select id from ciudades limit 1").scalar()
        z = q("insert into agencia_zonas (id_agencia,id_ciudad) values (:a,:c) returning id_zona",
              a=id_agencia, c=ciudad).scalar()
        t = q("insert into agencia_tarifas (id_zona,criterio,rango_min,costo,vigente_desde) "
              "values (:z,'PESO',0,10,'2026-01-01') returning id_tarifa", z=z).scalar()
        e = q("insert into envios (id_venta,estado,codigo_sucursal,id_agencia,id_tarifa_aplicada,"
              "costo_agencia,peso_kg,volumen_m3) values (:v,'ASIGNADO',:s,:a,:t,10,1,1) returning id_envio",
              v=v, s=suc, a=id_agencia, t=t).scalar()
        self.db.commit()  # confirma el fixture (SAVEPOINT): un rollback del service no lo deshace
        return e

    # -- crear --------------------------------------------------------------
    def test_crear_persiste_normalizado_y_habilitada(self):
        a = self.crear(nit="1.020-304 050", razon_social="  Andes    Express ")
        fila = self.db.execute(
            text("select nit, razon_social, is_active, correo_facturacion from agencias_reparto where id_agencia=:i"),
            {"i": a.id_agencia},
        ).one()
        self.assertEqual(tuple(fila), ("1020304050", "Andes Express", True, "facturas@andes-express.com"))

    def test_nit_duplicado_aunque_venga_con_otro_formato(self):
        self.crear()
        with self.assertRaises(HTTPException) as cm:
            self.crear(razon_social="Otra Agencia", nit="1.020.304-050")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("NIT", cm.exception.detail)

    def test_razon_social_duplicada_ignora_mayusculas_y_espacios(self):
        self.crear()
        for variante in ("ANDES EXPRESS", "andes express", "  Andes   Express  ", "aNdEs  eXpReSs"):
            with self.subTest(variante), self.assertRaises(HTTPException) as cm:
                self.crear(razon_social=variante, nit="9998887776")
            self.assertEqual(cm.exception.status_code, 409)
            self.assertIn("razon social", cm.exception.detail)

    def test_nombre_distinto_no_es_duplicado(self):
        self.crear()
        self.crear(razon_social="Andes Express Norte", nit="55566677")

    def test_carrera_real_integrity_error_por_nit(self):
        """Si el chequeo previo no ve el duplicado (otra transaccion lo creo
        justo antes), el UNIQUE de la DB lo frena y el service responde 409."""
        self.crear()
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            with self.assertRaises(HTTPException) as cm:
                self.crear(razon_social="Otra Agencia")  # mismo NIT
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("NIT", cm.exception.detail)
        # la sesion sigue usable tras el rollback
        self.crear(razon_social="Tercera Agencia", nit="4445556667")

    def test_carrera_real_integrity_error_por_razon_social(self):
        self.crear()
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            with self.assertRaises(HTTPException) as cm:
                self.crear(nit="7778889990", razon_social="ANDES express")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("razon social", cm.exception.detail)

    # -- actualizar ---------------------------------------------------------
    def test_actualizar_y_conflicto_con_otra_agencia(self):
        a = self.crear()
        b = self.crear(razon_social="Beta Cargo", nit="2223334445")
        r = svc.actualizar_agencia(self.db, self.gs, a.id_agencia, AgenciaUpdate(telefono="70000000", razon_social="Andes Express Plus"))
        self.assertEqual((r.telefono, r.razon_social, r.nit), ("70000000", "Andes Express Plus", "1020304050"))
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_agencia(self.db, self.gs, b.id_agencia, AgenciaUpdate(nit="1020-304-050"))
        self.assertEqual(cm.exception.status_code, 409)
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_agencia(self.db, self.gs, b.id_agencia, AgenciaUpdate(razon_social="  ANDES express  plus"))
        self.assertEqual(cm.exception.status_code, 409)

    def test_actualizar_puede_conservar_su_propio_nit_y_razon(self):
        a = self.crear()
        r = svc.actualizar_agencia(self.db, self.gs, a.id_agencia,
                                   AgenciaUpdate(nit="1.020.304.050", razon_social="ANDES EXPRESS"))
        self.assertEqual((r.nit, r.razon_social), ("1020304050", "ANDES EXPRESS"))

    def test_actualizar_inexistente_404(self):
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_agencia(self.db, self.gs, 999999, AgenciaUpdate(telefono="1"))
        self.assertEqual(cm.exception.status_code, 404)

    # -- estado -------------------------------------------------------------
    def test_deshabilitar_con_envios_no_modifica_el_envio(self):
        a = self.crear()
        e = self.envio_con_agencia(a.id_agencia)
        antes = self.db.execute(text("select estado, id_agencia, costo_agencia, fecha_actualizacion from envios where id_envio=:e"), {"e": e}).one()
        r = svc.cambiar_estado(self.db, self.gs, a.id_agencia, False)
        self.assertFalse(r.is_active)
        despues = self.db.execute(text("select estado, id_agencia, costo_agencia, fecha_actualizacion from envios where id_envio=:e"), {"e": e}).one()
        self.assertEqual(tuple(antes), tuple(despues))
        self.assertTrue(svc.cambiar_estado(self.db, self.gs, a.id_agencia, True).is_active)

    # -- eliminar -----------------------------------------------------------
    def test_eliminar_sin_envios(self):
        a = self.crear()
        self.assertEqual(svc.eliminar_agencia(self.db, self.gs, a.id_agencia), "Andes Express")
        self.assertEqual(self.db.execute(text("select count(*) from agencias_reparto where id_agencia=:i"), {"i": a.id_agencia}).scalar(), 0)

    def test_eliminar_con_envios_409_y_la_agencia_sigue(self):
        a = self.crear()
        self.envio_con_agencia(a.id_agencia)
        with self.assertRaises(HTTPException) as cm:
            svc.eliminar_agencia(self.db, self.gs, a.id_agencia)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(svc.contar_envios(self.db, a.id_agencia), 1)
        self.assertIsNotNone(svc.obtener_agencia(self.db, self.gs, a.id_agencia))

    # -- consulta por rol ---------------------------------------------------
    def test_listado_por_rol_y_filtros(self):
        a = self.crear()
        b = self.crear(razon_social="Beta Cargo", nit="2223334445", contacto_operativo="Ana Perez")
        svc.cambiar_estado(self.db, self.gs, b.id_agencia, False)

        items, total = svc.listar_agencias(self.db, self.gs)
        self.assertEqual((total, [i.razon_social for i in items]), (2, ["Andes Express", "Beta Cargo"]))
        self.assertEqual(svc.listar_agencias(self.db, self.gs, is_active=False)[1], 1)
        self.assertEqual(svc.listar_agencias(self.db, self.gs, q="2223")[0][0].id_agencia, b.id_agencia)
        self.assertEqual(svc.listar_agencias(self.db, self.gs, q="ana per")[1], 1)

        # D: solo habilitadas; su filtro is_active=False se ignora; no busca por NIT
        items_d, total_d = svc.listar_agencias(self.db, self.d, is_active=False)
        self.assertEqual((total_d, items_d[0].id_agencia), (1, a.id_agencia))
        self.assertEqual(svc.listar_agencias(self.db, self.d, q="1020304050")[1], 0)

    def test_paginacion(self):
        for i in range(5):
            self.crear(razon_social=f"Agencia {i}", nit=f"1000000{i}1")
        items, total = svc.listar_agencias(self.db, self.gs, page=2, limit=2)
        self.assertEqual((total, len(items)), (5, 2))

    def test_d_no_ve_deshabilitada_por_id_y_gs_si(self):
        a = self.crear()
        svc.cambiar_estado(self.db, self.gs, a.id_agencia, False)
        with self.assertRaises(HTTPException) as cm:
            svc.obtener_agencia(self.db, self.d, a.id_agencia)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertFalse(svc.obtener_agencia(self.db, self.gs, a.id_agencia).is_active)

    def test_detalle_con_totales(self):
        a = self.crear()
        self.envio_con_agencia(a.id_agencia)
        self.db.refresh(a)
        det = svc.serializar_detalle(self.db, a, self.gs)
        self.assertEqual((det["total_envios"], det["total_zonas"]), (1, 1))
        self.assertIn("nit", det)
        self.assertNotIn("nit", svc.serializar_detalle(self.db, a, self.d))


if __name__ == "__main__":
    unittest.main()
