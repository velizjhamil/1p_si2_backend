# Integracion CU19 fase 2: esquema real (constraints/indices/FK) en la BD LOCAL
# de pruebas. Solo corre contra `tienda_ropa_test`; jamas contra Supabase.
# Cada test corre dentro de una transaccion que SIEMPRE se revierte (rollback),
# asi que no deja datos.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_esquema.py
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

import app.main  # noqa: F401,E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402


def _bd_lista() -> bool:
    if local_db.modo_supabase_readonly() or not local_db.es_bd_de_pruebas_local():
        return False
    try:
        with SessionLocal() as db:
            return db.execute(
                text("select to_regclass('public.agencia_tarifas') is not null")
            ).scalar()
    except Exception:
        return False


@unittest.skipUnless(_bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class EsquemaCU19(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # -- helpers (SQL crudo: prueban la DB, no los modelos) -----------------
    def q(self, sql, **p):
        return self.db.execute(text(sql), p)

    def agencia(self, razon="Agencia Uno", nit="1020304050", **extra):
        cols = {
            "razon_social": razon,
            "nit": nit,
            "correo_facturacion": "fact@x.test",
            "direccion_fiscal": "Av. Fiscal 1",
            **extra,
        }
        return self.q(
            f"insert into agencias_reparto ({', '.join(cols)}) "
            f"values ({', '.join(':' + c for c in cols)}) returning id_agencia",
            **cols,
        ).scalar()

    def ciudad(self):
        return self.q("select id from ciudades order by id limit 1").scalar()

    def zona(self, id_agencia, nombre=None):
        return self.q(
            "insert into agencia_zonas (id_agencia, id_ciudad, nombre_zona) "
            "values (:a, :c, :n) returning id_zona",
            a=id_agencia, c=self.ciudad(), n=nombre,
        ).scalar()

    def tarifa(self, id_zona, **extra):
        cols = {
            "id_zona": id_zona, "criterio": "PESO", "rango_min": 0,
            "rango_max": 5, "costo": 10, "vigente_desde": "2026-01-01", **extra,
        }
        return self.q(
            f"insert into agencia_tarifas ({', '.join(cols)}) "
            f"values ({', '.join(':' + c for c in cols)}) returning id_tarifa",
            **cols,
        ).scalar()

    def rechaza(self, fn, fragmento):
        """La operacion falla con IntegrityError que menciona `fragmento`."""
        with self.assertRaises(IntegrityError) as cm:
            with self.db.begin_nested():
                fn()
        self.assertIn(fragmento, str(cm.exception))

    # -- agencias_reparto ---------------------------------------------------
    def test_agencia_valida_e_is_active_por_defecto(self):
        i = self.agencia()
        self.assertTrue(self.q("select is_active from agencias_reparto where id_agencia=:i", i=i).scalar())

    def test_nit_duplicado_rechazado(self):
        self.agencia("A", "111")
        self.rechaza(lambda: self.agencia("B", "111"), "ix_agencias_reparto_nit")

    def test_razon_social_duplicada_sin_distinguir_mayusculas(self):
        self.agencia("Andes Express", "111")
        self.rechaza(lambda: self.agencia("  ANDES express ", "222"), "uq_agencias_reparto_razon_social_lower")

    def test_facturacion_obligatoria(self):
        for campo in ("correo_facturacion", "direccion_fiscal", "razon_social", "nit"):
            with self.subTest(campo):
                self.rechaza(lambda c=campo: self.agencia(**{"nit": "9", "razon_social": "Z", c: None}), campo)

    def test_razon_social_y_nit_no_vacios(self):
        self.rechaza(lambda: self.agencia("   ", "5"), "razon_social_no_vacia")
        self.rechaza(lambda: self.agencia("Z", "  "), "nit_no_vacio")

    # -- agencia_zonas ------------------------------------------------------
    def test_zona_duplicada_incluyendo_ciudad_completa(self):
        a = self.agencia()
        self.zona(a, None)
        self.rechaza(lambda: self.zona(a, ""), "uq_agencia_zonas_cobertura")  # NULL == ''
        self.zona(a, "Zona Sur")
        self.rechaza(lambda: self.zona(a, " zona SUR "), "uq_agencia_zonas_cobertura")

    def test_misma_zona_en_otra_agencia_permitida(self):
        self.zona(self.agencia("A", "1"), "Centro")
        self.zona(self.agencia("B", "2"), "Centro")

    def test_zona_exige_ciudad_existente(self):
        a = self.agencia()
        self.rechaza(
            lambda: self.q("insert into agencia_zonas (id_agencia, id_ciudad) values (:a, -1)", a=a),
            "fk_agencia_zonas_id_ciudad_ciudades",
        )

    # -- agencia_tarifas ----------------------------------------------------
    def test_tarifa_valida_tramo_abierto_y_vigencia_abierta(self):
        z = self.zona(self.agencia())
        self.tarifa(z, rango_min=5, rango_max=None, vigente_hasta=None)
        self.tarifa(z, criterio="VOLUMEN")

    def test_tarifa_checks(self):
        z = self.zona(self.agencia())
        casos = [
            ({"criterio": "PESADO"}, "criterio_valido"),
            ({"rango_min": -1}, "rango_min_no_negativo"),
            ({"rango_min": 5, "rango_max": 5}, "rango_max_mayor_que_min"),
            ({"rango_min": 5, "rango_max": 2}, "rango_max_mayor_que_min"),
            ({"costo": -0.01}, "costo_no_negativo"),
            ({"vigente_desde": "2026-06-01", "vigente_hasta": "2026-05-31"}, "vigencia_valida"),
        ]
        for extra, ck in casos:
            with self.subTest(ck=ck, extra=extra):
                self.rechaza(lambda e=extra: self.tarifa(z, **e), ck)

    def test_cascada_agencia_zona_tarifa(self):
        a = self.agencia()
        z = self.zona(a)
        self.tarifa(z)
        self.q("delete from agencias_reparto where id_agencia=:a", a=a)
        self.assertEqual(self.q("select count(*) from agencia_zonas where id_agencia=:a", a=a).scalar(), 0)
        self.assertEqual(self.q("select count(*) from agencia_tarifas where id_zona=:z", z=z).scalar(), 0)

    # -- envios -------------------------------------------------------------
    def envio_base(self):
        """Crea usuario D, venta y envio ASIGNADO minimo; devuelve (id_envio, id_usuario)."""
        rol = self.q("select id_rol from roles where nombre_rol='D'").scalar()
        if rol is None:
            rol = uuid.uuid4()
            self.q("insert into roles (id_rol, nombre_rol) values (:r, 'D')", r=rol)
        u = uuid.uuid4()
        self.q(
            "insert into usuarios (id_usuario, nombre, correo, password, estado, id_rol, intentos_fallidos) "
            "values (:u, 'T', :c, 'x', true, :r, 0)",
            u=u, c=f"{u}@x.test", r=rol,
        )
        v = self.q(
            "insert into ventas (id_cliente, total, costo_envio, metodo_pago, estado_pago, codigo, "
            "tipo_entrega, nombre_cliente, correo, telefono, direccion, ciudad) "
            "values (:u, 10, 0, 'QR', 'PAGADO', :cod, 'DOMICILIO', 'N', 'c@x.test', '1', 'd', 'c') "
            "returning id_venta",
            u=u, cod="T" + uuid.uuid4().hex[:8],
        ).scalar()
        suc = self.q("select codigo_sucursal from sucursales limit 1").scalar()
        e = self.q(
            "insert into envios (id_venta, estado, codigo_sucursal) values (:v, 'LISTO_ENVIO', :s) returning id_envio",
            v=v, s=suc,
        ).scalar()
        return e, u

    def test_envio_existente_sin_agencia_sigue_valido(self):
        e, u = self.envio_base()
        self.q("update envios set id_repartidor=:u, estado='ASIGNADO' where id_envio=:e", u=u, e=e)
        fila = self.q(
            "select id_agencia, id_tarifa_aplicada, costo_agencia, peso_kg, volumen_m3 from envios where id_envio=:e",
            e=e,
        ).one()
        self.assertEqual(tuple(fila), (None,) * 5)

    def _agencia_y_tarifa(self):
        a = self.agencia()
        return a, self.tarifa(self.zona(a))

    def test_envio_con_agencia_snapshot_completo(self):
        e, _ = self.envio_base()
        a, t = self._agencia_y_tarifa()
        self.q(
            "update envios set id_agencia=:a, id_tarifa_aplicada=:t, costo_agencia=12.5, "
            "peso_kg=2.5, volumen_m3=0.02 where id_envio=:e", a=a, t=t, e=e,
        )

    def test_envio_agencia_y_repartidor_exclusivos(self):
        e, u = self.envio_base()
        a, t = self._agencia_y_tarifa()
        self.rechaza(
            lambda: self.q(
                "update envios set id_repartidor=:u, id_agencia=:a, id_tarifa_aplicada=:t, "
                "costo_agencia=1, peso_kg=1, volumen_m3=1 where id_envio=:e", u=u, a=a, t=t, e=e,
            ),
            "repartidor_o_agencia",
        )

    def test_envio_agencia_exige_snapshot(self):
        e, _ = self.envio_base()
        a, t = self._agencia_y_tarifa()
        incompletos = [
            "id_tarifa_aplicada=NULL, costo_agencia=1, peso_kg=1, volumen_m3=1",
            "id_tarifa_aplicada=:t, costo_agencia=NULL, peso_kg=1, volumen_m3=1",
            "id_tarifa_aplicada=:t, costo_agencia=1, peso_kg=NULL, volumen_m3=1",
            "id_tarifa_aplicada=:t, costo_agencia=1, peso_kg=1, volumen_m3=NULL",
            "id_tarifa_aplicada=:t, costo_agencia=1, peso_kg=0, volumen_m3=1",
            "id_tarifa_aplicada=:t, costo_agencia=1, peso_kg=1, volumen_m3=-1",
        ]
        for sets in incompletos:
            with self.subTest(sets=sets):
                self.rechaza(
                    lambda s=sets: self.q(f"update envios set id_agencia=:a, {s} where id_envio=:e", a=a, t=t, e=e),
                    "agencia_snapshot_completo",
                )

    def test_datos_de_agencia_sin_agencia_rechazados(self):
        e, _ = self.envio_base()
        self.rechaza(
            lambda: self.q("update envios set peso_kg=1 where id_envio=:e", e=e),
            "datos_agencia_requieren_agencia",
        )

    def test_costo_agencia_no_negativo(self):
        e, _ = self.envio_base()
        a, t = self._agencia_y_tarifa()
        self.rechaza(
            lambda: self.q(
                "update envios set id_agencia=:a, id_tarifa_aplicada=:t, costo_agencia=-1, "
                "peso_kg=1, volumen_m3=1 where id_envio=:e", a=a, t=t, e=e,
            ),
            "costo_agencia_no_negativo",
        )

    def test_borrar_agencia_o_tarifa_con_envios_bloqueado(self):
        e, _ = self.envio_base()
        a, t = self._agencia_y_tarifa()
        self.q(
            "update envios set id_agencia=:a, id_tarifa_aplicada=:t, costo_agencia=5, "
            "peso_kg=1, volumen_m3=1 where id_envio=:e", a=a, t=t, e=e,
        )
        self.rechaza(lambda: self.q("delete from agencias_reparto where id_agencia=:a", a=a),
                     "fk_envios_id_agencia_agencias_reparto")
        self.rechaza(lambda: self.q("delete from agencia_tarifas where id_tarifa=:t", t=t),
                     "fk_envios_id_tarifa_aplicada_agencia_tarifas")

    def test_deshabilitar_agencia_con_envios_permitido(self):
        e, _ = self.envio_base()
        a, t = self._agencia_y_tarifa()
        self.q(
            "update envios set id_agencia=:a, id_tarifa_aplicada=:t, costo_agencia=5, "
            "peso_kg=1, volumen_m3=1 where id_envio=:e", a=a, t=t, e=e,
        )
        self.q("update agencias_reparto set is_active=false where id_agencia=:a", a=a)
        self.assertEqual(self.q("select id_agencia from envios where id_envio=:e", e=e).scalar(), a)


if __name__ == "__main__":
    unittest.main()
