# Integracion CU20 sobre la BD LOCAL de pruebas con el SEED DETERMINISTA
# (tests/support/seed_cu20.py). Solo corre contra `tienda_ropa_test`; jamas
# contra Supabase. Construir la BD:
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/support/setup_test_db.py --recreate
#   python tests/integration/test_reportes_local_datos.py
#
# Estrategia anti-autoengano: los valores esperados NO salen del service.
#  1. Estan calculados a mano (tabla del seed) y escritos como constantes.
#  2. `SeedTest` comprueba con SQL crudo que el seed coincide con esa tabla, y
#     varios tests contrastan el endpoint con SQL independiente.
# Si la consulta del service tiene un error, algun test falla.
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

import app.main  # noqa: F401,E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

BASE = "/api/v1/reportes"
MENSAJE = "No se encontraron datos para los parámetros ingresados."
TODO = {"fecha_inicio": "2025-12-01", "fecha_fin": "2026-12-31"}  # abarca todo el seed
DIAS_TODO = (date(2026, 12, 31) - date(2025, 12, 1)).days + 1
SIN_VENTAS = {"fecha_inicio": "2026-04-01", "fecha_fin": "2026-04-30"}


def _seed_disponible() -> bool:
    if not local_db.modo_supabase_readonly() and not local_db.es_bd_de_pruebas_local():
        return False
    if local_db.modo_supabase_readonly():
        return False  # los datos exactos solo existen en la BD local
    try:
        with SessionLocal() as db:
            return db.execute(text("select count(*) from ventas where codigo like 'ATT-T%'")).scalar() == 10
    except Exception:
        return False


