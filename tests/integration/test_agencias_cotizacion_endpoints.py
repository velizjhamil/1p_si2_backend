# Integracion CU19 fase 8 - COMPORTAMIENTO de GET /api/v1/agencias-reparto/{id}/cotizacion
# sobre la BD LOCAL de pruebas (jamas Supabase ni Render), con JWT reales. La
# matriz de roles vive en test_agencias_cotizacion_authz.py.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_cotizacion_endpoints.py
#
# REGLA DE SELECCION CONGELADA: mayor costo -> (empate) PESO antes que VOLUMEN ->
# (empate) zona de ciudad completa antes que subzona -> (empate) 409. Sin ids.
# Rangos [min, max) (max null = abierto); vigencia [desde, hasta] en UTC.
# Solo lectura: nada se persiste.
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import event, text  # noqa: E402

from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

LA_PAZ, COCHABAMBA = 2, 3
CAMPOS = {"agencia", "ciudad", "peso_kg", "volumen_m3", "fecha_referencia", "criterio", "costo_agencia",
          "tarifa", "candidatas"}
CAMPOS_TARIFA = {"id_tarifa", "id_zona", "nombre_zona", "criterio", "rango_min", "rango_max", "costo",
                 "vigente_desde", "vigente_hasta"}
PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal")
TABLAS = ("agencias_reparto", "agencia_zonas", "agencia_tarifas", "envios", "envio_historial", "ventas", "ciudades")


def hoy():
    return datetime.now(timezone.utc).date()


