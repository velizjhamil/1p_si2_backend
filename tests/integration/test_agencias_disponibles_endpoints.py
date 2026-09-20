# Integracion CU19 fase 7 - COMPORTAMIENTO de GET /api/v1/agencias-reparto/disponibles
# sobre la BD LOCAL de pruebas (jamas Supabase ni Render), con JWT reales. La
# matriz de roles vive en test_agencias_disponibles_authz.py.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_disponibles_endpoints.py
#
# Regla verificada: disponible = agencia HABILITADA con al menos una zona en la
# ciudad (con o sin subzona, con o sin tarifas), una sola vez, ordenada por
# lower(razon_social) e id_agencia. Solo disponibilidad: sin tarifas ni costos,
# sin asignar nada.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import event, text  # noqa: E402

from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

LA_PAZ, COCHABAMBA, TARIJA = 2, 3, 7
CAMPOS_AGENCIA = {"id_agencia", "razon_social", "contacto_operativo", "telefono", "is_active",
                  "cubre_ciudad", "cobertura_completa", "zonas_en_ciudad"}
PRIVADOS = ("nit", "correo_facturacion", "direccion_fiscal", "correo", "direccion")
TARIFAS = ("costo", "tarifa", "rango_min", "rango_max", "vigente", "criterio")


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class DisponiblesEndpoints(BaseAgenciasAPI):
    n_agencias = 0

    # -- helpers ------------------------------------------------------------------
    def agencia(self, razon_social, **extra) -> int:
        DisponiblesEndpoints.n_agencias += 1
        nit = f"7{DisponiblesEndpoints.n_agencias:08d}"
        return self.crear("GS", razon_social=razon_social, nit=nit, **extra)["id_agencia"]

    def zona(self, id_agencia, id_ciudad=LA_PAZ, subzona=None) -> int:
        cuerpo = {"id_ciudad": id_ciudad}
        if subzona:
            cuerpo["nombre_zona"] = subzona
        r = self.req("POST", f"/{id_agencia}/zonas", "GS", json=cuerpo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id_zona"]

    def tarifa(self, id_agencia, id_zona, **extra):
        cuerpo = {"criterio": "PESO", "rango_min": "0", "rango_max": "5", "costo": "10", "vigente_desde": "2026-01-01", **extra}
        r = self.req("POST", f"/{id_agencia}/zonas/{id_zona}/tarifas", "GS", json=cuerpo)
        self.assertEqual(r.status_code, 201, r.text)

    def disp(self, ciudad="La Paz", rol="GS"):
        return self.req("GET", "/disponibles", rol, params={"ciudad": ciudad})

    def ids(self, ciudad="La Paz", rol="GS"):
        r = self.disp(ciudad, rol)
        self.assertEqual(r.status_code, 200, r.text)
        return [a["id_agencia"] for a in r.json()["data"]]

    def ciudad_sql(self, nombre, departamento="X") -> int:
        i = self.db.execute(text("insert into ciudades (nombre, departamento) values (:n,:d) returning id"),
                            {"n": nombre, "d": departamento}).scalar()
        self.db.commit()
        return i

    # -- 1-3: cobertura basica ---------------------------------------------------------
    def test_ciudad_con_una_agencia_cubierta(self):
        a = self.agencia("Andes Express", contacto_operativo="Ana Perez", telefono="70000000")
        self.zona(a)
        r = self.disp("La Paz")
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message", "total", "ciudad"})
        self.assertEqual((b["status"], b["total"]), ("success", 1))
        self.assertEqual(b["ciudad"], {"id_ciudad": LA_PAZ, "nombre": "La Paz", "departamento": "La Paz"})
        self.assertIn("La Paz", b["message"])
        (ag,) = b["data"]
        self.assertEqual(set(ag), CAMPOS_AGENCIA)
        self.assertEqual((ag["id_agencia"], ag["razon_social"], ag["contacto_operativo"], ag["telefono"]),
                         (a, "Andes Express", "Ana Perez", "70000000"))
        self.assertEqual((ag["is_active"], ag["cubre_ciudad"]), (True, True))

    def test_ciudad_con_varias_agencias_solo_las_que_la_cubren(self):
        a, b, c = self.agencia("Alfa"), self.agencia("Bravo"), self.agencia("Charlie")
        self.zona(a, LA_PAZ)
        self.zona(b, LA_PAZ)
        self.zona(c, COCHABAMBA)                       # cubre otra ciudad
        self.assertEqual(self.ids("La Paz"), [a, b])
        self.assertEqual(self.ids("Cochabamba"), [c])

    def test_ciudad_sin_agencias_200_lista_vacia(self):
        a = self.agencia("Alfa")
        self.zona(a, LA_PAZ)
        r = self.disp("Tarija")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["data"], r.json()["total"]), ([], 0))
        self.assertEqual(r.json()["ciudad"]["id_ciudad"], TARIJA)
        self.assertIn("No hay agencias", r.json()["message"])

    def test_agencia_sin_zonas_no_aparece(self):
        self.agencia("Sin Zonas")
        self.assertEqual(self.ids("La Paz"), [])

    # -- 4-6: ciudad -----------------------------------------------------------------------
    def test_ciudad_inexistente_404(self):
        self.zona(self.agencia("Alfa"))
        r = self.disp("Atlantida")
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("Atlantida", r.json()["detail"])

    def test_no_aproxima_por_nombres_parecidos(self):
        self.zona(self.agencia("Alfa"))
        for parecido in ("Paz", "La Pa", "La Paz Norte", "El Alto Paz"):
            with self.subTest(parecido):
                self.assertEqual(self.disp(parecido).status_code, 404)

    def test_ciudad_ambigua_409_y_no_devuelve_agencias(self):
        a = self.agencia("Alfa")
        self.zona(a, LA_PAZ)
        dup = self.ciudad_sql("LA  PAZ", "Otro Departamento")
        r = self.disp("la paz")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ambiguo", r.json()["detail"])
        self.assertNotIn("data", r.json())                                # nada por aproximacion
        self.zona(self.agencia("Beta"), dup)
        self.assertEqual(self.disp("La Paz").status_code, 409)             # sigue ambigua aunque ahora haya cobertura en ambas
        self.assertEqual(self.ids("Cochabamba"), [])                       # las no ambiguas no se ven afectadas
        self.assertEqual(self.disp("Cochabamba").status_code, 200)

    def test_nombre_normalizado_mayusculas_tildes_y_espacios(self):
        nueva = self.ciudad_sql("Ñandú Ciudad")
        a = self.agencia("Alfa")
        self.zona(a, nueva)
        b = self.agencia("Beta")
        self.zona(b, LA_PAZ)
        for nombre in ("ñandú ciudad", "NANDU CIUDAD", "  Nandu    Ciudad ", "Ñandú Ciudad"):
            with self.subTest(nombre):
                self.assertEqual(self.ids(nombre), [a])
        for nombre in ("la paz", "LA PAZ", "  La    Paz  "):
            with self.subTest(nombre):
                self.assertEqual(self.ids(nombre), [b])

    def test_ciudad_faltante_o_vacia_422(self):
        self.assertEqual(self.req("GET", "/disponibles", "GS").status_code, 422)
        for vacio in ("", "   "):
            self.assertEqual(self.disp(vacio).status_code, 422, repr(vacio))
        self.assertEqual(self.disp("x" * 101).status_code, 422)

    # -- 7-8: habilitacion --------------------------------------------------------------------
    def test_deshabilitada_no_aparece_y_habilitada_si(self):
        a, b = self.agencia("Alfa"), self.agencia("Bravo")
        self.zona(a)
        self.zona(b)
        self.assertEqual(self.ids(), [a, b])
        self.req("PATCH", f"/{b}/estado", "GS", json={"is_active": False})
        self.assertEqual(self.ids(), [a])
        for rol in ("ASU", "D"):
            self.assertEqual(self.ids(rol=rol), [a], rol)                   # ni siquiera ASU/GS ven historico aqui
        self.req("PATCH", f"/{b}/estado", "GS", json={"is_active": True})
        self.assertEqual(self.ids(), [a, b])
        self.assertEqual(self.req("GET", f"/{b}", "GS").status_code, 200)   # la deshabilitada seguia existiendo

    # -- 9-11: zonas y subzonas ------------------------------------------------------------------
    def test_zona_toda_la_ciudad(self):
        a = self.agencia("Alfa")
        self.zona(a)
        (ag,) = self.disp().json()["data"]
        self.assertEqual((ag["cobertura_completa"], ag["zonas_en_ciudad"]), (True, 1))

    def test_una_subzona_basta_para_cubrir_la_ciudad(self):
        a = self.agencia("Alfa")
        self.zona(a, LA_PAZ, "Sopocachi")
        r = self.disp()
        self.assertEqual([x["id_agencia"] for x in r.json()["data"]], [a])
        (ag,) = r.json()["data"]
        self.assertEqual((ag["cubre_ciudad"], ag["cobertura_completa"], ag["zonas_en_ciudad"]), (True, False, 1))

    def test_varias_zonas_de_la_misma_ciudad_aparece_una_sola_vez(self):
        a = self.agencia("Alfa")
        for sub in ("Sopocachi", "Miraflores", "Calacoto"):
            self.zona(a, LA_PAZ, sub)
        b = self.agencia("Bravo")
        self.zona(b, LA_PAZ, "Centro")
        self.zona(b, LA_PAZ)                                              # toda la ciudad + subzona
        self.zona(b, COCHABAMBA, "Norte")                                 # otra ciudad: no cuenta aqui
        r = self.disp()
        self.assertEqual([x["id_agencia"] for x in r.json()["data"]], [a, b])   # cada una UNA vez
        self.assertEqual(r.json()["total"], 2)
        por_id = {x["id_agencia"]: x for x in r.json()["data"]}
        self.assertEqual((por_id[a]["zonas_en_ciudad"], por_id[a]["cobertura_completa"]), (3, False))
        self.assertEqual((por_id[b]["zonas_en_ciudad"], por_id[b]["cobertura_completa"]), (2, True))

    def test_zona_eliminada_deja_de_cubrir(self):
        a = self.agencia("Alfa")
        z = self.zona(a)
        self.assertEqual(self.ids(), [a])
        self.assertEqual(self.req("DELETE", f"/{a}/zonas/{z}", "GS").status_code, 200)
        self.assertEqual(self.ids(), [])

    def test_la_zona_pertenece_a_la_agencia_que_aparece(self):
        a, b = self.agencia("Alfa"), self.agencia("Bravo")
        self.zona(a, LA_PAZ)
        self.zona(b, COCHABAMBA)
        self.assertEqual(self.ids("La Paz"), [a])                          # b no aparece por la zona de a
        self.assertEqual(self.ids("Cochabamba"), [b])

    # -- 12-14: tarifas y privacidad ------------------------------------------------------------------
    def test_con_y_sin_tarifas_aparecen_y_no_se_calcula_nada(self):
        sin, con = self.agencia("Alfa"), self.agencia("Bravo")
        self.zona(sin)
        z = self.zona(con)
        self.tarifa(con, z)
        self.tarifa(con, z, rango_min="5", rango_max=None, costo="99")
        r = self.disp()
        self.assertEqual([x["id_agencia"] for x in r.json()["data"]], [sin, con])
        for x in r.json()["data"]:
            self.assertEqual(set(x), CAMPOS_AGENCIA)                       # misma forma con o sin tarifas
        for palabra in TARIFAS:
            self.assertNotIn(palabra, r.text)                              # ni costo ni tarifa en la respuesta

    def test_solo_tarifas_inactivas_o_expiradas_igual_aparece(self):
        a = self.agencia("Alfa")
        z = self.zona(a)
        self.tarifa(a, z, is_active=False)
        self.tarifa(a, z, rango_min="5", rango_max="9", vigente_desde="2020-01-01", vigente_hasta="2020-12-31")
        self.assertEqual(self.ids(), [a])

    def test_no_hay_nit_ni_facturacion_en_ningun_rol(self):
        a = self.agencia("Alfa", correo="ops@andes-express.com", direccion="Calle Falsa 123")
        self.zona(a)
        detalle = self.req("GET", f"/{a}", "GS").json()["data"]          # el detalle admin SI los tiene: son los valores a buscar
        secretos = [detalle["nit"], detalle["correo_facturacion"], detalle["direccion_fiscal"], "ops@andes-express.com", "Calle Falsa 123"]
        self.assertTrue(all(secretos))
        for rol in ("ASU", "GS", "D"):
            r = self.disp(rol=rol)
            (ag,) = r.json()["data"]
            for campo in PRIVADOS:
                self.assertNotIn(campo, ag, (rol, campo))
                self.assertNotIn(f'"{campo}"', r.text, (rol, campo))
            for valor in secretos:
                self.assertNotIn(valor, r.text, (rol, valor))

    # -- 24: orden -----------------------------------------------------------------------------------------
    def test_orden_determinista_por_razon_social_sin_distinguir_mayusculas(self):
        creadas = {}
        for nombre in ("delta", "Alfa", "charlie", "BRAVO", "echo", "Bravo Norte"):    # insertadas desordenadas
            creadas[nombre] = self.agencia(nombre)
            self.zona(creadas[nombre])
        esperado = [creadas[n] for n in ("Alfa", "BRAVO", "Bravo Norte", "charlie", "delta", "echo")]
        primera = self.ids()
        self.assertEqual(primera, esperado)
        self.assertEqual([self.ids() for _ in range(3)], [primera] * 3)                  # estable entre llamadas
        self.assertEqual(self.ids(rol="D"), primera)                                     # y entre roles
        self.assertEqual(self.ids("  la   paz "), primera)                               # y entre formas del nombre
        r = self.disp()
        self.assertEqual([a["razon_social"] for a in r.json()["data"]],
                         ["Alfa", "BRAVO", "Bravo Norte", "charlie", "delta", "echo"])

    # -- 25: N+1 -------------------------------------------------------------------------------------------------
    def sentencias(self, ciudad="La Paz") -> int:
        """Sentencias SQL emitidas por UNA llamada al endpoint (el listener se
        instala sobre la conexion que usa la sesion del test)."""
        vistas: list[str] = []

        def contar(conn, cursor, statement, parameters, context, executemany):
            vistas.append(statement)

        self.disp(ciudad, "D")            # calentamiento: tras un commit la sesion recarga objetos expirados (ruido del arnes)
        event.listen(self.conn, "before_cursor_execute", contar)
        try:
            r = self.disp(ciudad, "D")
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            event.remove(self.conn, "before_cursor_execute", contar)
        self.vistas = vistas
        return len(vistas)

    def test_no_hay_n_mas_uno_las_sentencias_no_crecen_con_las_agencias(self):
        a = self.agencia("Agencia 0")
        self.zona(a, LA_PAZ, "Sur")
        self.zona(a, LA_PAZ, "Norte")
        con_1 = self.sentencias()
        conteo_1 = len(self.disp().json()["data"])
        for i in range(1, 30):
            ag = self.agencia(f"Agencia {i:02d}")
            self.zona(ag, LA_PAZ, "Centro")
            self.zona(ag, LA_PAZ, "Otra")
            self.tarifa(ag, self.zona(ag, COCHABAMBA))
        con_30 = self.sentencias()
        self.assertEqual((conteo_1, len(self.disp().json()["data"])), (1, 30))
        self.assertEqual(con_1, con_30, f"las sentencias crecen con las agencias: {con_1} -> {con_30}")
        self.assertLessEqual(con_30, 8, self.vistas)                                    # constante y chico
        consultas_a_agencias = [s for s in self.vistas if "FROM agencias_reparto" in s]
        self.assertEqual(len(consultas_a_agencias), 1, consultas_a_agencias)             # UNA sola consulta a agencias (la auth usa usuarios/roles)
        self.assertTrue(any("GROUP BY agencia_zonas.id_agencia" in s for s in self.vistas))
        self.assertFalse([s for s in self.vistas if "FROM agencia_zonas" in s and "GROUP BY" not in s and "agencias_reparto" not in s],
                         "hay consultas de zonas por separado (N+1)")

    # -- ruta y solo lectura ----------------------------------------------------------------------------------
    def test_la_ruta_no_se_confunde_con_el_detalle_por_id(self):
        r = self.req("GET", "/disponibles", "GS", params={"ciudad": "La Paz"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("ciudad", r.json())                                   # es /disponibles, no /{id_agencia}
        self.assertEqual(self.req("GET", "/999999", "GS").status_code, 404)  # el detalle por id sigue igual
        self.assertEqual(self.req("GET", "/abc", "GS").status_code, 422)

    def test_es_solo_lectura_no_cambia_ninguna_tabla(self):
        a = self.agencia("Alfa")
        z = self.zona(a)
        self.tarifa(a, z)
        tablas = ("agencias_reparto", "agencia_zonas", "agencia_tarifas", "envios", "ventas", "ciudades")
        antes = {t: self.db.execute(text(f"select count(*) from {t}")).scalar() for t in tablas}
        for _ in range(3):
            self.disp()
            self.disp("Cochabamba", "D")
        self.assertEqual(antes, {t: self.db.execute(text(f"select count(*) from {t}")).scalar() for t in tablas})
        self.assertEqual(self.db.execute(text("select count(*) from envios where id_agencia is not null")).scalar(), 0)


if __name__ == "__main__":
    unittest.main()