@unittest.skipUnless(_seed_disponible(), "BD local con seed CU20 no disponible (ver cabecera)")
class Base(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.execute(text("SET TRANSACTION READ ONLY"))
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(rol=SimpleNamespace(nombre_rol="GS"))
        self.client = TestClient(app)
        self.cat = {n: i for i, n in self.db.execute(text("select id_categoria, nombre from categorias"))}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.rollback()
        self.db.close()

    def sql(self, q, **p):
        return self.db.execute(text(q), p).all()

    def ok(self, ruta, **params):
        r = self.client.get(f"{BASE}/{ruta}", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "success")
        return r.json()["data"]


# ---------------------------------------------------------------------------
class SeedTest(Base):
    """El seed coincide con la tabla documentada (base de los valores a mano)."""

    def test_conteos(self):
        c = lambda t, w="true": self.sql(f"select count(*) from {t} where {w}")[0][0]  # noqa: E731
        self.assertEqual((c("ventas"), c("ventas", "estado_pago='PAGADO'")), (10, 8))
        self.assertEqual(c("ventas", "estado_pago='PAGADO' and id_vendedor is null"), 4)
        self.assertEqual(c("ventas", "estado_pago='PAGADO' and id_vendedor is not null"), 4)
        self.assertEqual((c("productos"), c("categorias"), c("devoluciones")), (9, 4, 7))
        self.assertEqual(c("detalle_devoluciones"), 8)

    def test_totales_de_ventas_pagadas(self):
        n, sub, uni, tot, env = self.sql(
            """select count(distinct v.id_venta), sum(d.subtotal), sum(d.cantidad),
                      (select sum(total) from ventas where estado_pago='PAGADO'),
                      (select sum(costo_envio) from ventas where estado_pago='PAGADO')
               from detalle_ventas d join ventas v using(id_venta) where v.estado_pago='PAGADO'"""
        )[0]
        self.assertEqual((n, sub, uni, tot, env), (8, 2200, 20, 2260, 60))

    def test_totales_de_devoluciones(self):
        efectivo = self.sql(
            """select sum(l.subtotal), sum(l.cantidad_devuelta) from detalle_devoluciones l
               join devoluciones d using(id_devolucion) where d.estado <> 'RECHAZADA'"""
        )[0]
        self.assertEqual(tuple(efectivo), (730, 7))

    def test_inventario_del_seed(self):
        self.assertEqual(
            self.sql("select sum(stock_total), sum(stock_total*precio_venta) from productos where estado<>'Inactivo'")[0][:],
            (109, 13160),
        )


# ---------------------------------------------------------------------------
class VentasTest(Base):
    def test_totales_calculados_a_mano_y_no_pagadas_excluidas(self):
        d = self.ok("ventas", **TODO)
        self.assertEqual(d["num_ventas"], 8)  # V8 (PENDIENTE) y V9 (RECHAZADO) no cuentan
        self.assertEqual(d["unidades_vendidas"], 20)
        self.assertEqual(d["ingresos_productos"], 2200)
        self.assertEqual(d["ticket_promedio"], 275)
        self.assertEqual((d["total_facturado"], d["total_envios"]), (2260, 60))
        self.assertFalse(d["sin_datos"])
        self.assertIsNone(d["mensaje"])

    def test_por_fecha(self):
        d = self.ok("ventas", **TODO)
        self.assertEqual(len(d["por_fecha"]), DIAS_TODO)  # un punto por dia, con ceros
        esperado = {  # fecha UTC: (ingresos, unidades, ventas)
            "2025-12-30": (280, 3, 1), "2025-12-31": (120, 1, 1), "2026-01-01": (650, 4, 2),
            "2026-01-15": (420, 5, 1), "2026-02-28": (310, 3, 1), "2026-03-01": (100, 1, 1),
            "2026-03-15": (320, 3, 1),
        }
        for p in d["por_fecha"]:
            self.assertEqual((p["ingresos"], p["unidades"], p["num_ventas"]), esperado.get(p["fecha"], (0, 0, 0)), p["fecha"])
        fechas = [p["fecha"] for p in d["por_fecha"]]
        self.assertEqual(fechas, sorted(fechas))

    def test_por_categoria(self):
        d = self.ok("ventas", **TODO)
        self.assertEqual(
            [(f["categoria"], f["ingresos"], f["unidades"]) for f in d["por_categoria"]],
            [("Camisas", 1260, 14), ("Pantalones", 690, 5), ("Vestidos", 250, 1)],  # Accesorios: sin ventas
        )

    def test_por_canal(self):
        d = self.ok("ventas", **TODO)
        self.assertEqual([(f["canal"], f["ingresos"], f["num_ventas"]) for f in d["por_canal"]],
                         [("POS", 1150, 4), ("ONLINE", 1050, 4)])

    def test_por_metodo_de_pago(self):
        d = self.ok("ventas", **TODO)
        self.assertEqual([(f["metodo_pago"], f["ingresos"], f["num_ventas"]) for f in d["por_metodo_pago"]],
                         [("QR", 1010, 3), ("TARJETA", 650, 2), ("EFECTIVO", 540, 3)])

    def test_canal_online_y_pos(self):
        on = self.ok("ventas", canal_venta="ONLINE", **TODO)
        self.assertEqual((on["num_ventas"], on["unidades_vendidas"], on["ingresos_productos"], on["total_facturado"], on["total_envios"]),
                         (4, 10, 1050, 1110, 60))
        self.assertEqual([f["canal"] for f in on["por_canal"]], ["ONLINE"])
        pos = self.ok("ventas", canal_venta="pos", **TODO)
        self.assertEqual((pos["num_ventas"], pos["unidades_vendidas"], pos["ingresos_productos"], pos["total_facturado"], pos["total_envios"]),
                         (4, 10, 1150, 1150, 0))
        self.assertEqual([f["canal"] for f in pos["por_canal"]], ["POS"])

    def test_categoria(self):
        d = self.ok("ventas", categoria_id=self.cat["Pantalones"], **TODO)
        self.assertEqual((d["num_ventas"], d["unidades_vendidas"], d["ingresos_productos"]), (4, 5, 690))
        self.assertEqual((d["total_facturado"], d["total_envios"]), (None, None))  # envio no atribuible
        self.assertEqual([f["categoria"] for f in d["por_categoria"]], ["Pantalones"])

    def test_categoria_y_canal_combinados(self):
        d = self.ok("ventas", categoria_id=self.cat["Camisas"], canal_venta="POS", **TODO)
        self.assertEqual((d["num_ventas"], d["unidades_vendidas"], d["ingresos_productos"]), (3, 5, 460))

    def test_categoria_sin_ventas_es_200_sin_datos(self):
        d = self.ok("ventas", categoria_id=self.cat["Accesorios"], **TODO)
        self.assertTrue(d["sin_datos"])
        self.assertEqual((d["num_ventas"], d["ingresos_productos"], d["ticket_promedio"]), (0, 0, 0))

    def test_sin_datos_por_rango(self):
        for rango in (SIN_VENTAS, {"fecha_inicio": "2025-12-01", "fecha_fin": "2025-12-29"}):
            body = self.client.get(f"{BASE}/ventas", params=rango).json()
            d = body["data"]
            self.assertTrue(d["sin_datos"], rango)
            self.assertEqual((d["mensaje"], body["message"]), (MENSAJE, MENSAJE))
            self.assertEqual((d["num_ventas"], d["ingresos_productos"], d["ticket_promedio"]), (0, 0, 0))
            for s in ("por_fecha", "por_categoria", "por_canal", "por_metodo_pago"):
                self.assertEqual(d[s], [], s)

    def test_cruce_de_anio_y_limites_del_dia_utc(self):
        # V2 23:59:59 del 31-dic entra; V3 00:00:00 del 1-ene entra al dia siguiente.
        d = self.ok("ventas", fecha_inicio="2025-12-31", fecha_fin="2026-01-01")
        self.assertEqual((d["num_ventas"], d["unidades_vendidas"], d["ingresos_productos"]), (3, 5, 770))
        self.assertEqual([(p["fecha"], p["ingresos"]) for p in d["por_fecha"]], [("2025-12-31", 120), ("2026-01-01", 650)])
        antes = self.ok("ventas", fecha_inicio="2025-12-30", fecha_fin="2025-12-31")
        self.assertEqual((antes["num_ventas"], antes["ingresos_productos"]), (2, 400))  # V3 (00:00:00 del 1-ene) fuera
        un_dia = self.ok("ventas", fecha_inicio="2026-01-01", fecha_fin="2026-01-01")
        self.assertEqual((un_dia["num_ventas"], un_dia["ingresos_productos"]), (2, 650))  # V3 + V4 (02:00 UTC)

    def test_cruce_de_mes(self):
        d = self.ok("ventas", fecha_inicio="2026-02-28", fecha_fin="2026-03-01")
        self.assertEqual((d["num_ventas"], d["ingresos_productos"]), (2, 410))  # V6 (28-feb) + V7 (1-mar 00:00)
        self.assertEqual([p["fecha"] for p in d["por_fecha"]], ["2026-02-28", "2026-03-01"])
        mes = self.ok("ventas", fecha_inicio="2026-03-01", fecha_fin="2026-03-31")
        self.assertEqual((mes["num_ventas"], mes["ingresos_productos"]), (2, 420))  # V7 + V10; V8/V9 no pagadas

    def test_fecha_fin_sola_completa_inicio_cruzando_mes_y_anio(self):
        f = self.ok("ventas", fecha_fin="2026-03-10")["filtros"]
        self.assertEqual(f["fecha_inicio"], "2026-02-09")
        d = self.ok("ventas", fecha_fin="2026-01-15")
        self.assertEqual(d["filtros"]["fecha_inicio"], "2025-12-17")
        self.assertEqual(d["num_ventas"], 5)  # V1, V2, V3, V4 y V5 (15-ene 12:00)
        n = self.sql("select count(*) from ventas where estado_pago='PAGADO' and fecha_venta >= '2025-12-17' and fecha_venta < '2026-01-16'")[0][0]
        self.assertEqual(d["num_ventas"], n)  # contraste con SQL independiente

    def test_zona_horaria_de_la_sesion_no_cambia_el_reporte(self):
        base = self.ok("ventas", **TODO)["por_fecha"]
        for zona in ("America/La_Paz", "Pacific/Kiritimati", "America/Los_Angeles"):
            self.db.execute(text(f"SET TIME ZONE '{zona}'"))
            self.assertEqual(self.ok("ventas", **TODO)["por_fecha"], base, zona)

    def test_contraste_con_sql_independiente(self):
        d = self.ok("ventas", **TODO)
        esperado = self.sql(
            """select (v.fecha_venta at time zone 'UTC')::date, sum(d.subtotal), sum(d.cantidad), count(distinct v.id_venta)
               from detalle_ventas d join ventas v using(id_venta) where v.estado_pago='PAGADO' group by 1"""
        )
        por_dia = {p["fecha"]: p for p in d["por_fecha"]}
        for dia, ing, uni, n in esperado:
            p = por_dia[dia.isoformat()]
            self.assertEqual((p["ingresos"], p["unidades"], p["num_ventas"]), (float(ing), uni, n))
        cat_sql = self.sql(
            """select c.nombre, sum(d.subtotal) from detalle_ventas d join ventas v using(id_venta)
               join productos p using(id_producto) join categorias c using(id_categoria)
               where v.estado_pago='PAGADO' group by 1"""
        )
        self.assertEqual({f["categoria"]: f["ingresos"] for f in d["por_categoria"]}, {n: float(t) for n, t in cat_sql})


# ---------------------------------------------------------------------------
class TopProductosTest(Base):
    def test_ranking_completo_calculado_a_mano(self):
        d = self.ok("productos-mas-vendidos", **TODO)
        self.assertEqual(d["total_productos_vendidos"], 6)
        self.assertEqual(
            [(i["posicion"], i["producto"], i["cantidad_vendida"], i["total_generado"], i["categoria"], i["num_ventas"]) for i in d["items"]],
            [(1, "Camisa Oxford", 9, 900, "Camisas", 5), (2, "Jean Slim", 3, 450, "Pantalones", 2),
             (3, "Camisa Lino", 3, 240, "Camisas", 2), (4, "Pantalon Chino", 2, 240, "Pantalones", 2),
             (5, "Camisa Rayas", 2, 120, "Camisas", 1), (6, "Vestido Midi", 1, 250, "Vestidos", 1)],
        )  # desempates: 3 unidades -> Jean (450) antes que Lino (240); 2 unidades -> Chino (240) antes que Rayas (120)

    def test_productos_sin_ventas_pagadas_no_aparecen(self):
        nombres = {i["producto"] for i in self.ok("productos-mas-vendidos", top=100, **TODO)["items"]}
        self.assertTrue(nombres.isdisjoint({"Vestido Floral", "Vestido Gala", "Chaqueta Inactiva"}))  # solo V8/V9, o ninguna venta

    def test_top_limita(self):
        d = self.ok("productos-mas-vendidos", top=2, **TODO)
        self.assertEqual([i["producto"] for i in d["items"]], ["Camisa Oxford", "Jean Slim"])
        self.assertEqual(d["total_productos_vendidos"], 6)

    def test_canal_pos(self):
        d = self.ok("productos-mas-vendidos", canal_venta="POS", **TODO)
        self.assertEqual([(i["producto"], i["cantidad_vendida"], i["total_generado"]) for i in d["items"]],
                         [("Jean Slim", 3, 450), ("Camisa Oxford", 3, 300), ("Pantalon Chino", 2, 240), ("Camisa Lino", 2, 160)])

    def test_categoria(self):
        d = self.ok("productos-mas-vendidos", categoria_id=self.cat["Vestidos"], **TODO)
        self.assertEqual([(i["producto"], i["cantidad_vendida"]) for i in d["items"]], [("Vestido Midi", 1)])

    def test_rango_sin_ventas(self):
        d = self.ok("productos-mas-vendidos", **SIN_VENTAS)
        self.assertEqual((d["sin_datos"], d["items"], d["total_productos_vendidos"], d["mensaje"]), (True, [], 0, MENSAJE))

    def test_contraste_con_sql_independiente(self):
        esperado = self.sql(
            """select p.nombre, sum(d.cantidad), sum(d.subtotal) from detalle_ventas d join ventas v using(id_venta)
               join productos p using(id_producto) where v.estado_pago='PAGADO'
               group by p.nombre order by 2 desc, 3 desc, 1"""
        )
        got = self.ok("productos-mas-vendidos", top=100, **TODO)["items"]
        self.assertEqual([(i["producto"], i["cantidad_vendida"], i["total_generado"]) for i in got],
                         [(n, int(u), float(t)) for n, u, t in esperado])


# ---------------------------------------------------------------------------
class InventarioTest(Base):
    FILAS = [  # producto, stock, nivel, vendidas(periodo completo), rotacion, valor
        ("Jean Slim", 0, "CRITICO", 3, None, 0),
        ("Pantalon Chino", 3, "CRITICO", 2, 0.67, 360),
        ("Camisa Rayas", 4, "CRITICO", 2, 0.5, 240),
        ("Vestido Midi", 5, "BAJO", 1, 0.2, 1250),
        ("Camisa Lino", 12, "BAJO", 3, 0.25, 960),
        ("Vestido Gala", 15, "OK", 0, 0.0, 1350),
        ("Vestido Floral", 20, "OK", 0, 0.0, 4000),
        ("Camisa Oxford", 50, "OK", 9, 0.18, 5000),
    ]

    def test_kpis_y_niveles_calculados_a_mano(self):
        d = self.ok("inventario", **TODO)
        self.assertEqual((d["total_productos"], d["stock_total_unidades"], d["valor_inventario"], d["agotados"]), (8, 109, 13160, 1))
        self.assertEqual(d["por_nivel"], {"CRITICO": 3, "BAJO": 2, "OK": 3})
        self.assertEqual((d["total_filas"], len(d["items"])), (8, 8))  # Chaqueta Inactiva (Inactivo) fuera
        self.assertNotIn("Chaqueta Inactiva", [i["producto"] for i in d["items"]])

    def test_filas_niveles_y_rotacion_calculados_a_mano(self):
        d = self.ok("inventario", **TODO)
        self.assertEqual(
            [(i["producto"], i["stock_actual"], i["nivel_stock"], i["unidades_vendidas"], i["rotacion"], i["valor_stock"]) for i in d["items"]],
            self.FILAS,
        )  # orden: stock ascendente

    def test_limites_de_niveles(self):
        por = {i["producto"]: i["nivel_stock"] for i in self.ok("inventario", **TODO)["items"]}
        self.assertEqual(por["Camisa Rayas"], "CRITICO")   # 4 < 5
        self.assertEqual(por["Vestido Midi"], "BAJO")      # 5 no es < 5
        self.assertEqual(por["Vestido Gala"], "OK")        # 15 no es < 15
        self.assertEqual(por["Jean Slim"], "CRITICO")      # stock 0

    def test_stock_cero_rotacion_segura(self):
        jean = next(i for i in self.ok("inventario", **TODO)["items"] if i["producto"] == "Jean Slim")
        self.assertEqual((jean["stock_actual"], jean["unidades_vendidas"]), (0, 3))  # vendio pero sin stock
        self.assertIsNone(jean["rotacion"])          # no se divide por cero
        self.assertFalse(jean["rotacion_disponible"])

    def test_producto_sin_ventas_tiene_rotacion_cero(self):
        gala = next(i for i in self.ok("inventario", **TODO)["items"] if i["producto"] == "Vestido Gala")
        self.assertEqual((gala["unidades_vendidas"], gala["rotacion"], gala["rotacion_disponible"]), (0, 0, True))

    def test_filtro_por_nivel(self):
        for nivel, esperados in (("CRITICO", 3), ("bajo", 2), ("OK", 3)):
            d = self.ok("inventario", nivel_stock=nivel, **TODO)
            self.assertEqual((d["total_filas"], len(d["items"])), (esperados, esperados), nivel)
            self.assertTrue(all(i["nivel_stock"] == nivel.upper() for i in d["items"]))
            self.assertEqual(d["total_productos"], 8)  # KPIs del total

    def test_rotacion_en_otra_ventana_de_fechas(self):
        d = self.ok("inventario", fecha_inicio="2026-03-01", fecha_fin="2026-03-31")  # V7 (P1x1) y V10 (P1x2, P3x1)
        por = {i["producto"]: (i["unidades_vendidas"], i["rotacion"]) for i in d["items"]}
        self.assertEqual(por["Camisa Oxford"], (3, 0.06))
        self.assertEqual(por["Pantalon Chino"], (1, 0.33))
        self.assertEqual(por["Jean Slim"], (0, None))
        self.assertEqual(por["Camisa Lino"], (0, 0.0))

    def test_rotacion_por_canal(self):
        d = self.ok("inventario", canal_venta="POS", **TODO)
        por = {i["producto"]: (i["unidades_vendidas"], i["rotacion"]) for i in d["items"]}
        self.assertEqual(por["Pantalon Chino"], (2, 0.67))
        self.assertEqual(por["Camisa Oxford"], (3, 0.06))
        self.assertEqual(por["Camisa Lino"], (2, 0.17))
        self.assertEqual(por["Vestido Midi"], (0, 0.0))  # solo se vendio online

    def test_ventana_sin_ventas_igual_devuelve_inventario(self):
        d = self.ok("inventario", **SIN_VENTAS)  # el inventario es un snapshot, no depende de haber vendido
        self.assertFalse(d["sin_datos"])
        self.assertEqual(d["total_productos"], 8)
        self.assertTrue(all(i["unidades_vendidas"] == 0 for i in d["items"]))

    def test_categoria(self):
        d = self.ok("inventario", categoria_id=self.cat["Vestidos"], **TODO)
        self.assertEqual([i["producto"] for i in d["items"]], ["Vestido Midi", "Vestido Gala", "Vestido Floral"])
        self.assertEqual((d["total_productos"], d["stock_total_unidades"]), (3, 40))
        sin = self.ok("inventario", categoria_id=self.cat["Accesorios"], **TODO)  # categoria sin productos
        self.assertTrue(sin["sin_datos"])
        self.assertEqual((sin["items"], sin["total_productos"], sin["por_nivel"]), ([], 0, {"CRITICO": 0, "BAJO": 0, "OK": 0}))

    def test_limite_recorta_filas_no_totales(self):
        d = self.ok("inventario", limite=3, **TODO)
        self.assertEqual([i["producto"] for i in d["items"]], ["Jean Slim", "Pantalon Chino", "Camisa Rayas"])
        self.assertEqual((d["total_filas"], d["total_productos"]), (8, 8))

    def test_contraste_con_sql_independiente(self):
        esperado = self.sql(
            """select p.nombre, p.stock_total,
                      case when p.stock_total < 5 then 'CRITICO' when p.stock_total < 15 then 'BAJO' else 'OK' end,
                      coalesce((select sum(d.cantidad) from detalle_ventas d join ventas v using(id_venta)
                                where d.id_producto = p.id_producto and v.estado_pago='PAGADO'), 0)
               from productos p where p.estado <> 'Inactivo' order by p.stock_total, p.nombre"""
        )
        got = self.ok("inventario", **TODO)["items"]
        self.assertEqual([(i["producto"], i["stock_actual"], i["nivel_stock"], i["unidades_vendidas"]) for i in got],
                         [(n, s, lv, int(u)) for n, s, lv, u in esperado])


# ---------------------------------------------------------------------------
class DevolucionesTest(Base):
    def test_totales_calculados_a_mano(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual((d["num_devoluciones"], d["unidades_devueltas"], d["importe_total"], d["importe_completado"]), (7, 7, 730, 550))
        self.assertFalse(d["sin_datos"])

    def test_por_estado_incluye_rechazadas(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual(
            [(e["estado"], e["num_devoluciones"], e["unidades"], e["importe"]) for e in d["por_estado"]],
            [("APROBADA", 1, 1, 80), ("COMPLETADA", 4, 5, 550), ("RECHAZADA", 1, 1, 100), ("SOLICITADA", 1, 1, 100)],
        )
        # RECHAZADA cuenta en conteos/por_estado, pero no en unidades_devueltas ni importe_total
        self.assertEqual(sum(e["num_devoluciones"] for e in d["por_estado"]), d["num_devoluciones"])
        self.assertEqual(sum(e["importe"] for e in d["por_estado"]), d["importe_total"] + 100)

    def test_por_fecha(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual(len(d["por_fecha"]), DIAS_TODO)
        esperado = {
            "2025-12-31": (1, 1, 80), "2026-01-05": (1, 1, 100), "2026-02-03": (1, 2, 250), "2026-02-10": (1, 0, 0),  # RECHAZADA
            "2026-02-20": (1, 1, 80), "2026-03-02": (1, 1, 100), "2026-03-12": (1, 1, 120),
        }
        for p in d["por_fecha"]:
            self.assertEqual((p["num_devoluciones"], p["unidades"], p["importe"]), esperado.get(p["fecha"], (0, 0, 0)), p["fecha"])

    def test_por_producto(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual([(p["producto"], p["categoria"], p["unidades"], p["importe"]) for p in d["por_producto"]],
                         [("Camisa Oxford", "Camisas", 3, 300), ("Camisa Lino", "Camisas", 2, 160),
                          ("Jean Slim", "Pantalones", 1, 150), ("Pantalon Chino", "Pantalones", 1, 120)])
        self.assertEqual(len(self.ok("devoluciones", top=2, **TODO)["por_producto"]), 2)

    def test_por_categoria(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual([(c["categoria"], c["unidades"], c["importe"]) for c in d["por_categoria"]],
                         [("Camisas", 5, 460), ("Pantalones", 2, 270)])

    def test_detalle(self):
        d = self.ok("devoluciones", **TODO)
        self.assertEqual(
            [(x["estado"], x["codigo_venta"], x["unidades"], x["importe"]) for x in d["detalle"]],
            [("COMPLETADA", "ATT-T00010", 1, 120), ("SOLICITADA", "ATT-T00007", 1, 100), ("APROBADA", "ATT-T00006", 1, 80),
             ("RECHAZADA", "ATT-T00005", 1, 100), ("COMPLETADA", "ATT-T00003", 2, 250), ("COMPLETADA", "ATT-T00001", 1, 100),
             ("COMPLETADA", "ATT-T00001", 1, 80)],
        )  # fecha de solicitud descendente; la RECHAZADA muestra el monto solicitado
        self.assertEqual(len(self.ok("devoluciones", limite_detalle=2, **TODO)["detalle"]), 2)

    def test_estado_completada_y_rechazada(self):
        c = self.ok("devoluciones", estado="completada", **TODO)
        self.assertEqual((c["num_devoluciones"], c["unidades_devueltas"], c["importe_total"], c["estado"]), (4, 5, 550, "COMPLETADA"))
        r = self.ok("devoluciones", estado="RECHAZADA", **TODO)
        self.assertEqual((r["num_devoluciones"], r["unidades_devueltas"], r["importe_total"]), (1, 0, 0))  # no hay reembolso
        self.assertEqual([(e["estado"], e["importe"]) for e in r["por_estado"]], [("RECHAZADA", 100)])
        self.assertEqual((r["por_producto"], r["por_categoria"]), ([], []))
        self.assertFalse(r["sin_datos"])  # existe la devolucion, aunque no sume unidades

    def test_canal_de_la_venta_original(self):
        pos = self.ok("devoluciones", canal_venta="POS", **TODO)
        self.assertEqual((pos["num_devoluciones"], pos["unidades_devueltas"], pos["importe_total"]), (3, 4, 450))
        on = self.ok("devoluciones", canal_venta="ONLINE", **TODO)
        self.assertEqual((on["num_devoluciones"], on["unidades_devueltas"], on["importe_total"]), (4, 3, 280))

    def test_categoria_y_devolucion_con_dos_categorias(self):
        p = self.ok("devoluciones", categoria_id=self.cat["Pantalones"], **TODO)
        self.assertEqual((p["num_devoluciones"], p["unidades_devueltas"], p["importe_total"]), (2, 2, 270))
        c = self.ok("devoluciones", categoria_id=self.cat["Camisas"], **TODO)
        self.assertEqual((c["num_devoluciones"], c["unidades_devueltas"], c["importe_total"]), (6, 5, 460))  # D2 cuenta una vez
        cc = self.ok("devoluciones", categoria_id=self.cat["Camisas"], estado="COMPLETADA", **TODO)
        self.assertEqual((cc["num_devoluciones"], cc["unidades_devueltas"], cc["importe_total"]), (3, 3, 280))

    def test_cruce_de_anio(self):
        d = self.ok("devoluciones", fecha_inicio="2025-12-31", fecha_fin="2026-01-05")
        self.assertEqual((d["num_devoluciones"], d["importe_total"], len(d["por_fecha"])), (2, 180, 6))

    def test_cruce_de_mes(self):
        d = self.ok("devoluciones", fecha_inicio="2026-02-28", fecha_fin="2026-03-02")
        self.assertEqual((d["num_devoluciones"], d["importe_total"]), (1, 100))  # D5 (2-mar); D4 fue el 20-feb

    def test_sin_datos(self):
        casos = [SIN_VENTAS, {**TODO, "categoria_id": self.cat["Vestidos"]},
                 {**TODO, "canal_venta": "POS", "estado": "RECHAZADA"}, {**TODO, "categoria_id": self.cat["Accesorios"]}]
        for params in casos:
            body = self.client.get(f"{BASE}/devoluciones", params=params).json()
            d = body["data"]
            self.assertTrue(d["sin_datos"], params)
            self.assertEqual((body["message"], d["mensaje"]), (MENSAJE, MENSAJE))
            self.assertEqual((d["num_devoluciones"], d["unidades_devueltas"], d["importe_total"]), (0, 0, 0))
            for s in ("por_estado", "por_fecha", "por_producto", "por_categoria", "detalle"):
                self.assertEqual(d[s], [], (params, s))

    def test_contraste_con_sql_independiente(self):
        d = self.ok("devoluciones", **TODO)
        filas = self.sql(
            """select dv.estado, count(distinct dv.id_devolucion), sum(l.cantidad_devuelta), sum(l.subtotal)
               from detalle_devoluciones l join devoluciones dv using(id_devolucion) group by 1 order by 1"""
        )
        self.assertEqual([(e["estado"], e["num_devoluciones"], e["unidades"], e["importe"]) for e in d["por_estado"]],
                         [(e, n, int(u), float(i)) for e, n, u, i in filas])
        porprod = self.sql(
            """select p.nombre, sum(l.cantidad_devuelta), sum(l.subtotal) from detalle_devoluciones l
               join devoluciones dv using(id_devolucion) join productos p on p.id_producto=l.id_producto
               where dv.estado <> 'RECHAZADA' group by 1"""
        )
        self.assertEqual({p["producto"]: (p["unidades"], p["importe"]) for p in d["por_producto"]},
                         {n: (int(u), float(i)) for n, u, i in porprod})


# ---------------------------------------------------------------------------
class FiltrosInvalidosTest(Base):
    def test_422_en_todos_los_reportes(self):
        for ep in ("ventas", "productos-mas-vendidos", "inventario", "devoluciones"):
            for params in ({"fecha_inicio": "2026-10-01", "fecha_fin": "2026-09-01"}, {"categoria_id": 99999},
                           {"categoria_id": 0}, {"canal_venta": "TIENDA"}, {"fecha_inicio": "ayer"}):
                self.assertEqual(self.client.get(f"{BASE}/{ep}", params=params).status_code, 422, (ep, params))

    def test_422_propios_de_cada_reporte(self):
        for ep, params in (("inventario", {"nivel_stock": "x"}), ("inventario", {"limite": 0}),
                           ("devoluciones", {"estado": "INVENTADO"}), ("productos-mas-vendidos", {"top": 0}),
                           ("productos-mas-vendidos", {"top": 101})):
            self.assertEqual(self.client.get(f"{BASE}/{ep}", params=params).status_code, 422, (ep, params))

    def test_consultar_no_modifica_datos(self):
        sql = ("select (select count(*) from ventas), (select count(*) from detalle_ventas), "
               "(select sum(stock_total) from productos), (select count(*) from devoluciones), "
               "(select count(*) from movimientos_inventario)")
        antes = self.sql(sql)
        for ep in ("ventas", "productos-mas-vendidos", "inventario", "devoluciones", "rendimiento-vendedores"):
            self.ok(ep, **(TODO if ep != "rendimiento-vendedores" else {}))
        self.assertEqual(antes, self.sql(sql))


if __name__ == "__main__":
    unittest.main()