def dia(delta):
    return (hoy() + timedelta(days=delta)).isoformat()


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class CotizacionEndpoints(BaseAgenciasAPI):
    n = 0

    # -- helpers ---------------------------------------------------------------------
    def agencia(self, nombre="Andes Express") -> int:
        CotizacionEndpoints.n += 1
        return self.crear("GS", razon_social=nombre, nit=f"8{CotizacionEndpoints.n:08d}")["id_agencia"]

    def zona(self, ag, id_ciudad=LA_PAZ, subzona=None) -> int:
        cuerpo = {"id_ciudad": id_ciudad, **({"nombre_zona": subzona} if subzona else {})}
        r = self.req("POST", f"/{ag}/zonas", "GS", json=cuerpo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id_zona"]

    def tarifa(self, ag, z, criterio="PESO", rmin="0", rmax="5", costo="10", desde="2020-01-01", hasta=None, activa=True):
        cuerpo = {"criterio": criterio, "rango_min": rmin, "rango_max": rmax, "costo": costo,
                  "vigente_desde": desde, "vigente_hasta": hasta, "is_active": activa}
        r = self.req("POST", f"/{ag}/zonas/{z}/tarifas", "GS", json=cuerpo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id_tarifa"]

    def cot(self, ag, ciudad="La Paz", peso="2", vol="1", rol="GS"):
        return self.req("GET", f"/{ag}/cotizacion", rol, params={"ciudad": ciudad, "peso_kg": peso, "volumen_m3": vol})

    def ok(self, ag, **kw) -> dict:
        r = self.cot(ag, **kw)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def ciudad_sql(self, nombre, departamento="X") -> int:
        i = self.db.execute(text("insert into ciudades (nombre, departamento) values (:n,:d) returning id"),
                            {"n": nombre, "d": departamento}).scalar()
        self.db.commit()
        return i

    def simple(self, **tarifa_kw):
        """Agencia con una zona de ciudad completa en La Paz y una tarifa."""
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, **tarifa_kw)
        return ag, z

    # -- 1-3: PESO, VOLUMEN y ambas -------------------------------------------------------
    def test_tarifa_peso(self):
        ag, z = self.simple(criterio="PESO", rmin="0", rmax="5", costo="10.50")
        d = self.ok(ag, peso="2", vol="99")
        self.assertEqual(set(d), CAMPOS)
        self.assertEqual((d["criterio"], d["costo_agencia"]), ("PESO", 10.5))
        self.assertEqual(set(d["tarifa"]), CAMPOS_TARIFA)
        self.assertEqual((d["tarifa"]["id_zona"], d["tarifa"]["nombre_zona"], d["tarifa"]["criterio"]), (z, None, "PESO"))
        self.assertEqual((d["tarifa"]["rango_min"], d["tarifa"]["rango_max"], d["tarifa"]["costo"]), (0.0, 5.0, 10.5))
        self.assertEqual((d["tarifa"]["vigente_desde"], d["tarifa"]["vigente_hasta"]), ("2020-01-01", None))

    def test_tarifa_volumen(self):
        ag, _ = self.simple(criterio="VOLUMEN", rmin="0", rmax="3", costo="22")
        d = self.ok(ag, peso="99", vol="1.5")
        self.assertEqual((d["criterio"], d["costo_agencia"]), ("VOLUMEN", 22.0))

    def test_respuesta_completa(self):
        ag, _ = self.simple()
        r = self.cot(ag, peso="2.5", vol="0.75")
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message"})
        d = b["data"]
        self.assertEqual(d["agencia"], {"id_agencia": ag, "razon_social": "Andes Express"})
        self.assertEqual(d["ciudad"], {"id_ciudad": LA_PAZ, "nombre": "La Paz", "departamento": "La Paz"})
        self.assertEqual((d["peso_kg"], d["volumen_m3"], d["fecha_referencia"]), (2.5, 0.75, hoy().isoformat()))
        self.assertIn("La Paz", b["message"])
        self.assertIn("PESO", b["message"])
        for campo in PRIVADOS:
            self.assertNotIn(f'"{campo}"', r.text)

    def test_ambas_aplican_gana_el_mayor_costo(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", costo="10")
        self.tarifa(ag, z, "VOLUMEN", costo="25")
        d = self.ok(ag)
        self.assertEqual((d["criterio"], d["costo_agencia"]), ("VOLUMEN", 25.0))
        self.assertEqual([(c["criterio"], c["costo"], c["seleccionada"]) for c in d["candidatas"]],
                         [("VOLUMEN", 25.0, True), ("PESO", 10.0, False)])       # la regla queda visible
        ag2 = self.agencia("Otra Agencia")
        z2 = self.zona(ag2)
        self.tarifa(ag2, z2, "PESO", costo="40")
        self.tarifa(ag2, z2, "VOLUMEN", costo="25")
        d2 = self.ok(ag2)
        self.assertEqual((d2["criterio"], d2["costo_agencia"]), ("PESO", 40.0))

    def test_regla_de_seleccion_explicita_empate_de_costo_gana_peso(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "VOLUMEN", costo="15")
        self.tarifa(ag, z, "PESO", costo="15.00")
        d = self.ok(ag)
        self.assertEqual((d["criterio"], d["costo_agencia"]), ("PESO", 15.0))
        self.assertEqual([c["criterio"] for c in d["candidatas"]], ["PESO", "VOLUMEN"])

    # -- 5-7: una sola / ninguna --------------------------------------------------------------
    def test_solo_peso_aplica_aunque_haya_tarifa_de_volumen_fuera_de_rango(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", "0", "5", "10")
        self.tarifa(ag, z, "VOLUMEN", "5", "9", "99")
        d = self.ok(ag, peso="2", vol="1")
        self.assertEqual((d["criterio"], d["costo_agencia"], len(d["candidatas"])), ("PESO", 10.0, 1))

    def test_solo_volumen_aplica(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", "10", "20", "99")
        self.tarifa(ag, z, "VOLUMEN", "0", "5", "8")
        d = self.ok(ag, peso="2", vol="1")
        self.assertEqual((d["criterio"], d["costo_agencia"], len(d["candidatas"])), ("VOLUMEN", 8.0, 1))

    def test_agencia_con_tarifas_de_una_sola_dimension_cotiza_sin_exigir_la_otra(self):
        ag, _ = self.simple(criterio="PESO")
        self.assertEqual(self.cot(ag).status_code, 200)

    def test_ninguna_tarifa_aplicable_404_con_mensaje_propio(self):
        ag, _ = self.simple(criterio="PESO", rmin="0", rmax="5")
        r = self.cot(ag, peso="50", vol="50")
        self.assertEqual(r.status_code, 404, r.text)
        detalle = r.json()["detail"]
        self.assertIn("no existe tarifa aplicable", detalle)
        self.assertIn("peso 50 kg", detalle)
        self.assertIn("volumen 50 m3", detalle)
        self.assertNotIn("no tiene cobertura", detalle)                          # distinto al de cobertura
        sin_tarifas = self.agencia("Sin Tarifas")
        self.zona(sin_tarifas)
        r2 = self.cot(sin_tarifas)
        self.assertEqual(r2.status_code, 404)
        self.assertIn("no existe tarifa aplicable", r2.json()["detail"])

    # -- 8-12: cobertura, ciudad, agencia -------------------------------------------------------
    def test_agencia_sin_cobertura_404_de_cobertura(self):
        ag = self.agencia()
        z = self.zona(ag, COCHABAMBA)
        self.tarifa(ag, z)
        r = self.cot(ag, "La Paz")
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no tiene cobertura", r.json()["detail"])
        self.assertNotIn("tarifa aplicable", r.json()["detail"])
        self.assertEqual(self.cot(ag, "Cochabamba").status_code, 200)
        sin_zonas = self.agencia("Sin Zonas")
        self.assertIn("no tiene cobertura", self.cot(sin_zonas).json()["detail"])

    def test_ciudad_inexistente_404_y_no_aproxima(self):
        ag, _ = self.simple()
        r = self.cot(ag, "Atlantida")
        self.assertEqual(r.status_code, 404)
        self.assertIn("Atlantida", r.json()["detail"])
        for parecido in ("Paz", "La Pa", "La Paz Norte"):
            self.assertEqual(self.cot(ag, parecido).status_code, 404, parecido)

    def test_ciudad_ambigua_409(self):
        ag, _ = self.simple()
        self.ciudad_sql("LA  PAZ", "Otro Departamento")
        r = self.cot(ag, "la paz")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ambiguo", r.json()["detail"])
        self.assertNotIn("data", r.json())
        self.assertEqual(self.cot(ag, "  LA   PAZ ").status_code, 409)

    def test_nombre_de_ciudad_normalizado(self):
        nueva = self.ciudad_sql("Ñandú Ciudad")
        ag = self.agencia()
        z = self.zona(ag, nueva)
        self.tarifa(ag, z)
        for nombre in ("ñandú ciudad", "NANDU CIUDAD", "  Nandu    Ciudad "):
            with self.subTest(nombre):
                self.assertEqual(self.ok(ag, ciudad=nombre)["ciudad"]["id_ciudad"], nueva)

    def test_agencia_inexistente_404(self):
        r = self.cot(999999)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("agencia", r.json()["detail"])
        self.assertEqual(self.req("GET", "/abc/cotizacion", "GS",
                                  params={"ciudad": "La Paz", "peso_kg": "1", "volumen_m3": "1"}).status_code, 422)

    def test_agencia_deshabilitada_ningun_rol_cotiza(self):
        ag, _ = self.simple()
        self.assertEqual(self.cot(ag).status_code, 200)
        self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": False})
        for rol, codigo in (("GS", 400), ("ASU", 400), ("D", 404)):        # D no ve deshabilitadas (404), ASU/GS: operacion invalida
            r = self.cot(ag, rol=rol)
            self.assertEqual(r.status_code, codigo, (rol, r.text))
            self.assertNotIn("data", r.json())
        self.assertIn("deshabilitada", self.cot(ag, rol="GS").json()["detail"])
        self.assertEqual(self.req("GET", f"/{ag}/zonas", "GS").status_code, 200)     # sus datos historicos siguen consultables
        self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": True})
        self.assertEqual(self.cot(ag).status_code, 200)

    # -- 13-15: estado y vigencia -------------------------------------------------------------------
    def test_tarifa_inactiva_no_se_usa(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, costo="99", activa=False)
        self.assertEqual(self.cot(ag).status_code, 404)
        self.tarifa(ag, z, costo="10")                                       # la inactiva no bloquea ni cotiza
        self.assertEqual(self.ok(ag)["costo_agencia"], 10.0)

    def test_tarifa_expirada_y_futura_no_se_usan(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, costo="90", desde="2020-01-01", hasta=dia(-1))    # expiro ayer
        self.tarifa(ag, z, costo="91", desde=dia(1), hasta=None)             # empieza manana
        r = self.cot(ag)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no existe tarifa aplicable", r.json()["detail"])
        self.tarifa(ag, z, "PESO", "5", "9", "12", desde=dia(-3), hasta=dia(3))   # vigente hoy (otro tramo)
        self.assertEqual(self.ok(ag, peso="6")["costo_agencia"], 12.0)

    def test_vigencia_incluye_hoy_en_ambos_extremos(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, costo="10", desde=dia(0), hasta=dia(0))
        self.assertEqual(self.ok(ag)["costo_agencia"], 10.0)

    def test_expirada_y_futura_conviven_con_la_vigente_y_gana_la_vigente(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, costo="500", desde="2019-01-01", hasta="2019-12-31")
        self.tarifa(ag, z, costo="7", desde="2020-01-01", hasta=None)
        d = self.ok(ag)
        self.assertEqual(d["costo_agencia"], 7.0)                               # la mayor NO gana si no esta vigente
        self.assertEqual(len(d["candidatas"]), 1)

    # -- 16-18: limites [min, max) ------------------------------------------------------------------
    def test_limite_inferior_incluido_y_superior_excluido(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", "5", "10", "20")
        self.assertEqual(self.ok(ag, peso="5")["costo_agencia"], 20.0)           # minimo INCLUIDO
        self.assertEqual(self.ok(ag, peso="9.999")["costo_agencia"], 20.0)
        for peso in ("4.999", "10", "10.001"):                                   # maximo EXCLUIDO
            with self.subTest(peso=peso):
                self.assertEqual(self.cot(ag, peso=peso).status_code, 404, peso)

    def test_tramos_contiguos_la_frontera_pertenece_al_de_arriba(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", "0", "5", "10")
        self.tarifa(ag, z, "PESO", "5", "10", "20")
        self.assertEqual(self.ok(ag, peso="4.999")["costo_agencia"], 10.0)
        self.assertEqual(self.ok(ag, peso="5")["costo_agencia"], 20.0)
        self.assertEqual(self.ok(ag, peso="0.001")["costo_agencia"], 10.0)

    def test_rango_abierto(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "VOLUMEN", "2", None, "30")
        self.assertEqual(self.ok(ag, vol="2")["costo_agencia"], 30.0)
        self.assertEqual(self.ok(ag, vol="9999999.999")["costo_agencia"], 30.0)
        self.assertEqual(self.cot(ag, vol="1.999").status_code, 404)
        self.assertIsNone(self.ok(ag, vol="50")["tarifa"]["rango_max"])

    # -- 19-22: dimensiones ---------------------------------------------------------------------------
    def test_dimensiones_invalidas_422(self):
        ag, _ = self.simple()
        invalidas = [("peso_kg", v) for v in ("0", "0.000", "-1", "-0.001", "NaN", "nan", "Infinity", "-Infinity", "inf",
                                              "abc", "", "1,5", "1.2345", "10000000", "1e400")]
        invalidas += [("volumen_m3", v) for v in ("0", "-2", "NaN", "Infinity", "abc", "0.0001", "12345678")]
        for campo, valor in invalidas:
            with self.subTest(campo=campo, valor=valor):
                params = {"ciudad": "La Paz", "peso_kg": "1", "volumen_m3": "1", campo: valor}
                r = self.req("GET", f"/{ag}/cotizacion", "GS", params=params)
                self.assertEqual(r.status_code, 422, (campo, valor, r.text))
                self.assertNotIn("data", r.json())

    def test_precision_valida_se_acepta(self):
        ag, _ = self.simple(rmax=None)
        for peso in ("0.001", "2.5", "2.500", "5.0000", "9999999.999", "1e-3"):
            with self.subTest(peso=peso):
                self.assertEqual(self.cot(ag, peso=peso).status_code, 200, peso)

    def test_parametros_faltantes_o_ciudad_vacia_422(self):
        ag, _ = self.simple()
        base = {"ciudad": "La Paz", "peso_kg": "1", "volumen_m3": "1"}
        for falta in base:
            params = {k: v for k, v in base.items() if k != falta}
            with self.subTest(falta=falta):
                self.assertEqual(self.req("GET", f"/{ag}/cotizacion", "GS", params=params).status_code, 422)
        for vacio in ("", "   "):
            self.assertEqual(self.cot(ag, ciudad=vacio).status_code, 422, repr(vacio))
        self.assertEqual(self.cot(ag, ciudad="x" * 101).status_code, 422)

    def test_las_dimensiones_se_devuelven_tal_cual(self):
        ag, _ = self.simple(rmax=None)
        d = self.ok(ag, peso="3.125", vol="0.004")
        self.assertEqual((Decimal(str(d["peso_kg"])), Decimal(str(d["volumen_m3"]))), (Decimal("3.125"), Decimal("0.004")))

    # -- 23-24: zonas y subzonas ------------------------------------------------------------------------
    def test_zona_con_solo_subzona_cuenta_como_cobertura(self):
        ag = self.agencia()
        z = self.zona(ag, LA_PAZ, "Sopocachi")
        self.tarifa(ag, z, costo="12")
        d = self.ok(ag)
        self.assertEqual((d["costo_agencia"], d["tarifa"]["nombre_zona"]), (12.0, "Sopocachi"))

    def test_varias_zonas_de_la_misma_ciudad_mayor_costo(self):
        ag = self.agencia()
        completa, sopo = self.zona(ag), self.zona(ag, LA_PAZ, "Sopocachi")
        self.tarifa(ag, completa, costo="10")
        self.tarifa(ag, sopo, costo="18")
        d = self.ok(ag)
        self.assertEqual((d["costo_agencia"], d["tarifa"]["nombre_zona"], d["tarifa"]["id_zona"]), (18.0, "Sopocachi", sopo))
        self.assertEqual([(c["nombre_zona"], c["costo"], c["seleccionada"]) for c in d["candidatas"]],
                         [("Sopocachi", 18.0, True), (None, 10.0, False)])

    def test_empate_de_costo_entre_zonas_prefiere_peso_y_luego_ciudad_completa(self):
        ag = self.agencia()
        completa, sopo = self.zona(ag), self.zona(ag, LA_PAZ, "Sopocachi")
        self.tarifa(ag, sopo, "PESO", costo="15")
        self.tarifa(ag, completa, "VOLUMEN", costo="15")
        d = self.ok(ag)
        self.assertEqual((d["criterio"], d["tarifa"]["nombre_zona"]), ("PESO", "Sopocachi"))     # criterio antes que tipo de zona
        ag2 = self.agencia("Segunda")
        completa2, sopo2 = self.zona(ag2), self.zona(ag2, LA_PAZ, "Sopocachi")
        self.tarifa(ag2, sopo2, "PESO", costo="15")
        self.tarifa(ag2, completa2, "PESO", costo="15")
        d2 = self.ok(ag2)
        self.assertIsNone(d2["tarifa"]["nombre_zona"])                                          # ciudad completa
        self.assertEqual(d2["tarifa"]["id_zona"], completa2)

    def test_ambiguedad_final_409_dos_subzonas_equivalentes(self):
        ag = self.agencia()
        a, b = self.zona(ag, LA_PAZ, "Sopocachi"), self.zona(ag, LA_PAZ, "Miraflores")
        self.tarifa(ag, a, "PESO", costo="15")
        self.tarifa(ag, b, "PESO", costo="15")
        r = self.cot(ag)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("equivalentes", r.json()["detail"])
        self.assertIn("Sopocachi", r.json()["detail"])
        self.assertIn("Miraflores", r.json()["detail"])
        self.assertNotIn("data", r.json())
        c = self.zona(ag, LA_PAZ, "Calacoto")                                                    # una tercera, mas cara: se rompe el empate
        self.tarifa(ag, c, "PESO", costo="16")
        self.assertEqual(self.ok(ag)["tarifa"]["nombre_zona"], "Calacoto")

    def test_el_resultado_no_depende_del_orden_de_creacion_ni_de_los_ids(self):
        resultados = []
        for orden in ((0, 1), (1, 0)):
            ag = self.agencia(f"Agencia {orden}")
            zonas = [self.zona(ag), self.zona(ag, LA_PAZ, "Sur")]
            for i in orden:                                                                     # ids de tarifa en orden distinto
                self.tarifa(ag, zonas[i], "PESO", costo="15")
            resultados.append(self.ok(ag)["tarifa"]["nombre_zona"])
        self.assertEqual(resultados, [None, None])

    def test_solo_cuentan_zonas_de_la_ciudad_consultada(self):
        ag = self.agencia()
        lp, cb = self.zona(ag, LA_PAZ), self.zona(ag, COCHABAMBA)
        self.tarifa(ag, lp, costo="10")
        self.tarifa(ag, cb, costo="99")
        self.assertEqual(self.ok(ag, ciudad="La Paz")["costo_agencia"], 10.0)
        self.assertEqual(self.ok(ag, ciudad="Cochabamba")["costo_agencia"], 99.0)

    def test_solo_cuentan_tarifas_de_la_propia_agencia(self):
        a, _ = self.simple(costo="10")
        b = self.agencia("Otra")
        zb = self.zona(b)
        self.tarifa(b, zb, costo="500")
        self.assertEqual(self.ok(a)["costo_agencia"], 10.0)
        self.assertEqual(self.ok(b)["costo_agencia"], 500.0)

    # -- 25: no persiste nada --------------------------------------------------------------------------------
    def instantanea(self):
        return {t: self.db.execute(text(f"select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {t} t")).scalar()
                for t in TABLAS}

    def test_no_se_persiste_nada(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", costo="10")
        self.tarifa(ag, z, "VOLUMEN", costo="25")
        antes = self.instantanea()
        conteos = {t: self.db.execute(text(f"select count(*) from {t}")).scalar() for t in TABLAS}
        for _ in range(3):
            self.ok(ag)                                              # cotizaciones exitosas
            self.ok(ag, rol="D")
            self.cot(ag, peso="500")                                 # 404 sin tarifa
            self.cot(ag, ciudad="Cochabamba")                        # 404 sin cobertura
            self.cot(ag, peso="0")                                   # 422
        self.assertEqual(self.instantanea(), antes)                  # ninguna tabla cambio, ni una fila ni un byte
        self.assertEqual({t: self.db.execute(text(f"select count(*) from {t}")).scalar() for t in TABLAS}, conteos)
        self.assertEqual(self.db.execute(text("select count(*) from envios where id_agencia is not null")).scalar(), 0)

    def test_no_toca_costo_de_venta_ni_asigna_agencia(self):
        ag, _ = self.simple()
        costos = self.db.execute(text("select coalesce(sum(costo_envio),0), count(*) from ventas")).one()
        self.ok(ag)
        self.assertEqual(tuple(self.db.execute(text("select coalesce(sum(costo_envio),0), count(*) from ventas")).one()), tuple(costos))
        self.assertEqual(self.db.execute(text("select count(*) from envios where id_agencia is not null or costo_agencia is not null "
                                              "or peso_kg is not null or volumen_m3 is not null or id_tarifa_aplicada is not null")).scalar(), 0)

    def test_fecha_actualizacion_de_las_tarifas_no_cambia(self):
        ag, z = self.simple()
        antes = self.db.execute(text("select array_agg(fecha_actualizacion order by id_tarifa) from agencia_tarifas where id_zona=:z"), {"z": z}).scalar()
        self.ok(ag)
        despues = self.db.execute(text("select array_agg(fecha_actualizacion order by id_tarifa) from agencia_tarifas where id_zona=:z"), {"z": z}).scalar()
        self.assertEqual(antes, despues)

    # -- eficiencia (N+1) --------------------------------------------------------------------------------------
    def sentencias(self, ag) -> list[str]:
        vistas: list[str] = []

        def contar(conn, cursor, statement, parameters, context, executemany):
            vistas.append(statement)

        self.cot(ag, rol="D")                                          # calentamiento (objetos expirados tras commits del arnes)
        event.listen(self.conn, "before_cursor_execute", contar)
        try:
            r = self.cot(ag, rol="D")
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            event.remove(self.conn, "before_cursor_execute", contar)
        return vistas

    def test_no_hay_n_mas_uno_las_sentencias_no_crecen_con_zonas_y_tarifas(self):
        ag = self.agencia()
        z0 = self.zona(ag)
        self.tarifa(ag, z0, "PESO", "0", "5", "10")
        pocas = self.sentencias(ag)
        for i in range(1, 25):                                         # 24 zonas mas, cada una con tarifas
            z = self.zona(ag, LA_PAZ, f"Zona {i:02d}")
            self.tarifa(ag, z, "PESO", "0", "5", str(10 + i))
            self.tarifa(ag, z, "VOLUMEN", "0", "5", str(5 + i))
            self.tarifa(ag, self.zona(ag, COCHABAMBA, f"Otra {i:02d}"), "PESO", "0", "5", "1")
        muchas = self.sentencias(ag)
        self.assertEqual(len(pocas), len(muchas), f"crece con las zonas/tarifas: {len(pocas)} -> {len(muchas)}")
        self.assertLessEqual(len(muchas), 9, muchas)
        self.assertEqual(len([s for s in muchas if "FROM agencia_zonas" in s]), 1, muchas)           # UNA consulta de zonas+tarifas
        self.assertEqual(len([s for s in muchas if "FROM agencia_tarifas" in s]), 0, muchas)         # tarifas via el outer join, no aparte
        self.assertTrue(any("LEFT OUTER JOIN agencia_tarifas" in s for s in muchas))
        self.assertEqual(len([s for s in muchas if "FROM agencias_reparto" in s]), 1, muchas)        # una consulta liviana de agencia
        # y sigue eligiendo bien con 25 zonas: la de mayor costo (subzona 24, PESO 34 vs VOLUMEN 29)
        d = self.ok(ag, rol="D")
        self.assertEqual((d["criterio"], d["costo_agencia"], d["tarifa"]["nombre_zona"]), ("PESO", 34.0, "Zona 24"))

    # -- ruta ----------------------------------------------------------------------------------------------------
    def test_la_ruta_no_choca_con_el_detalle_ni_con_estado(self):
        ag, _ = self.simple()
        self.assertEqual(self.cot(ag).status_code, 200)
        self.assertEqual(self.req("GET", f"/{ag}", "GS").status_code, 200)
        self.assertEqual(self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": True}).status_code, 200)
        self.assertEqual(self.req("GET", "/disponibles", "GS", params={"ciudad": "La Paz"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
