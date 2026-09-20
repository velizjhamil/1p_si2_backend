# Integracion CU20 (BD configurada, SOLO LECTURA): endpoints de reportes.
#
# Cada test compara la respuesta HTTP con SQL crudo independiente ejecutado en
# la misma transaccion READ ONLY, asi que no depende de datos fijos. Se omite
# si la BD no responde. Ejecutar desde la raiz del backend:
#   python tests/integration/test_reportes_endpoints.py
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

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.reportes import service  # noqa: E402

BASE = "/api/v1/reportes"
ENDPOINTS = ("ventas", "productos-mas-vendidos", "inventario", "devoluciones")
MENSAJE = "No se encontraron datos para los parámetros ingresados."
CON_DATOS = {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-12-31"}
SIN_DATOS = {"fecha_inicio": "2020-01-01", "fecha_fin": "2020-01-10"}
UTC = timezone.utc
INI = datetime(2026, 1, 1, tzinfo=UTC)
FIN = datetime(2027, 1, 1, tzinfo=UTC)  # exclusivo


def _db_disponible() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _usuario(rol: str):
    return SimpleNamespace(rol=SimpleNamespace(nombre_rol=rol))


@unittest.skipUnless(_db_disponible(), "BD no disponible")
class ReportesEndpointsBase(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.execute(text("SET TRANSACTION READ ONLY"))  # nada puede escribir
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: _usuario("GS")
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.rollback()
        self.db.close()

    # -- utilidades --------------------------------------------------------
    def get(self, ruta, rol="GS", **params):
        app.dependency_overrides[get_current_user] = lambda: _usuario(rol)
        return self.client.get(f"{BASE}/{ruta}", params=params)

    def ok(self, ruta, **params):
        r = self.get(ruta, **params)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()  # serializacion JSON valida
        self.assertEqual(set(body), {"status", "data", "message"})
        self.assertEqual(body["status"], "success")
        return body

    def sql(self, q, **p):
        return self.db.execute(text(q), p).all()

    def rango(self, **extra):
        return {"a": INI, "b": FIN, **extra}


class VentasTest(ReportesEndpointsBase):
    def test_200_con_datos_coincide_con_sql(self):
        d = self.ok("ventas", **CON_DATOS)["data"]
        n, u, ing = self.sql(
            """select count(distinct v.id_venta), coalesce(sum(d.cantidad),0), coalesce(sum(d.subtotal),0)
               from detalle_ventas d join ventas v using(id_venta)
               where v.estado_pago='PAGADO' and v.fecha_venta>=:a and v.fecha_venta<:b""",
            **self.rango(),
        )[0]
        self.assertEqual((d["num_ventas"], d["unidades_vendidas"]), (n, u))
        self.assertAlmostEqual(d["ingresos_productos"], float(ing), places=2)
        self.assertFalse(d["sin_datos"])
        self.assertIsNone(d["mensaje"])
        # importes son NUMEROS JSON, no strings
        for k in ("ingresos_productos", "ticket_promedio", "total_facturado", "total_envios"):
            self.assertIsInstance(d[k], (int, float), k)
        if n:
            self.assertAlmostEqual(d["ticket_promedio"], float(ing) / n, places=2)

    def test_ventas_por_fecha(self):
        d = self.ok("ventas", **CON_DATOS)["data"]
        self.assertEqual(len(d["por_fecha"]), 365)  # un punto por dia, con ceros
        fechas = [p["fecha"] for p in d["por_fecha"]]
        self.assertEqual(fechas, sorted(fechas))
        self.assertAlmostEqual(sum(p["ingresos"] for p in d["por_fecha"]), d["ingresos_productos"], places=2)
        self.assertEqual(sum(p["unidades"] for p in d["por_fecha"]), d["unidades_vendidas"])
        esperado = self.sql(
            """select (v.fecha_venta at time zone 'UTC')::date, sum(d.subtotal)
               from detalle_ventas d join ventas v using(id_venta)
               where v.estado_pago='PAGADO' and v.fecha_venta>=:a and v.fecha_venta<:b group by 1""",
            **self.rango(),
        )
        por_dia = {p["fecha"]: p["ingresos"] for p in d["por_fecha"]}
        for dia, total in esperado:
            self.assertAlmostEqual(por_dia[dia.isoformat()], float(total), places=2)

    def test_ventas_por_categoria_canal_y_metodo_suman_el_total(self):
        d = self.ok("ventas", **CON_DATOS)["data"]
        total = d["ingresos_productos"]
        for serie in ("por_categoria", "por_canal", "por_metodo_pago"):
            self.assertAlmostEqual(sum(f["ingresos"] for f in d[serie]), total, places=2, msg=serie)
        self.assertEqual(sum(f["num_ventas"] for f in d["por_canal"]), d["num_ventas"])
        self.assertEqual(sum(f["unidades"] for f in d["por_categoria"]), d["unidades_vendidas"])
        self.assertTrue({f["canal"] for f in d["por_canal"]} <= {"ONLINE", "POS"})
        self.assertTrue({f["metodo_pago"] for f in d["por_metodo_pago"]} <= {"QR", "EFECTIVO", "TARJETA"})

    def test_filtro_canal(self):
        for canal, cond in (("ONLINE", "is null"), ("POS", "is not null")):
            d = self.ok("ventas", canal_venta=canal.lower(), **CON_DATOS)["data"]
            n = self.sql(
                f"""select count(*) from ventas v where v.estado_pago='PAGADO'
                    and v.id_vendedor {cond} and v.fecha_venta>=:a and v.fecha_venta<:b""",
                **self.rango(),
            )[0][0]
            self.assertEqual(d["num_ventas"], n, canal)
            self.assertEqual(d["filtros"]["canal_venta"], canal)
            self.assertTrue(all(f["canal"] == canal for f in d["por_canal"]))

    def test_filtro_categoria(self):
        cats = self.ok("ventas", **CON_DATOS)["data"]["por_categoria"]
        if not cats:
            self.skipTest("sin ventas para probar categoria")
        cat = cats[0]
        d = self.ok("ventas", categoria_id=cat["id_categoria"], **CON_DATOS)["data"]
        self.assertAlmostEqual(d["ingresos_productos"], cat["ingresos"], places=2)
        self.assertEqual(d["unidades_vendidas"], cat["unidades"])
        self.assertEqual([f["id_categoria"] for f in d["por_categoria"]], [cat["id_categoria"]])
        # el envio no es atribuible a una categoria
        self.assertIsNone(d["total_facturado"])
        self.assertIsNone(d["total_envios"])

    def test_200_sin_datos(self):
        body = self.ok("ventas", **SIN_DATOS)
        d = body["data"]
        self.assertTrue(d["sin_datos"])
        self.assertEqual(d["mensaje"], MENSAJE)
        self.assertEqual(body["message"], MENSAJE)
        self.assertEqual((d["num_ventas"], d["unidades_vendidas"]), (0, 0))
        self.assertEqual((d["ingresos_productos"], d["ticket_promedio"]), (0, 0))  # sin division por cero
        for serie in ("por_fecha", "por_categoria", "por_canal", "por_metodo_pago"):
            self.assertEqual(d[serie], [], serie)

    def test_sin_datos_por_filtro_de_canal_no_es_error(self):
        # existe canal valido sin ventas en el rango -> 200 sin datos
        d = self.ok("ventas", canal_venta="POS", **CON_DATOS)["data"]
        if d["num_ventas"] == 0:
            self.assertTrue(d["sin_datos"])
            self.assertEqual(d["ticket_promedio"], 0)


class ProductosMasVendidosTest(ReportesEndpointsBase):
    def test_ranking_coincide_con_sql(self):
        d = self.ok("productos-mas-vendidos", top=3, **CON_DATOS)["data"]
        esperado = self.sql(
            """select p.id_producto from detalle_ventas d join ventas v using(id_venta)
               join productos p on p.id_producto=d.id_producto
               where v.estado_pago='PAGADO' and v.fecha_venta>=:a and v.fecha_venta<:b
               group by p.id_producto, p.nombre
               order by sum(d.cantidad) desc, sum(d.subtotal) desc, p.nombre limit 3""",
            **self.rango(),
        )
        self.assertEqual([i["id_producto"] for i in d["items"]], [r[0] for r in esperado])
        self.assertEqual([i["posicion"] for i in d["items"]], list(range(1, len(d["items"]) + 1)))
        cant = [i["cantidad_vendida"] for i in d["items"]]
        self.assertEqual(cant, sorted(cant, reverse=True))
        for i in d["items"]:
            self.assertIsInstance(i["total_generado"], (int, float))
            self.assertTrue(i["categoria"])

    def test_top_limita_y_suma_no_excede_total_vendido(self):
        ventas = self.ok("ventas", **CON_DATOS)["data"]
        d = self.ok("productos-mas-vendidos", top=1, **CON_DATOS)["data"]
        self.assertLessEqual(len(d["items"]), 1)
        todo = self.ok("productos-mas-vendidos", top=100, **CON_DATOS)["data"]
        self.assertEqual(sum(i["cantidad_vendida"] for i in todo["items"]), ventas["unidades_vendidas"])
        self.assertEqual(todo["total_productos_vendidos"], len(todo["items"]))

    def test_filtros_categoria_y_canal(self):
        items = self.ok("productos-mas-vendidos", **CON_DATOS)["data"]["items"]
        if not items:
            self.skipTest("sin ventas")
        cat = items[0]["id_categoria"]
        d = self.ok("productos-mas-vendidos", categoria_id=cat, canal_venta="ONLINE", **CON_DATOS)["data"]
        self.assertTrue(all(i["id_categoria"] == cat for i in d["items"]))

    def test_top_fuera_de_rango_422(self):
        self.assertEqual(self.get("productos-mas-vendidos", top=0).status_code, 422)
        self.assertEqual(self.get("productos-mas-vendidos", top=101).status_code, 422)

    def test_200_sin_datos(self):
        d = self.ok("productos-mas-vendidos", **SIN_DATOS)["data"]
        self.assertTrue(d["sin_datos"])
        self.assertEqual((d["items"], d["total_productos_vendidos"], d["mensaje"]), ([], 0, MENSAJE))


class InventarioTest(ReportesEndpointsBase):
    def test_kpis_y_niveles_coinciden_con_sql(self):
        d = self.ok("inventario", **CON_DATOS)["data"]
        n, stock, valor, agot = self.sql(
            """select count(*), coalesce(sum(stock_total),0), coalesce(sum(stock_total*precio_venta),0),
                      coalesce(sum(case when stock_total=0 then 1 else 0 end),0)
               from productos where estado<>'Inactivo'"""
        )[0]
        self.assertEqual((d["total_productos"], d["stock_total_unidades"], d["agotados"]), (n, stock, agot))
        self.assertAlmostEqual(d["valor_inventario"], float(valor), places=2)
        self.assertEqual(set(d["por_nivel"]), {"CRITICO", "BAJO", "OK"})
        self.assertEqual(sum(d["por_nivel"].values()), d["total_productos"])
        self.assertEqual(d["por_nivel"]["CRITICO"], self.sql(
            "select count(*) from productos where estado<>'Inactivo' and stock_total < :c",
            c=service.STOCK_CRITICO)[0][0])

    def test_filtro_por_nivel(self):
        base = self.ok("inventario", **CON_DATOS)["data"]
        for nivel in ("CRITICO", "BAJO", "OK"):
            d = self.ok("inventario", nivel_stock=nivel.lower(), **CON_DATOS)["data"]
            self.assertEqual(d["total_filas"], base["por_nivel"][nivel], nivel)
            self.assertTrue(all(i["nivel_stock"] == nivel for i in d["items"]))
            # los KPIs son del total, no del filtro
            self.assertEqual(d["total_productos"], base["total_productos"])

    def test_rotacion_aproximada_y_sin_division_por_cero(self):
        d = self.ok("inventario", **CON_DATOS)["data"]
        vendidas = dict(self.sql(
            """select d.id_producto, sum(d.cantidad) from detalle_ventas d join ventas v using(id_venta)
               where v.estado_pago='PAGADO' and v.fecha_venta>=:a and v.fecha_venta<:b group by 1""",
            **self.rango(),
        ))
        vistos_sin_stock = 0
        for i in d["items"]:
            v = int(vendidas.get(i["id_producto"], 0))
            self.assertEqual(i["unidades_vendidas"], v)
            if i["stock_actual"] > 0:
                self.assertAlmostEqual(i["rotacion"], round(v / i["stock_actual"], 2), places=2)
                self.assertTrue(i["rotacion_disponible"])
            else:
                vistos_sin_stock += 1
                self.assertIsNone(i["rotacion"])
                self.assertFalse(i["rotacion_disponible"])
            self.assertAlmostEqual(i["valor_stock"], i["stock_actual"] * i["precio_venta"], places=2)
        self.assertEqual(vistos_sin_stock, d["agotados"])

    def test_limite_recorta_filas_pero_no_totales(self):
        full = self.ok("inventario", **CON_DATOS)["data"]
        d = self.ok("inventario", limite=1, **CON_DATOS)["data"]
        self.assertEqual(len(d["items"]), min(1, full["total_filas"]))
        self.assertEqual(d["total_filas"], full["total_filas"])
        self.assertEqual(self.get("inventario", limite=0).status_code, 422)

    def test_orden_por_stock_ascendente(self):
        st = [i["stock_actual"] for i in self.ok("inventario", **CON_DATOS)["data"]["items"]]
        self.assertEqual(st, sorted(st))

    def test_nivel_invalido_422(self):
        self.assertEqual(self.get("inventario", nivel_stock="zzz").status_code, 422)

    def test_categoria_sin_productos_devuelve_sin_datos(self):
        libre = self.sql(
            """select c.id_categoria from categorias c
               where not exists (select 1 from productos p where p.id_categoria=c.id_categoria
                                 and p.estado<>'Inactivo') limit 1"""
        )
        if not libre:
            self.skipTest("todas las categorias tienen productos")
        d = self.ok("inventario", categoria_id=libre[0][0])["data"]
        self.assertTrue(d["sin_datos"])
        self.assertEqual((d["items"], d["total_productos"], d["valor_inventario"]), ([], 0, 0))
        self.assertEqual(d["por_nivel"], {"CRITICO": 0, "BAJO": 0, "OK": 0})


class DevolucionesTest(ReportesEndpointsBase):
    def test_totales_coinciden_con_sql(self):
        d = self.ok("devoluciones", **CON_DATOS)["data"]
        n, unid, imp, comp = self.sql(
            """select count(distinct dv.id_devolucion),
                 coalesce(sum(case when dv.estado<>'RECHAZADA' then l.cantidad_devuelta else 0 end),0),
                 coalesce(sum(case when dv.estado<>'RECHAZADA' then l.subtotal else 0 end),0),
                 coalesce(sum(case when dv.estado='COMPLETADA' then l.subtotal else 0 end),0)
               from detalle_devoluciones l join devoluciones dv using(id_devolucion)
               where dv.fecha_solicitud>=:a and dv.fecha_solicitud<:b""",
            **self.rango(),
        )[0]
        self.assertEqual((d["num_devoluciones"], d["unidades_devueltas"]), (n, unid))
        self.assertAlmostEqual(d["importe_total"], float(imp), places=2)
        self.assertAlmostEqual(d["importe_completado"], float(comp), places=2)
        # RECHAZADA cuenta en conteos/por_estado pero no en unidades/importe
        self.assertEqual(sum(e["num_devoluciones"] for e in d["por_estado"]), d["num_devoluciones"])
        self.assertAlmostEqual(sum(f["importe"] for f in d["por_fecha"]), d["importe_total"], places=2)
        self.assertAlmostEqual(sum(f["importe"] for f in d["por_categoria"]), d["importe_total"], places=2)
        self.assertEqual(len(d["por_fecha"]), 365)
        self.assertEqual(len(d["detalle"]), min(d["num_devoluciones"], 200))

    def test_filtro_estado(self):
        for estado in ("completada", "RECHAZADA", "SOLICITADA"):
            d = self.ok("devoluciones", estado=estado, **CON_DATOS)["data"]
            n = self.sql(
                "select count(*) from devoluciones where estado=:e and fecha_solicitud>=:a and fecha_solicitud<:b",
                e=estado.upper(), **self.rango())[0][0]
            self.assertEqual(d["num_devoluciones"], n, estado)
            self.assertTrue(all(x["estado"] == estado.upper() for x in d["detalle"]))
            if estado.upper() == "RECHAZADA":  # solo rechazadas: no hay reembolso
                self.assertEqual((d["unidades_devueltas"], d["importe_total"]), (0, 0))

    def test_estado_y_canal_invalidos_422(self):
        self.assertEqual(self.get("devoluciones", estado="INVENTADO").status_code, 422)
        self.assertEqual(self.get("devoluciones", canal_venta="TIENDA").status_code, 422)

    def test_200_sin_datos(self):
        body = self.ok("devoluciones", **SIN_DATOS)
        d = body["data"]
        self.assertTrue(d["sin_datos"])
        self.assertEqual((d["num_devoluciones"], d["importe_total"], d["mensaje"]), (0, 0, MENSAJE))
        self.assertEqual(body["message"], MENSAJE)
        for serie in ("por_estado", "por_fecha", "por_producto", "por_categoria", "detalle"):
            self.assertEqual(d[serie], [], serie)


class FiltrosPermisosYRangoTest(ReportesEndpointsBase):
    def test_rango_invertido_422_en_todos(self):
        for ep in ENDPOINTS:
            r = self.get(ep, fecha_inicio="2026-10-01", fecha_fin="2026-09-01")
            self.assertEqual(r.status_code, 422, ep)
            self.assertIn("fecha_inicio", r.json()["detail"])

    def test_mismo_dia_es_valido(self):
        for ep in ENDPOINTS:
            self.ok(ep, fecha_inicio="2026-09-17", fecha_fin="2026-09-17")

    def test_categoria_inexistente_o_invalida_422(self):
        for ep in ENDPOINTS:
            self.assertEqual(self.get(ep, categoria_id=99999999).status_code, 422, ep)
            self.assertEqual(self.get(ep, categoria_id=0).status_code, 422, ep)
            self.assertEqual(self.get(ep, categoria_id="abc").status_code, 422, ep)

    def test_canal_invalido_422(self):
        for ep in ENDPOINTS:
            self.assertEqual(self.get(ep, canal_venta="TIENDA").status_code, 422, ep)

    def test_fecha_con_formato_invalido_422(self):
        for ep in ENDPOINTS:
            self.assertEqual(self.get(ep, fecha_inicio="ayer").status_code, 422, ep)

    def test_rango_por_defecto_30_dias_utc(self):
        for ep in ENDPOINTS:
            f = self.ok(ep)["data"]["filtros"]
            ini, fin = date.fromisoformat(f["fecha_inicio"]), date.fromisoformat(f["fecha_fin"])
            self.assertEqual(fin, service.hoy_utc(), ep)
            self.assertEqual((fin - ini).days, 29, ep)

    def test_solo_una_fecha_completa_la_otra(self):
        f = self.ok("ventas", fecha_fin="2026-03-10")["data"]["filtros"]
        self.assertEqual(f["fecha_inicio"], "2026-02-09")  # cruza de mes
        f = self.ok("ventas", fecha_fin="2026-01-15")["data"]["filtros"]
        self.assertEqual(f["fecha_inicio"], "2025-12-17")  # cruza de anio

    def test_permisos_asu_gs_si_v_c_no(self):
        for ep in ENDPOINTS:
            for rol in ("ASU", "GS"):
                self.assertEqual(self.get(ep, rol=rol).status_code, 200, (ep, rol))
            for rol in ("V", "C", "D"):
                self.assertEqual(self.get(ep, rol=rol).status_code, 403, (ep, rol))

    def test_403_antes_que_validar_filtros(self):
        r = self.get("ventas", rol="V", fecha_inicio="2026-10-01", fecha_fin="2026-09-01")
        self.assertEqual(r.status_code, 403)

    def test_sin_token_401(self):
        app.dependency_overrides.pop(get_current_user, None)
        for ep in ENDPOINTS:
            self.assertEqual(self.client.get(f"{BASE}/{ep}").status_code, 401, ep)

    def test_reportes_no_modifican_datos(self):
        # La sesion es READ ONLY: cualquier escritura fallaria. Ademas los
        # conteos no cambian entre dos consultas.
        antes = self.sql("select (select count(*) from ventas), (select count(*) from productos), "
                         "(select sum(stock_total) from productos), (select count(*) from devoluciones)")
        for ep in ENDPOINTS:
            self.ok(ep, **CON_DATOS)
        despues = self.sql("select (select count(*) from ventas), (select count(*) from productos), "
                           "(select sum(stock_total) from productos), (select count(*) from devoluciones)")
        self.assertEqual(antes, despues)


if __name__ == "__main__":
    unittest.main()
