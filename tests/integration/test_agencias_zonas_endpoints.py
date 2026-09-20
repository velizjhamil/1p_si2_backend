# Integracion CU19 fase 5 - COMPORTAMIENTO de las zonas de cobertura sobre la BD
# LOCAL de pruebas (jamas Supabase ni Render), con JWT reales. La matriz de
# roles vive en test_agencias_zonas_authz.py.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_zonas_endpoints.py
#
# Semantica de subzona (`nombre_zona`) que se verifica aqui y en el service:
#   - se recorta y se colapsan espacios internos; vacia = sin subzona ("toda la
#     ciudad");
#   - se compara sin distinguir mayusculas y SIN ignorar tildes (igual que el
#     UNIQUE funcional de la DB).
# Ciudad por nombre: se normaliza (mayusculas, tildes, espacios). Ambiguo -> 409.
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import text  # noqa: E402

from app.modules.delivery import zonas_service as svc  # noqa: E402
from app.modules.empresa.models import Ciudad  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

CAMPOS_ZONA = {"id_zona", "id_agencia", "id_ciudad", "ciudad", "departamento", "nombre_zona",
               "total_tarifas", "fecha_creacion"}
PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal", "razon_social")
LA_PAZ, COCHABAMBA = 2, 3  # ids del catalogo sembrado por las migraciones CU17


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ZonasEndpoints(BaseAgenciasAPI):
    def setUp(self):
        super().setUp()
        self.ag = self.crear("GS")["id_agencia"]
        self.z = f"/{self.ag}/zonas"

    # -- helpers ---------------------------------------------------------------
    def zona(self, rol="GS", agencia=None, **json):
        ruta = f"/{agencia or self.ag}/zonas"
        r = self.req("POST", ruta, rol, json=json)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def ciudad_sql(self, nombre, departamento="X") -> int:
        i = self.db.execute(text("insert into ciudades (nombre, departamento) values (:n,:d) returning id"),
                            {"n": nombre, "d": departamento}).scalar()
        self.db.commit()
        return i

    def tarifa_sql(self, id_zona) -> int:
        t = self.db.execute(text("insert into agencia_tarifas (id_zona,criterio,rango_min,costo,vigente_desde) "
                                 "values (:z,'PESO',0,10,'2026-01-01') returning id_tarifa"), {"z": id_zona}).scalar()
        self.db.commit()
        return t

    def cuenta(self, sql, **p):
        return self.db.execute(text(sql), p).scalar()

    # -- crear --------------------------------------------------------------------
    def test_crear_sin_subzona_201_y_envelope(self):
        r = self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ})
        self.assertEqual(r.status_code, 201, r.text)
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message"})
        d = b["data"]
        self.assertEqual(set(d), CAMPOS_ZONA)
        self.assertEqual((d["id_agencia"], d["id_ciudad"], d["ciudad"], d["nombre_zona"], d["total_tarifas"]),
                         (self.ag, LA_PAZ, "La Paz", None, 0))

    def test_crear_con_subzona_normalizada(self):
        d = self.zona(id_ciudad=LA_PAZ, nombre_zona="  Zona    Sur ")
        self.assertEqual(d["nombre_zona"], "Zona Sur")
        self.assertEqual(self.zona(id_ciudad=COCHABAMBA, nombre_zona="   ")["nombre_zona"], None)  # vacia = sin subzona

    def test_crear_ruta_con_barra_final(self):
        self.assertEqual(self.req("POST", self.z + "/", "ASU", json={"id_ciudad": LA_PAZ}).status_code, 201)

    def test_crear_por_nombre_de_ciudad_normalizado(self):
        nueva = self.ciudad_sql("Ñandú Ciudad")
        for n, nombre in enumerate(("ñandú ciudad", "NANDU CIUDAD", "  Nandu   Ciudad ")):
            ag = self.crear("GS", razon_social=f"Ag {n}", nit=f"600000{n}1")["id_agencia"]
            r = self.req("POST", f"/{ag}/zonas", "GS", json={"ciudad": nombre})
            self.assertEqual(r.status_code, 201, (nombre, r.text))
            self.assertEqual(r.json()["data"]["id_ciudad"], nueva)

    def test_ciudad_inexistente_404_por_id_y_por_nombre(self):
        for cuerpo in ({"id_ciudad": 999999}, {"ciudad": "Atlantida"}):
            with self.subTest(cuerpo=cuerpo):
                r = self.req("POST", self.z, "GS", json=cuerpo)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertIn("ciudad", r.json()["detail"].lower())
        self.assertEqual(self.req("GET", self.z, "GS").json()["total"], 0)

    def test_ciudad_ambigua_por_nombre_409_y_no_disponible(self):
        dup = self.ciudad_sql("LA  PAZ", "Otro Departamento")  # mismo nombre normalizado que id=2
        r = self.req("POST", self.z, "GS", json={"ciudad": "la paz"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ambiguo", r.json()["detail"])
        self.assertIn("id_ciudad", r.json()["detail"])
        self.assertEqual(self.req("GET", self.z, "GS").json()["total"], 0)
        # con id_ciudad no hay ambiguedad
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": dup}).status_code, 201)
        # y la resolucion por nombre (la que usara la disponibilidad) NO devuelve ciudad
        self.assertIsNone(svc.resolver_ciudad(self.db, "La Paz"))
        self.assertEqual(sorted(c.id for c in svc.buscar_ciudades_por_nombre(self.db, "la paz")), sorted([LA_PAZ, dup]))
        self.assertEqual(svc.resolver_ciudad(self.db, "Cochabamba").id, COCHABAMBA)  # las no ambiguas si

    def test_ciudad_obligatoria_y_una_sola_forma_422(self):
        for cuerpo in ({}, {"nombre_zona": "Sur"}, {"id_ciudad": 0}, {"id_ciudad": LA_PAZ, "ciudad": "La Paz"},
                       {"ciudad": "   "}, {"id_ciudad": "abc"}, {"id_ciudad": 2, "nombre_zona": "x" * 101}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.req("POST", self.z, "GS", json=cuerpo).status_code, 422)
        self.assertEqual(self.req("POST", self.z, "GS").status_code, 422)  # sin body

    def test_subzona_sin_letras_422(self):
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": "---"}).status_code, 422)

    # -- agencia inexistente / deshabilitada ------------------------------------------
    def test_agencia_inexistente_404_en_todo(self):
        for metodo, ruta, kw in [("GET", "/999999/zonas", {}), ("POST", "/999999/zonas", {"json": {"id_ciudad": 2}}),
                                 ("GET", "/999999/zonas/1", {}), ("PUT", "/999999/zonas/1", {"json": {"nombre_zona": "x"}}),
                                 ("DELETE", "/999999/zonas/1", {})]:
            with self.subTest(m=metodo, r=ruta):
                self.assertEqual(self.req(metodo, ruta, "GS", **kw).status_code, 404)
        self.assertEqual(self.req("GET", "/abc/zonas", "GS").status_code, 422)

    def test_agencia_deshabilitada_bloquea_crear_y_actualizar_pero_no_consultar_ni_eliminar(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": False})
        r = self.req("POST", self.z, "GS", json={"id_ciudad": COCHABAMBA})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("deshabilitada", r.json()["detail"])
        self.assertEqual(self.req("PUT", f"{self.z}/{z['id_zona']}", "GS", json={"nombre_zona": "Norte"}).status_code, 400)
        self.assertEqual(self.req("GET", self.z, "GS").json()["total"], 1)  # GS consulta
        self.assertEqual(self.req("GET", f"{self.z}/{z['id_zona']}", "ASU").status_code, 200)
        self.assertEqual(self.req("GET", f"{self.z}/{z['id_zona']}", "GS").json()["data"]["nombre_zona"], "Sur")  # intacta
        self.assertEqual(self.req("DELETE", f"{self.z}/{z['id_zona']}", "GS").status_code, 200)  # quitar cobertura si
        # rehabilitada vuelve a aceptar zonas
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": True})
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": COCHABAMBA}).status_code, 201)

    # -- duplicados y diferencias de subzona ---------------------------------------------
    def test_duplicado_misma_agencia_ciudad_sin_subzona_409(self):
        self.zona(id_ciudad=LA_PAZ)
        r = self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("toda la ciudad", r.json()["detail"])
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": "  "}).status_code, 409)
        self.assertEqual(self.req("POST", self.z, "GS", json={"ciudad": "la paz"}).status_code, 409)  # por nombre tambien

    def test_duplicado_con_subzona_ignora_mayusculas_y_espacios_pero_no_tildes(self):
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Zona Sur")
        for variante in ("ZONA SUR", "zona sur", "  Zona    Sur  "):
            with self.subTest(variante):
                r = self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": variante})
                self.assertEqual(r.status_code, 409, r.text)
        # con tilde es OTRA subzona (no se ignoran tildes)
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sopocachi")
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": "Sopocachí"}).status_code, 201)

    def test_zonas_distintas_no_son_duplicado(self):
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Norte")           # otra subzona
        self.zona(id_ciudad=LA_PAZ)                                  # toda la ciudad + subzonas conviven
        self.zona(id_ciudad=COCHABAMBA, nombre_zona="Sur")          # misma subzona, otra ciudad
        otra = self.crear("GS", razon_social="Otra Agencia", nit="7778889990")["id_agencia"]
        self.zona(agencia=otra, id_ciudad=LA_PAZ, nombre_zona="Sur")  # misma cobertura, otra agencia
        self.assertEqual(self.req("GET", self.z, "GS").json()["total"], 4)

    def test_carrera_real_unique_409_no_500(self):
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        with mock.patch.object(svc, "_cobertura_en_uso", return_value=False):
            r = self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": "SUR"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.req("POST", self.z, "GS", json={"id_ciudad": COCHABAMBA}).status_code, 201)  # sesion usable

    def test_fk_ciudad_de_la_db_404_no_500(self):
        """Si la ciudad desaparece entre la validacion y el INSERT, la FK de la DB
        lo frena y la API responde 404."""
        fantasma = Ciudad(id=999999, nombre="Fantasma", departamento="X")
        with mock.patch.object(svc, "_ciudad_del_payload", return_value=fantasma):
            r = self.req("POST", self.z, "GS", json={"id_ciudad": LA_PAZ})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.cuenta("select count(*) from agencia_zonas where id_agencia=:a", a=self.ag), 0)

    # -- listar / detalle -----------------------------------------------------------------
    def test_listar_orden_por_ciudad_y_subzona(self):
        self.zona(id_ciudad=COCHABAMBA)
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        self.zona(id_ciudad=LA_PAZ)
        self.zona(id_ciudad=LA_PAZ, nombre_zona="norte")
        b = self.req("GET", self.z, "GS").json()
        self.assertEqual(b["total"], 4)
        self.assertEqual([(z["ciudad"], z["nombre_zona"]) for z in b["data"]],
                         [("Cochabamba", None), ("La Paz", None), ("La Paz", "norte"), ("La Paz", "Sur")])
        for z in b["data"]:
            self.assertEqual(set(z), CAMPOS_ZONA)

    def test_listar_solo_zonas_de_esa_agencia(self):
        otra = self.crear("GS", razon_social="Otra", nit="7778889990")["id_agencia"]
        self.zona(agencia=otra, id_ciudad=LA_PAZ)
        self.zona(id_ciudad=COCHABAMBA)
        self.assertEqual([z["id_ciudad"] for z in self.req("GET", self.z, "GS").json()["data"]], [COCHABAMBA])

    def test_detalle_200_y_404(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        r = self.req("GET", f"{self.z}/{z['id_zona']}", "ASU")
        self.assertEqual((r.status_code, r.json()["data"]["nombre_zona"]), (200, "Sur"))
        self.assertEqual(self.req("GET", f"{self.z}/999999", "GS").status_code, 404)
        self.assertEqual(self.req("GET", f"{self.z}/abc", "GS").status_code, 422)

    def test_zona_de_otra_agencia_404_en_get_put_delete(self):
        otra = self.crear("GS", razon_social="Otra", nit="7778889990")["id_agencia"]
        ajena = self.zona(agencia=otra, id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        for metodo, kw in [("GET", {}), ("PUT", {"json": {"nombre_zona": "Hack"}}), ("DELETE", {})]:
            with self.subTest(m=metodo):
                r = self.req(metodo, f"{self.z}/{ajena}", "GS", **kw)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertIn("no tiene una zona", r.json()["detail"])
        for rol in ("GS", "D"):  # tampoco D
            self.assertEqual(self.req("GET", f"{self.z}/{ajena}", rol).status_code, 404)
        # la zona ajena sigue intacta
        d = self.req("GET", f"/{otra}/zonas/{ajena}", "GS").json()["data"]
        self.assertEqual((d["nombre_zona"], d["id_agencia"]), ("Sur", otra))

    # -- privacidad de D ----------------------------------------------------------------------
    def test_d_solo_ve_agencias_habilitadas_y_sin_datos_de_agencia(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        for r in (self.req("GET", self.z, "D"), self.req("GET", f"{self.z}/{z['id_zona']}", "D")):
            self.assertEqual(r.status_code, 200)
            for privado in PRIVADOS:
                self.assertNotIn(privado, r.text)
            self.assertNotIn("1020304050", r.text)
        self.assertEqual(self.req("GET", self.z, "D").json()["data"], self.req("GET", self.z, "GS").json()["data"])
        self.req("PATCH", f"/{self.ag}/estado", "GS", json={"is_active": False})
        self.assertEqual(self.req("GET", self.z, "D").status_code, 404)

    # -- actualizar ----------------------------------------------------------------------------
    def test_actualizar_subzona_ciudad_y_ambas(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        u = f"{self.z}/{z}"
        r = self.req("PUT", u, "GS", json={"nombre_zona": "  Zona   Norte "})
        self.assertEqual((r.status_code, r.json()["data"]["nombre_zona"], r.json()["data"]["ciudad"]), (200, "Zona Norte", "La Paz"))
        r = self.req("PUT", u, "GS", json={"id_ciudad": COCHABAMBA})  # conserva subzona
        self.assertEqual((r.json()["data"]["ciudad"], r.json()["data"]["nombre_zona"]), ("Cochabamba", "Zona Norte"))
        r = self.req("PUT", u, "ASU", json={"ciudad": "la paz", "nombre_zona": "Centro"})
        self.assertEqual((r.json()["data"]["ciudad"], r.json()["data"]["nombre_zona"]), ("La Paz", "Centro"))

    def test_actualizar_body_vacio_400_y_ninguna_forma_de_vaciar_la_subzona(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        for cuerpo in ({}, {"nombre_zona": None}, {"nombre_zona": "  "}, {"nombre_zona": ""}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json=cuerpo).status_code, 400)
        self.assertEqual(self.req("GET", f"{self.z}/{z}", "GS").json()["data"]["nombre_zona"], "Sur")

    def test_actualizar_sin_cambios_reales_200_sin_error_por_duplicarse_a_si_misma(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json={"nombre_zona": "Sur"}).status_code, 200)
        r = self.req("PUT", f"{self.z}/{z}", "GS", json={"nombre_zona": "SUR", "id_ciudad": LA_PAZ})  # solo cambia la forma
        self.assertEqual((r.status_code, r.json()["data"]["nombre_zona"]), (200, "SUR"))

    def test_actualizar_a_cobertura_existente_409_y_no_modifica(self):
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")
        otra = self.zona(id_ciudad=LA_PAZ, nombre_zona="Norte")["id_zona"]
        r = self.req("PUT", f"{self.z}/{otra}", "GS", json={"nombre_zona": "  SUR "})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.req("GET", f"{self.z}/{otra}", "GS").json()["data"]["nombre_zona"], "Norte")
        sin_sub = self.zona(id_ciudad=COCHABAMBA)["id_zona"]
        self.zona(id_ciudad=LA_PAZ)  # toda La Paz
        self.assertEqual(self.req("PUT", f"{self.z}/{sin_sub}", "GS", json={"id_ciudad": LA_PAZ}).status_code, 409)

    def test_actualizar_sin_referencias_invalidas(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json={"id_ciudad": 999999}).status_code, 404)
        self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json={"ciudad": "Atlantida"}).status_code, 404)
        self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json={"id_ciudad": 2, "ciudad": "x"}).status_code, 422)
        self.assertEqual(self.req("PUT", f"{self.z}/{z}", "GS", json={"nombre_zona": "---"}).status_code, 422)
        self.assertEqual(self.req("PUT", f"{self.z}/999999", "GS", json={"nombre_zona": "x"}).status_code, 404)
        d = self.req("GET", f"{self.z}/{z}", "GS").json()["data"]
        self.assertEqual((d["id_ciudad"], d["nombre_zona"]), (LA_PAZ, "Sur"))

    def test_actualizar_a_ciudad_ambigua_por_nombre_409(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        self.ciudad_sql("Cochabamba", "Otro")
        r = self.req("PUT", f"{self.z}/{z}", "GS", json={"ciudad": "cochabamba"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.req("GET", f"{self.z}/{z}", "GS").json()["data"]["id_ciudad"], LA_PAZ)

    # -- eliminar / FK -------------------------------------------------------------------------------
    def test_eliminar_sin_tarifas_200_luego_404_y_se_puede_recrear(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        r = self.req("DELETE", f"{self.z}/{z}", "GS")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), {"status", "data", "message"})
        self.assertIsNone(r.json()["data"])
        self.assertIn("La Paz / Sur", r.json()["message"])
        self.assertEqual(self.req("GET", f"{self.z}/{z}", "GS").status_code, 404)
        self.assertEqual(self.req("DELETE", f"{self.z}/{z}", "GS").status_code, 404)
        self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")  # la cobertura quedo libre

    def test_eliminar_con_tarifas_409_sin_cascada_destructiva(self):
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        t = self.tarifa_sql(z)
        r = self.req("DELETE", f"{self.z}/{z}", "GS")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("tarifa", r.json()["detail"])
        self.assertEqual(self.cuenta("select count(*) from agencia_tarifas where id_tarifa=:t", t=t), 1)  # tarifa intacta
        self.assertEqual(self.req("GET", f"{self.z}/{z}", "GS").json()["data"]["total_tarifas"], 1)
        self.db.execute(text("delete from agencia_tarifas where id_tarifa=:t"), {"t": t})
        self.db.commit()
        self.assertEqual(self.req("DELETE", f"{self.z}/{z}", "GS").status_code, 200)  # sin tarifas ya se puede

    def test_fk_envios_sobre_tarifas_409_de_la_db(self):
        """Aunque el chequeo previo no viera la tarifa, la FK RESTRICT de envios
        la protege y la API responde 409 (no 500)."""
        z = self.zona(id_ciudad=LA_PAZ, nombre_zona="Sur")["id_zona"]
        e = self.envio_con_agencia_en_zona(z)
        self.assertTrue(e)
        r = self.req("DELETE", f"{self.z}/{z}", "GS")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.req("GET", f"{self.z}/{z}", "GS").status_code, 200)

    def envio_con_agencia_en_zona(self, id_zona: int) -> int:
        t = self.tarifa_sql(id_zona)
        u = self.crear_usuario("C")
        v = self.db.execute(text(
            "insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
            "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,10,0,'QR','PAGADO',:k,'DOMICILIO',"
            "'N','c@andes-express.com','1','d','c') returning id_venta"),
            {"u": u, "k": "Z" + self.cuenta("select md5(random()::text)")[:8]}).scalar()
        suc = self.cuenta("select codigo_sucursal from sucursales limit 1")
        e = self.db.execute(text(
            "insert into envios (id_venta,estado,codigo_sucursal,id_agencia,id_tarifa_aplicada,costo_agencia,"
            "peso_kg,volumen_m3) values (:v,'ASIGNADO',:s,:a,:t,10,1,1) returning id_envio"),
            {"v": v, "s": suc, "a": self.ag, "t": t}).scalar()
        self.db.commit()
        return e

    def test_eliminar_agencia_sin_envios_arrastra_sus_zonas_segun_la_migracion(self):
        """Comportamiento de Fase 2/4 (FK CASCADE de agencia_zonas), sin cambios."""
        self.zona(id_ciudad=LA_PAZ)
        self.assertEqual(self.req("GET", f"/{self.ag}", "GS").json()["data"]["total_zonas"], 1)  # ya es real
        self.assertEqual(self.req("DELETE", f"/{self.ag}", "GS").status_code, 200)
        self.assertEqual(self.cuenta("select count(*) from agencia_zonas where id_agencia=:a", a=self.ag), 0)

    def test_agencia_con_envios_sigue_sin_poder_eliminarse_y_conserva_zonas(self):
        z = self.zona(id_ciudad=LA_PAZ)["id_zona"]
        self.envio_con_agencia_en_zona(z)
        self.assertEqual(self.req("DELETE", f"/{self.ag}", "GS").status_code, 409)
        self.assertEqual(self.cuenta("select count(*) from agencia_zonas where id_agencia=:a", a=self.ag), 1)

    # -- contrato de agencias sin cambios ----------------------------------------------------------
    def test_detalle_de_agencia_refleja_total_zonas_y_no_cambia_su_forma(self):
        antes = set(self.req("GET", f"/{self.ag}", "GS").json()["data"])
        self.zona(id_ciudad=LA_PAZ)
        self.zona(id_ciudad=COCHABAMBA)
        d = self.req("GET", f"/{self.ag}", "GS").json()["data"]
        self.assertEqual(d["total_zonas"], 2)
        self.assertEqual(set(d), antes)


if __name__ == "__main__":
    unittest.main()
