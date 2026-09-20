# Integracion CU19 fase 6 - COMPORTAMIENTO de las tarifas de zona sobre la BD
# LOCAL de pruebas (jamas Supabase ni Render), con JWT reales. La matriz de roles
# vive en test_agencias_tarifas_authz.py.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_tarifas_endpoints.py
#
# Semantica verificada:
#   rango     [min, max)      max null = tramo abierto; [0,5) y [5,10) son contiguos
#   vigencia  [desde, hasta]  hasta null = sin fin; dias consecutivos no se solapan
#   solapa    misma zona + mismo criterio + rangos que se cruzan + vigencias que se
#             cruzan (solo tarifas ACTIVAS); PESO y VOLUMEN nunca se bloquean.
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import text  # noqa: E402

from app.modules.delivery import tarifas_service as svc  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

CAMPOS = {"id_tarifa", "id_zona", "id_agencia", "criterio", "rango_min", "rango_max", "costo", "vigente_desde",
          "vigente_hasta", "is_active", "vigente", "fecha_creacion", "fecha_actualizacion"}
PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal", "razon_social")
LA_PAZ, COCHABAMBA = 2, 3


def cuerpo(**extra):
    base = {"criterio": "PESO", "rango_min": "0", "rango_max": "5", "costo": "10.50", "vigente_desde": "2026-01-01"}
    base.update(extra)
    return base


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class TarifasEndpoints(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        self.zona = self.req("POST", f"/{self.ag}/zonas", "GS", json={"id_ciudad": LA_PAZ}).json()["data"]["id_zona"]
        self.t = f"/{self.ag}/zonas/{self.zona}/tarifas"

    # -- helpers ------------------------------------------------------------------
    def tarifa(self, rol="GS", ruta=None, **extra):
        r = self.req("POST", ruta or self.t, rol, json=cuerpo(**extra))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def post(self, **extra):
        return self.req("POST", self.t, "GS", json=cuerpo(**extra))

    def cuenta(self, sql, **p):
        return self.db.execute(text(sql), p).scalar()

    def otra_zona(self, id_ciudad=COCHABAMBA, agencia=None):
        ag = agencia or self.ag
        return self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": id_ciudad}).json()["data"]["id_zona"]

    # -- crear ---------------------------------------------------------------------
    def test_crear_peso_201_envelope_y_campos(self):
        r = self.req("POST", self.t, "GS", json=cuerpo())
        self.assertEqual(r.status_code, 201, r.text)
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message"})
        d = b["data"]
        self.assertEqual(set(d), CAMPOS)
        self.assertEqual((d["id_zona"], d["id_agencia"], d["criterio"], d["rango_min"], d["rango_max"], d["costo"]),
                         (self.zona, self.ag, "PESO", 0.0, 5.0, 10.5))
        self.assertEqual((d["vigente_desde"], d["vigente_hasta"], d["is_active"]), ("2026-01-01", None, True))

    def test_crear_volumen_y_criterio_en_minusculas(self):
        d = self.tarifa(criterio="volumen", rango_max="0.5")
        self.assertEqual(d["criterio"], "VOLUMEN")

    def test_crear_asu_y_barra_final(self):
        self.assertEqual(self.req("POST", self.t + "/", "ASU", json=cuerpo()).status_code, 201)

    def test_rango_abierto_y_vigencia_abierta(self):
        d = self.tarifa(rango_min="5", rango_max=None, vigente_hasta=None)
        self.assertIsNone(d["rango_max"])
        self.assertIsNone(d["vigente_hasta"])
        d = self.tarifa(rango_min="0", rango_max="5")  # el tramo abierto no choca con [0,5)
        self.assertEqual(d["rango_max"], 5.0)

    def test_costo_cero_valido(self):
        self.assertEqual(self.tarifa(costo="0")["costo"], 0.0)

    def test_vigente_hoy_expirada_y_futura(self):
        hoy = date.today()
        vigente = self.tarifa(rango_min="0", rango_max="1", vigente_desde=(hoy - timedelta(days=5)).isoformat())
        expirada = self.tarifa(rango_min="1", rango_max="2", vigente_desde="2020-01-01", vigente_hasta="2020-12-31")
        futura = self.tarifa(rango_min="2", rango_max="3", vigente_desde=(hoy + timedelta(days=30)).isoformat())
        inactiva = self.tarifa(rango_min="3", rango_max="4", is_active=False)
        self.assertEqual([vigente["vigente"], expirada["vigente"], futura["vigente"], inactiva["vigente"]],
                         [True, False, False, False])

    # -- validaciones de forma y negocio ------------------------------------------------
    def test_criterio_invalido_422(self):
        for c in ("PESADO", "", "PESO+VOLUMEN", "COMBINADO", None):
            with self.subTest(c=c):
                self.assertEqual(self.post(criterio=c).status_code, 422)

    def test_rango_invalido_422(self):
        for extra in ({"rango_min": "-1"}, {"rango_max": "0"}, {"rango_max": "-2"}, {"rango_min": "5", "rango_max": "5"},
                      {"rango_min": "8", "rango_max": "3"}, {"rango_min": "abc"}, {"rango_min": "1.2345"},
                      {"rango_min": "10000000"}, {"rango_max": "Infinity"}):
            with self.subTest(extra):
                r = self.post(**extra)
                self.assertEqual(r.status_code, 422, (extra, r.text))
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 0)  # nada se guardo

    def test_costo_invalido_422(self):
        for costo in ("-1", "-0.01", "abc", "NaN", "10.123", "100000000", None):
            with self.subTest(costo=costo):
                self.assertEqual(self.post(costo=costo).status_code, 422)

    def test_vigencia_invalida_422(self):
        for extra in ({"vigente_desde": "2026-06-01", "vigente_hasta": "2026-05-31"}, {"vigente_desde": "ayer"},
                      {"vigente_desde": "2026-13-45"}, {"vigente_hasta": "manana"}, {"vigente_desde": None}):
            with self.subTest(extra):
                self.assertEqual(self.post(**extra).status_code, 422)
        self.assertEqual(self.post(vigente_desde="2026-06-01", vigente_hasta="2026-06-01").status_code, 201)  # un dia

    def test_faltantes_y_body_422(self):
        for falta in ("criterio", "rango_min", "costo", "vigente_desde"):
            c = cuerpo()
            del c[falta]
            with self.subTest(falta=falta):
                self.assertEqual(self.req("POST", self.t, "GS", json=c).status_code, 422)
        self.assertEqual(self.req("POST", self.t, "GS").status_code, 422)

    def test_422_de_negocio_lleva_mensaje_claro(self):
        r = self.post(rango_min="5", rango_max="5")
        self.assertEqual(r.status_code, 422)
        self.assertIn("mayor", str(r.json()["detail"]))
        r = self.post(vigente_desde="2026-06-01", vigente_hasta="2026-05-31")
        self.assertIn("vigencia", str(r.json()["detail"]).lower())

    # -- existencia / pertenencia / habilitacion -------------------------------------------
    def test_agencia_inexistente_404_en_todo(self):
        b = f"/999999/zonas/{self.zona}/tarifas"
        for metodo, ruta, kw in [("GET", b, {}), ("POST", b, {"json": cuerpo()}), ("GET", b + "/1", {}),
                                 ("PUT", b + "/1", {"json": {"costo": "1"}}), ("DELETE", b + "/1", {})]:
            with self.subTest(m=metodo, r=ruta):
                self.assertEqual(self.req(metodo, ruta, "GS", **kw).status_code, 404)

    def test_zona_inexistente_404_en_todo(self):
        b = f"/{self.ag}/zonas/999999/tarifas"
        for metodo, ruta, kw in [("GET", b, {}), ("POST", b, {"json": cuerpo()}), ("GET", b + "/1", {}),
                                 ("PUT", b + "/1", {"json": {"costo": "1"}}), ("DELETE", b + "/1", {})]:
            with self.subTest(m=metodo, r=ruta):
                self.assertEqual(self.req(metodo, ruta, "GS", **kw).status_code, 404)
        self.assertEqual(self.req("GET", f"/{self.ag}/zonas/abc/tarifas", "GS").status_code, 422)

    def test_zona_de_otra_agencia_404(self):
        otra = self.crear("GS", razon_social="Otra", nit="7778889990")["id_agencia"]
        z_ajena = self.otra_zona(agencia=otra)
        t_ajena = self.tarifa(ruta=f"/{otra}/zonas/{z_ajena}/tarifas")["id_tarifa"]
        ruta_cruzada = f"/{self.ag}/zonas/{z_ajena}/tarifas"     # zona de otra agencia bajo MI agencia
        for metodo, ruta, kw in [("GET", ruta_cruzada, {}), ("POST", ruta_cruzada, {"json": cuerpo()}),
                                 ("GET", f"{ruta_cruzada}/{t_ajena}", {}),
                                 ("PUT", f"{ruta_cruzada}/{t_ajena}", {"json": {"costo": "1"}}),
                                 ("DELETE", f"{ruta_cruzada}/{t_ajena}", {})]:
            with self.subTest(m=metodo):
                r = self.req(metodo, ruta, "GS", **kw)
                self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.req("GET", ruta_cruzada, "D").status_code, 404)
        d = self.req("GET", f"/{otra}/zonas/{z_ajena}/tarifas/{t_ajena}", "GS").json()["data"]  # la ajena intacta
        self.assertEqual(d["costo"], 10.5)

    def test_tarifa_de_otra_zona_o_agencia_404(self):
        z2 = self.otra_zona()
        t_de_z2 = self.tarifa(ruta=f"/{self.ag}/zonas/{z2}/tarifas")["id_tarifa"]
        for metodo, kw in [("GET", {}), ("PUT", {"json": {"costo": "1"}}), ("DELETE", {})]:
            with self.subTest(m=metodo):
                r = self.req(metodo, f"{self.t}/{t_de_z2}", "GS", **kw)   # tarifa de z2 pedida bajo mi zona
                self.assertEqual(r.status_code, 404, r.text)
                self.assertIn("no tiene una tarifa", r.json()["detail"])
        self.assertEqual(self.req("GET", f"{self.t}/999999", "GS").status_code, 404)
        self.assertEqual(self.req("GET", f"{self.t}/abc", "GS").status_code, 422)
        self.assertEqual(self.req("GET", f"/{self.ag}/zonas/{z2}/tarifas/{t_de_z2}", "GS").status_code, 200)

    def test_agencia_deshabilitada_bloquea_crear_y_actualizar_pero_no_consultar_ni_eliminar(self):
        t = self.tarifa()["id_tarifa"]
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": False})
        r = self.post(rango_min="5", rango_max="9")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("deshabilitada", r.json()["detail"])
        self.assertEqual(self.req("PUT", f"{self.t}/{t}", "GS", json={"costo": "1"}).status_code, 400)
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 1)
        self.assertEqual(self.req("GET", f"{self.t}/{t}", "GS").json()["data"]["costo"], 10.5)   # intacta
        self.assertEqual(self.req("DELETE", f"{self.t}/{t}", "GS").status_code, 200)              # quitar si
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": True})
        self.assertEqual(self.post().status_code, 201)                                            # rehabilitada

    # -- solapamiento de rangos ---------------------------------------------------------------
    def test_solapamiento_de_rangos_409(self):
        self.tarifa(rango_min="0", rango_max="5")
        for extra in ({"rango_min": "3", "rango_max": "8"}, {"rango_min": "0", "rango_max": "5"},
                      {"rango_min": "1", "rango_max": "2"}, {"rango_min": "0", "rango_max": "10"},
                      {"rango_min": "4.999", "rango_max": "9"}, {"rango_min": "2", "rango_max": None}):
            with self.subTest(extra):
                r = self.post(**extra)
                self.assertEqual(r.status_code, 409, (extra, r.text))
                self.assertIn("se solapa", r.json()["detail"])
                self.assertIn("PESO", r.json()["detail"])
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 1)

    def test_rangos_contiguos_y_separados_permitidos(self):
        self.tarifa(rango_min="0", rango_max="5")
        self.tarifa(rango_min="5", rango_max="10")        # contiguo: 5 no pertenece a [0,5)
        self.tarifa(rango_min="12", rango_max="20")       # separado
        self.tarifa(rango_min="20", rango_max=None)       # tramo abierto contiguo
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 4)

    def test_tramo_abierto_choca_con_cualquier_rango_por_encima(self):
        self.tarifa(rango_min="10", rango_max=None)
        self.assertEqual(self.post(rango_min="500", rango_max="600").status_code, 409)
        self.assertEqual(self.post(rango_min="0", rango_max="10.001").status_code, 409)
        self.assertEqual(self.post(rango_min="0", rango_max="10").status_code, 201)       # justo antes del abierto
        self.assertEqual(self.post(rango_min="99", rango_max=None).status_code, 409)      # dos abiertos

    def test_criterio_distinto_no_bloquea(self):
        self.tarifa(criterio="PESO", rango_min="0", rango_max="5")
        self.tarifa(criterio="VOLUMEN", rango_min="0", rango_max="5")     # mismo rango y vigencia, otro criterio
        self.tarifa(criterio="VOLUMEN", rango_min="5", rango_max=None)
        self.assertEqual(self.post(criterio="VOLUMEN", rango_min="3", rango_max="4").status_code, 409)   # ese si choca con [0,5)
        r = self.post(criterio="VOLUMEN", rango_min="6", rango_max="7")
        self.assertEqual(r.status_code, 409)
        self.assertIn("[5, en adelante)", r.json()["detail"])                                            # numeros legibles
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 3)

    def test_mismo_rango_en_otra_zona_o_agencia_no_bloquea(self):
        self.tarifa()
        z2 = self.otra_zona()
        self.tarifa(ruta=f"/{self.ag}/zonas/{z2}/tarifas")
        otra = self.crear("GS", razon_social="Otra", nit="7778889990")["id_agencia"]
        zo = self.otra_zona(agencia=otra, id_ciudad=LA_PAZ)
        self.tarifa(ruta=f"/{otra}/zonas/{zo}/tarifas")

    # -- solapamiento de vigencias --------------------------------------------------------------
    def test_solapamiento_de_vigencias_409(self):
        self.tarifa(vigente_desde="2026-01-01", vigente_hasta="2026-01-31")
        for extra in ({"vigente_desde": "2026-01-31", "vigente_hasta": "2026-02-15"},   # comparte el 31
                      {"vigente_desde": "2025-12-01", "vigente_hasta": "2026-01-01"},   # comparte el 1
                      {"vigente_desde": "2026-01-10", "vigente_hasta": "2026-01-20"},
                      {"vigente_desde": "2025-01-01", "vigente_hasta": None},
                      {"vigente_desde": "2026-01-15", "vigente_hasta": None}):
            with self.subTest(extra):
                self.assertEqual(self.post(**extra).status_code, 409, extra)

    def test_periodos_no_solapados_permitidos_y_expirada_mas_futura(self):
        expirada = self.tarifa(vigente_desde="2025-01-01", vigente_hasta="2025-12-31")
        self.tarifa(vigente_desde="2026-01-01", vigente_hasta="2026-06-30")               # dia siguiente: no se cruza
        self.tarifa(vigente_desde="2026-07-01", vigente_hasta=None)                        # futura abierta
        self.assertFalse(expirada["vigente"])
        self.assertEqual(self.post(vigente_desde="2026-03-01", vigente_hasta="2026-03-31").status_code, 409)
        self.assertEqual(self.req("GET", self.t, "GS").json()["total"], 3)

    def test_vigencia_abierta_bloquea_cualquier_periodo_posterior(self):
        self.tarifa(vigente_desde="2026-01-01", vigente_hasta=None)
        self.assertEqual(self.post(vigente_desde="2099-01-01", vigente_hasta="2099-12-31").status_code, 409)
        self.assertEqual(self.post(vigente_desde="2025-01-01", vigente_hasta="2025-12-31").status_code, 201)   # anterior

    def test_tarifa_inactiva_no_bloquea(self):
        inactiva = self.tarifa(is_active=False)
        self.assertEqual(self.post().status_code, 201)                    # el mismo tramo/vigencia entra
        r = self.req("PUT", f"{self.t}/{inactiva['id_tarifa']}", "GS", json={"is_active": True})
        self.assertEqual(r.status_code, 409, r.text)                       # reactivar SI valida
        self.assertEqual(self.req("PUT", f"{self.t}/{inactiva['id_tarifa']}", "GS", json={"rango_min": "5", "rango_max": "9", "is_active": True}).status_code, 200)

    # -- listar / detalle ----------------------------------------------------------------------
    def test_listar_orden_y_solo_de_la_zona(self):
        self.tarifa(criterio="VOLUMEN", rango_min="0", rango_max="1")
        self.tarifa(criterio="PESO", rango_min="5", rango_max=None)
        self.tarifa(criterio="PESO", rango_min="0", rango_max="5")
        z2 = self.otra_zona()
        self.tarifa(ruta=f"/{self.ag}/zonas/{z2}/tarifas")
        b = self.req("GET", self.t, "GS").json()
        self.assertEqual(b["total"], 3)
        self.assertEqual([(t["criterio"], t["rango_min"]) for t in b["data"]], [("PESO", 0.0), ("PESO", 5.0), ("VOLUMEN", 0.0)])
        for t in b["data"]:
            self.assertEqual(set(t), CAMPOS)
        self.assertEqual(self.req("GET", f"/{self.ag}/zonas/{z2}/tarifas", "GS").json()["total"], 1)

    def test_detalle_y_barra_final(self):
        t = self.tarifa()["id_tarifa"]
        r = self.req("GET", f"{self.t}/{t}", "ASU")
        self.assertEqual((r.status_code, r.json()["data"]["id_tarifa"]), (200, t))
        self.assertEqual(self.req("GET", self.t + "/", "GS").status_code, 200)

    def test_d_ve_tarifas_sin_datos_de_agencia(self):
        self.tarifa()
        for r in (self.req("GET", self.t, "D"), self.req("GET", f"{self.t}/{self.req('GET', self.t, 'GS').json()['data'][0]['id_tarifa']}", "D")):
            self.assertEqual(r.status_code, 200)
            for privado in PRIVADOS:
                self.assertNotIn(privado, r.text)
            self.assertNotIn("1020304050", r.text)
        self.assertEqual(self.req("GET", self.t, "D").json()["data"], self.req("GET", self.t, "GS").json()["data"])

    # -- actualizar ----------------------------------------------------------------------------
    def test_actualizar_parcial(self):
        t = self.tarifa()["id_tarifa"]
        r = self.req("PUT", f"{self.t}/{t}", "GS", json={"costo": "22.75"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual((d["costo"], d["rango_min"], d["rango_max"], d["criterio"], d["is_active"]), (22.75, 0.0, 5.0, "PESO", True))

    def test_actualizar_null_explicito_abre_rango_y_vigencia_y_ausente_no_cambia(self):
        t = self.tarifa(rango_min="5", rango_max="10", vigente_hasta="2026-12-31")["id_tarifa"]
        d = self.req("PUT", f"{self.t}/{t}", "GS", json={"costo": "1"}).json()["data"]
        self.assertEqual((d["rango_max"], d["vigente_hasta"]), (10.0, "2026-12-31"))         # ausente = no cambiar
        d = self.req("PUT", f"{self.t}/{t}", "GS", json={"rango_max": None, "vigente_hasta": None}).json()["data"]
        self.assertEqual((d["rango_max"], d["vigente_hasta"]), (None, None))                 # null = abierto
        d = self.req("PUT", f"{self.t}/{t}", "GS", json={"rango_max": "20", "vigente_hasta": "2027-06-30"}).json()["data"]
        self.assertEqual((d["rango_max"], d["vigente_hasta"]), (20.0, "2027-06-30"))         # y se puede volver a cerrar

    def test_actualizar_null_en_campos_obligatorios_422(self):
        t = self.tarifa()["id_tarifa"]
        for campo in ("criterio", "rango_min", "costo", "vigente_desde", "is_active"):
            with self.subTest(campo=campo):
                self.assertEqual(self.req("PUT", f"{self.t}/{t}", "GS", json={campo: None}).status_code, 422)

    def test_actualizar_body_vacio_400(self):
        t = self.tarifa()["id_tarifa"]
        self.assertEqual(self.req("PUT", f"{self.t}/{t}", "GS", json={}).status_code, 400)

    def test_actualizar_valida_el_resultado_completo(self):
        t = self.tarifa(rango_min="0", rango_max="5")["id_tarifa"]
        for cuerpo_ in ({"rango_min": "9"}, {"rango_max": "0.1", "rango_min": "1"}, {"vigente_hasta": "2025-12-31"},
                        {"costo": "-1"}, {"criterio": "PESADO"}, {"rango_min": "-3"}):
            with self.subTest(cuerpo_):
                self.assertEqual(self.req("PUT", f"{self.t}/{t}", "GS", json=cuerpo_).status_code, 422)
        d = self.req("GET", f"{self.t}/{t}", "GS").json()["data"]
        self.assertEqual((d["rango_min"], d["rango_max"], d["costo"]), (0.0, 5.0, 10.5))     # intacta

    def test_actualizar_solapamiento_409_excluyendo_a_si_misma(self):
        a = self.tarifa(rango_min="0", rango_max="5")["id_tarifa"]
        b = self.tarifa(rango_min="5", rango_max="10")["id_tarifa"]
        self.assertEqual(self.req("PUT", f"{self.t}/{a}", "GS", json={"rango_max": "6"}).status_code, 409)
        self.assertEqual(self.req("PUT", f"{self.t}/{a}", "GS", json={"rango_max": "5"}).status_code, 200)   # ella misma
        self.assertEqual(self.req("PUT", f"{self.t}/{a}", "GS", json={"rango_min": "0", "rango_max": "4.5", "costo": "3"}).status_code, 200)
        self.assertEqual(self.req("PUT", f"{self.t}/{b}", "GS", json={"rango_min": "4"}).status_code, 409)
        self.assertEqual(self.req("PUT", f"{self.t}/{b}", "GS", json={"rango_min": "4.5"}).status_code, 200)

    def test_actualizar_cambio_de_criterio_valida_contra_el_nuevo(self):
        self.tarifa(criterio="VOLUMEN", rango_min="0", rango_max="5")
        peso = self.tarifa(criterio="PESO", rango_min="0", rango_max="5")["id_tarifa"]
        self.assertEqual(self.req("PUT", f"{self.t}/{peso}", "GS", json={"criterio": "VOLUMEN"}).status_code, 409)

    def test_actualizar_404(self):
        self.assertEqual(self.req("PUT", f"{self.t}/999999", "GS", json={"costo": "1"}).status_code, 404)

    # -- eliminar / FK --------------------------------------------------------------------------
    def test_eliminar_200_luego_404_y_el_rango_queda_libre(self):
        t = self.tarifa()["id_tarifa"]
        r = self.req("DELETE", f"{self.t}/{t}", "GS")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), {"status", "data", "message"})
        self.assertIsNone(r.json()["data"])
        self.assertIn(f"#{t}", r.json()["message"])
        self.assertEqual(self.req("GET", f"{self.t}/{t}", "GS").status_code, 404)
        self.assertEqual(self.req("DELETE", f"{self.t}/{t}", "GS").status_code, 404)
        self.assertEqual(self.post().status_code, 201)

    def envio_con_tarifa(self, id_tarifa: int) -> int:
        u = self.crear_usuario("C")
        v = self.db.execute(text(
            "insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
            "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,10,0,'QR','PAGADO',:k,'DOMICILIO',"
            "'N','c@andes-express.com','1','d','c') returning id_venta"),
            {"u": u, "k": "T" + self.cuenta("select md5(random()::text)")[:8]}).scalar()
        suc = self.cuenta("select codigo_sucursal from sucursales limit 1")
        e = self.db.execute(text(
            "insert into envios (id_venta,estado,codigo_sucursal,id_agencia,id_tarifa_aplicada,costo_agencia,"
            "peso_kg,volumen_m3) values (:v,'ASIGNADO',:s,:a,:t,10,1,1) returning id_envio"),
            {"v": v, "s": suc, "a": self.ag, "t": id_tarifa}).scalar()
        self.db.commit()
        return e

    def test_eliminar_tarifa_usada_por_un_envio_409_y_se_sugiere_desactivar(self):
        t = self.tarifa()["id_tarifa"]
        self.envio_con_tarifa(t)
        r = self.req("DELETE", f"{self.t}/{t}", "GS")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("desactivela", r.json()["detail"])
        self.assertEqual(self.req("GET", f"{self.t}/{t}", "GS").status_code, 200)               # sigue
        r = self.req("PUT", f"{self.t}/{t}", "GS", json={"is_active": False})                    # el camino sugerido
        self.assertEqual((r.status_code, r.json()["data"]["is_active"]), (200, False))
        self.assertEqual(self.cuenta("select count(*) from envios where id_tarifa_aplicada=:t", t=t), 1)  # envio intacto

    def test_actualizar_tarifa_usada_por_un_envio_no_altera_el_snapshot_del_envio(self):
        t = self.tarifa(costo="10")["id_tarifa"]
        e = self.envio_con_tarifa(t)
        self.assertEqual(self.req("PUT", f"{self.t}/{t}", "GS", json={"costo": "99"}).status_code, 200)
        self.assertEqual(float(self.cuenta("select costo_agencia from envios where id_envio=:e", e=e)), 10.0)

    def test_zona_con_tarifas_sigue_sin_poder_eliminarse_y_total_tarifas_es_real(self):
        self.tarifa()
        self.tarifa(rango_min="5", rango_max="9")
        self.assertEqual(self.req("GET", f"/{self.ag}/zonas/{self.zona}", "GS").json()["data"]["total_tarifas"], 2)
        self.assertEqual(self.req("DELETE", f"/{self.ag}/zonas/{self.zona}", "GS").status_code, 409)

    # -- carreras y restricciones de la DB (traducidas, nunca 500) --------------------------------
    def test_check_de_la_db_por_carrera_422_no_500(self):
        """Si una validacion del servicio no ve el problema, el CHECK de la DB lo
        frena y la API responde 422."""
        with mock.patch.object(svc, "validar_rango"), mock.patch.object(svc, "validar_vigencia"), \
             mock.patch.object(svc, "validar_costo"):
            for extra, ck in [({"rango_min": "5", "rango_max": "5"}, "rango"), ({"rango_min": "-1"}, "rango"),
                              ({"vigente_desde": "2026-06-01", "vigente_hasta": "2026-05-31"}, "vigencia")]:
                with self.subTest(ck=ck, extra=extra):
                    # el schema ya lo frena para -1; aqui se prueba el camino del servicio con valores que pasan el schema
                    if extra.get("rango_min") == "-1":
                        continue
                    r = self.post(**extra)
                    self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.post(rango_min="1", rango_max="2").status_code, 201)     # la sesion sigue usable

    def test_fk_zona_desaparecida_404_no_500(self):
        """La zona se borra entre la validacion y el INSERT: la FK de la DB lo frena."""
        real = svc._buscar_zona

        def zona_y_borrarla(db, id_agencia, id_zona, **kw):
            z = real(db, id_agencia, id_zona, **kw)
            db.execute(text("delete from agencia_zonas where id_zona=:z"), {"z": id_zona})
            return z

        with mock.patch.object(svc, "_buscar_zona", side_effect=zona_y_borrarla):
            r = self.post()
        self.assertIn(r.status_code, (404, 409), r.text)     # 404 por la FK; nunca 500
        self.assertNotEqual(r.status_code, 500)

    def test_traduccion_de_fk_de_envios_es_409(self):
        self.assertEqual(svc.traducir_integrity_error(_integrity("fk_envios_id_tarifa_aplicada_agencia_tarifas")).status_code, 409)


def _integrity(nombre):
    from sqlalchemy.exc import IntegrityError

    class Diag:
        constraint_name = nombre

    class Orig(Exception):
        diag = Diag()

    return IntegrityError("x", {}, Orig(nombre))


if __name__ == "__main__":
    unittest.main()
