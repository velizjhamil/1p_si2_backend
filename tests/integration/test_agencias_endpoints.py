# Integracion CU19 fase 4 - COMPORTAMIENTO de /api/v1/agencias-reparto sobre la
# BD LOCAL de pruebas (jamas Supabase), con JWT reales (ver
# tests/support/agencias_api.py). La matriz de roles vive en
# test_agencias_authz.py; aqui se verifica el contrato de cada endpoint.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_endpoints.py
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import text  # noqa: E402

from app.main import app  # noqa: E402
from app.modules.delivery import agencias_service as svc  # noqa: E402
from tests.support.agencias_api import BASE, BaseAgenciasAPI, bd_lista, payload  # noqa: E402

PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal")
RESUMEN = {"id_agencia", "razon_social", "contacto_operativo", "telefono", "is_active"}


class RutasExpuestas(unittest.TestCase):
    """Sin BD: inventario de rutas de agencias (el resto de CU19 cuelga de aqui)."""

    def test_rutas_y_metodos(self):
        # Se lee del esquema OpenAPI: FastAPI reciente no lista los routers
        # incluidos en `app.routes`.
        paths = app.openapi()["paths"]
        rutas = {p: {m.upper() for m in ops} for p, ops in paths.items() if p.startswith(BASE)}
        self.assertEqual(rutas[BASE], {"GET", "POST"})
        self.assertEqual(rutas[BASE + "/"], {"GET", "POST"})
        self.assertEqual(rutas[BASE + "/{id_agencia}"], {"GET", "PUT", "DELETE"})
        self.assertEqual(rutas[BASE + "/{id_agencia}/estado"], {"PATCH"})
        # Fase 5 agrego las rutas de zonas (y Fase 6 sus tarifas); Fase 7 agrega
        # /disponibles y Fase 8 SOLO GET /{id_agencia}/cotizacion (no hay asignacion).
        extra = set(rutas) - {BASE, BASE + "/", BASE + "/{id_agencia}", BASE + "/{id_agencia}/estado",
                              BASE + "/disponibles", BASE + "/{id_agencia}/cotizacion"}
        self.assertTrue(all("/zonas" in p for p in extra), extra)
        for p in rutas:
            if "cotiz" in p:                                    # Fase 8: solo GET /{id_agencia}/cotizacion
                self.assertEqual(p, BASE + "/{id_agencia}/cotizacion")
                self.assertEqual(rutas[p], {"GET"})
            if "disponibles" in p:                              # Fase 7: solo GET /disponibles
                self.assertEqual(p, BASE + "/disponibles")
                self.assertEqual(rutas[p], {"GET"})
            if "tarifa" in p:  # Fase 6: solo el recurso anidado zonas/{id_zona}/tarifas
                self.assertIn("/zonas/{id_zona}/tarifas", p)

    def test_envios_sigue_montado_igual(self):
        paths = app.openapi()["paths"]
        self.assertEqual({m.upper() for m in paths["/api/v1/envios"]}, {"GET", "POST"})
        for p in ("/api/v1/envios/{id_envio}/asignar", "/api/v1/envios/repartidores",
                  "/api/v1/envios/{id_envio}/estado", "/api/v1/envios/{id_envio}/confirmar-preparacion"):
            self.assertIn(p, paths)
        # Fase 9: el contrato de asignar de CU18 se AMPLIA sin romperse: se sigue asignando
        # con `id_repartidor` (mismo campo, mismo tipo) y `id_agencia` se agrega como
        # alternativa exclusiva. No hay un endpoint nuevo: es el mismo PATCH /asignar.
        req = app.openapi()["components"]["schemas"]["AsignarEnvioPayload"]
        self.assertIn("id_repartidor", req["properties"])
        for campo in ("id_agencia", "peso_kg", "volumen_m3"):
            self.assertIn(campo, req["properties"])
        asignar = [p for p in paths if p.endswith("/asignar")]
        self.assertEqual(asignar, ["/api/v1/envios/{id_envio}/asignar"])
        self.assertEqual({m.upper() for m in paths[asignar[0]]}, {"PATCH"})


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class AgenciasEndpoints(BaseAgenciasAPI):
    # -- POST ---------------------------------------------------------------
    def test_crear_201_envelope_y_normalizacion(self):
        r = self.req("POST", "", "GS", json=payload(nit="1.020-304 050", razon_social="  Andes    Express ",
                                                      correo_facturacion="Facturas@Andes-Express.COM", telefono="  "))
        self.assertEqual(r.status_code, 201, r.text)
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message"})
        self.assertEqual(b["status"], "success")
        d = b["data"]
        self.assertEqual((d["nit"], d["razon_social"], d["is_active"]), ("1020304050", "Andes Express", True))
        self.assertEqual(d["correo_facturacion"], "Facturas@andes-express.com")
        self.assertIsNone(d["telefono"])
        self.assertEqual((d["total_zonas"], d["total_envios"]), (0, 0))
        self.assertIn("habilitada", b["message"])

    def test_crear_asu(self):
        self.assertEqual(self.req("POST", "", "ASU", json=payload()).status_code, 201)

    def test_crear_ruta_con_barra_final(self):
        self.assertEqual(self.req("POST", "/", "GS", json=payload()).status_code, 201)

    def test_crear_duplicados_409(self):
        self.crear()
        casos = [
            (payload(razon_social="Otra", nit="1.020.304-050"), "NIT"),
            (payload(nit="9990001112", razon_social="ANDES EXPRESS"), "razon social"),
            (payload(nit="9990001112", razon_social="  andes    express "), "razon social"),
        ]
        for cuerpo, texto in casos:
            with self.subTest(cuerpo=cuerpo):
                r = self.req("POST", "", "GS", json=cuerpo)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertIn(texto, r.json()["detail"])

    def test_crear_422_de_schema(self):
        for falta in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            cuerpo = payload()
            del cuerpo[falta]
            with self.subTest(falta=falta):
                self.assertEqual(self.req("POST", "", "GS", json=cuerpo).status_code, 422)
        for campo in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            with self.subTest(vacio=campo):
                self.assertEqual(self.req("POST", "", "GS", json=payload(**{campo: "   "})).status_code, 422)
        self.assertEqual(self.req("POST", "", "GS", json=payload(razon_social="x" * 151)).status_code, 422)
        self.assertEqual(self.req("POST", "", "GS").status_code, 422)  # sin body

    def test_crear_422_de_datos_de_facturacion(self):
        for extra, texto in [
            ({"nit": "ABC-123"}, "NIT"),
            ({"nit": "1234"}, "NIT"),
            ({"nit": "0000000"}, "NIT"),
            ({"correo_facturacion": "sin-arroba"}, "correo de facturacion"),
            ({"correo_facturacion": "a@factura.test"}, "correo de facturacion"),  # dominio reservado
            ({"direccion_fiscal": "..."}, "direccion fiscal"),
            ({"razon_social": "#"}, "razon social"),
            ({"correo": "mal"}, "correo de contacto"),
        ]:
            with self.subTest(extra=extra):
                r = self.req("POST", "", "GS", json=payload(**extra))
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIn(texto, r.json()["detail"])
        self.assertEqual(self.req("GET", "", "GS").json()["total"], 0)  # nada se guardo

    def test_crear_carrera_real_409_no_500(self):
        """El chequeo previo no ve el duplicado (otra transaccion): el UNIQUE lo frena."""
        self.crear()
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            r = self.req("POST", "", "GS", json=payload(razon_social="Otra"))
        self.assertEqual(r.status_code, 409, r.text)

    # -- GET lista ------------------------------------------------------------
    def test_listar_envelope_paginacion_y_orden(self):
        for n in ("Delta", "alfa", "Charlie", "Bravo", "Echo"):
            self.crear(razon_social=n, nit=f"1000{ord(n[0]):03d}")
        r = self.req("GET", "", "GS", params={"page": 1, "limit": 2})
        b = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((b["total"], b["page"], b["limit"], b["pages"]), (5, 1, 2, 3))
        self.assertEqual([a["razon_social"] for a in b["data"]], ["alfa", "Bravo"])  # sin distinguir mayusculas
        p3 = self.req("GET", "", "GS", params={"page": 3, "limit": 2}).json()
        self.assertEqual([a["razon_social"] for a in p3["data"]], ["Echo"])
        self.assertEqual(self.req("GET", "", "GS", params={"page": 9, "limit": 2}).json()["data"], [])

    def test_listar_parametros_invalidos_422(self):
        for params in ({"page": 0}, {"limit": 0}, {"limit": 101}, {"is_active": "quizas"}, {"page": "x"}):
            with self.subTest(params=params):
                self.assertEqual(self.req("GET", "", "GS", params=params).status_code, 422)

    def test_listar_filtros(self):
        a = self.crear(razon_social="Andes Express")
        b = self.crear(razon_social="Beta Cargo", nit="2223334445", contacto_operativo="Ana Perez")
        self.req("PATCH", f"/{b['id_agencia']}/estado", "GS", json={"is_active": False})
        ids = lambda **p: [x["id_agencia"] for x in self.req("GET", "", "GS", params=p).json()["data"]]  # noqa: E731
        self.assertEqual(ids(is_active="false"), [b["id_agencia"]])
        self.assertEqual(ids(is_active="true"), [a["id_agencia"]])
        self.assertEqual(ids(q="beta"), [b["id_agencia"]])
        self.assertEqual(ids(q="2223"), [b["id_agencia"]])          # por NIT (GS si)
        self.assertEqual(ids(q="ana per"), [b["id_agencia"]])       # por contacto
        self.assertEqual(ids(q="zzz"), [])
        self.assertEqual(len(ids()), 2)

    def test_listar_gs_incluye_datos_de_facturacion(self):
        self.crear()
        item = self.req("GET", "", "GS").json()["data"][0]
        for campo in PRIVADOS:
            self.assertIn(campo, item)

    # -- GET detalle ------------------------------------------------------------
    def test_detalle_200_404_y_422(self):
        a = self.crear()
        r = self.req("GET", f"/{a['id_agencia']}", "ASU")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["nit"], "1020304050")
        self.assertEqual(r.json()["data"]["total_zonas"], 0)
        self.assertEqual(self.req("GET", "/999999", "GS").status_code, 404)
        self.assertEqual(self.req("GET", "/abc", "GS").status_code, 422)

    # -- visibilidad y privacidad de D ---------------------------------------------
    def test_d_solo_ve_habilitadas_en_listado_y_detalle(self):
        a = self.crear(razon_social="Andes Express")
        b = self.crear(razon_social="Beta Cargo", nit="2223334445")
        self.req("PATCH", f"/{b['id_agencia']}/estado", "GS", json={"is_active": False})

        lista = self.req("GET", "", "D").json()
        self.assertEqual([x["id_agencia"] for x in lista["data"]], [a["id_agencia"]])
        self.assertEqual(lista["total"], 1)
        # su filtro is_active=false se ignora: sigue viendo solo habilitadas
        self.assertEqual(self.req("GET", "", "D", params={"is_active": "false"}).json()["total"], 1)
        self.assertEqual(self.req("GET", f"/{a['id_agencia']}", "D").status_code, 200)
        r = self.req("GET", f"/{b['id_agencia']}", "D")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.req("GET", f"/{b['id_agencia']}", "GS").status_code, 200)  # GS si la ve

    def test_d_nunca_recibe_datos_de_facturacion(self):
        a = self.crear(contacto_operativo="Ana Perez", telefono="70000000")
        item = self.req("GET", "", "D").json()["data"][0]
        det = self.req("GET", f"/{a['id_agencia']}", "D").json()["data"]
        for cuerpo in (item, det):
            self.assertEqual(set(cuerpo), RESUMEN)
            for campo in PRIVADOS:
                self.assertNotIn(campo, cuerpo)
        self.assertEqual((det["contacto_operativo"], det["telefono"]), ("Ana Perez", "70000000"))
        # ni el texto plano de la respuesta contiene el NIT
        self.assertNotIn("1020304050", self.req("GET", "", "D").text)

    def test_d_no_puede_buscar_por_nit(self):
        self.crear()
        self.assertEqual(self.req("GET", "", "D", params={"q": "1020304050"}).json()["total"], 0)
        self.assertEqual(self.req("GET", "", "D", params={"q": "andes"}).json()["total"], 1)

    # -- PUT ------------------------------------------------------------------------
    def test_actualizar_parcial_y_no_cambia_estado(self):
        a = self.crear()
        r = self.req("PUT", f"/{a['id_agencia']}", "GS",
                     json={"telefono": "71111111", "razon_social": "Andes  Express Plus", "is_active": False})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual((d["telefono"], d["razon_social"], d["nit"], d["is_active"]),
                         ("71111111", "Andes Express Plus", "1020304050", True))  # is_active ignorado

    def test_actualizar_body_vacio_400(self):
        a = self.crear()
        for cuerpo in ({}, {"telefono": None}, {"telefono": "  "}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.req("PUT", f"/{a['id_agencia']}", "GS", json=cuerpo).status_code, 400)

    def test_actualizar_no_vacia_obligatorios(self):
        a = self.crear()
        for campo in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            with self.subTest(campo=campo):
                self.assertEqual(self.req("PUT", f"/{a['id_agencia']}", "GS", json={campo: "  "}).status_code, 422)

    def test_actualizar_duplicado_409_y_propio_valor_ok(self):
        a = self.crear()
        b = self.crear(razon_social="Beta Cargo", nit="2223334445")
        r = self.req("PUT", f"/{b['id_agencia']}", "GS", json={"nit": "1020-304-050"})
        self.assertEqual(r.status_code, 409)
        r = self.req("PUT", f"/{b['id_agencia']}", "GS", json={"razon_social": " ANDES express "})
        self.assertEqual(r.status_code, 409)
        r = self.req("PUT", f"/{a['id_agencia']}", "GS", json={"nit": "1.020.304.050", "razon_social": "ANDES EXPRESS"})
        self.assertEqual(r.status_code, 200)

    def test_actualizar_422_y_404(self):
        a = self.crear()
        self.assertEqual(self.req("PUT", f"/{a['id_agencia']}", "GS", json={"nit": "12ab"}).status_code, 422)
        self.assertEqual(self.req("PUT", f"/{a['id_agencia']}", "GS", json={"correo_facturacion": "x"}).status_code, 422)
        self.assertEqual(self.req("PUT", "/999999", "GS", json={"telefono": "1"}).status_code, 404)
        self.assertEqual(self.req("GET", f"/{a['id_agencia']}", "GS").json()["data"]["nit"], "1020304050")

    # -- PATCH estado -------------------------------------------------------------------
    def test_estado_deshabilitar_habilitar_e_idempotente(self):
        a = self.crear()
        i = a["id_agencia"]
        r = self.req("PATCH", f"/{i}/estado", "GS", json={"is_active": False})
        self.assertEqual((r.status_code, r.json()["data"]["is_active"], r.json()["message"]), (200, False, "Agencia deshabilitada."))
        r = self.req("PATCH", f"/{i}/estado", "GS", json={"is_active": False})  # ya deshabilitada: sin error
        self.assertEqual((r.status_code, r.json()["data"]["is_active"]), (200, False))
        r = self.req("PATCH", f"/{i}/estado", "ASU", json={"is_active": True})
        self.assertEqual((r.status_code, r.json()["message"]), (200, "Agencia habilitada."))

    def test_estado_422_y_404(self):
        a = self.crear()
        self.assertEqual(self.req("PATCH", f"/{a['id_agencia']}/estado", "GS", json={}).status_code, 422)
        self.assertEqual(self.req("PATCH", f"/{a['id_agencia']}/estado", "GS", json={"is_active": "quizas"}).status_code, 422)
        self.assertEqual(self.req("PATCH", "/999999/estado", "GS", json={"is_active": False}).status_code, 404)

    def test_deshabilitar_con_envios_no_toca_el_envio(self):
        a = self.crear()
        e = self.envio_con_agencia(a["id_agencia"])
        sql = "select estado, id_agencia, costo_agencia, fecha_actualizacion from envios where id_envio=:e"
        antes = tuple(self.db.execute(text(sql), {"e": e}).one())
        self.assertEqual(self.req("PATCH", f"/{a['id_agencia']}/estado", "GS", json={"is_active": False}).status_code, 200)
        self.assertEqual(tuple(self.db.execute(text(sql), {"e": e}).one()), antes)
        self.assertEqual(self.req("GET", f"/{a['id_agencia']}", "GS").json()["data"]["total_envios"], 1)

    # -- DELETE -----------------------------------------------------------------------------
    def test_eliminar_sin_envios_200_y_luego_404(self):
        a = self.crear()
        r = self.req("DELETE", f"/{a['id_agencia']}", "GS")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), {"status", "data", "message"})
        self.assertIsNone(r.json()["data"])
        self.assertIn("Andes Express", r.json()["message"])
        self.assertEqual(self.req("GET", f"/{a['id_agencia']}", "GS").status_code, 404)
        self.assertEqual(self.req("DELETE", f"/{a['id_agencia']}", "GS").status_code, 404)

    def test_eliminar_con_envios_409_y_la_agencia_permanece(self):
        a = self.crear()
        self.envio_con_agencia(a["id_agencia"])
        r = self.req("DELETE", f"/{a['id_agencia']}", "GS")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("deshabilitela", r.json()["detail"])
        self.assertEqual(self.req("GET", f"/{a['id_agencia']}", "GS").status_code, 200)
        # el camino sugerido si funciona
        self.assertEqual(self.req("PATCH", f"/{a['id_agencia']}/estado", "GS", json={"is_active": False}).status_code, 200)

    def test_eliminar_404(self):
        self.assertEqual(self.req("DELETE", "/999999", "GS").status_code, 404)

    # -- CU18 intacto ---------------------------------------------------------------------------
    def test_envios_cu18_responde_igual(self):
        r = self.req("GET", "", "GS")  # agencias
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/v1/envios", headers=self.headers("GS"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()) >= {"status", "data", "message", "total"}, True)
        self.assertEqual(self.client.get("/api/v1/envios", headers=self.headers("V")).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/envios/repartidores", headers=self.headers("D")).status_code, 403)


if __name__ == "__main__":
    unittest.main()
