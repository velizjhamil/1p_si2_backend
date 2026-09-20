# Integracion CU19 fase 10 (auditoria) - ROBUSTEZ de las entradas de usuario y
# eficiencia de los listados, sobre la BD LOCAL de pruebas (jamas Supabase ni
# Render), con JWT reales y cada test dentro de una transaccion que se revierte.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_robustez.py
#
# Defectos que demostro la auditoria (ambos eran un 500 sin traducir):
#   1. un caracter NUL en un texto libre: PostgreSQL no lo admite en texto y
#      psycopg2 lanzaba ValueError;
#   2. `page` gigante en GET /agencias-reparto: el OFFSET desbordaba bigint.
# Invariante: ninguna entrada hostil produce un 500; siempre 404/409/422.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import event  # noqa: E402

from tests.support.agencias_api import BaseAgenciasAPI, bd_lista, payload  # noqa: E402

NUL = chr(0)                 # el caracter que PostgreSQL no admite en texto
BIG = 99999999999            # > int32
HUGE = 10 ** 30              # > int64
LA_PAZ, COCHABAMBA = 2, 3
TEXTOS_AGENCIA = ("razon_social", "direccion_fiscal", "contacto_operativo", "telefono", "direccion")
CORREOS = ("correo_facturacion", "correo")
TARIFA = {"criterio": "PESO", "rango_min": "0", "rango_max": None, "costo": "5", "vigente_desde": "2020-01-01"}


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class EntradasHostiles(BaseAgenciasAPI):
    def no_500(self, r, permitidos, etiqueta):
        self.assertIn(r.status_code, permitidos, f"{etiqueta}: {r.status_code} {r.text[:120]}")

    # -- caracter NUL ------------------------------------------------------------------------
    def test_nul_en_textos_de_agencia_422_en_alta_y_edicion(self):
        ag = self.crear()["id_agencia"]
        for campo in (*TEXTOS_AGENCIA, *CORREOS, "nit"):
            valor = f"ab{NUL}cd" if campo != "nit" else f"1234{NUL}567"
            r = self.req("POST", "", "GS", json={**payload(nit="7770001112", razon_social="Nul Test"), campo: valor})
            self.no_500(r, (422,), f"POST {campo}")
            r = self.req("PUT", f"/{ag}", "GS", json={campo: valor})
            self.no_500(r, (422,), f"PUT {campo}")
        self.assertEqual(self.req("GET", "", "GS").json()["total"], 1)              # nada se guardo

    def test_nul_al_inicio_y_al_final_tambien(self):
        for valor in (f"{NUL}Andes", f"Andes{NUL}", NUL):
            r = self.req("POST", "", "GS", json=payload(razon_social=valor))
            self.no_500(r, (422,), repr(valor))

    def test_nul_en_zona_y_ciudad(self):
        ag = self.crear()["id_agencia"]
        r = self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": LA_PAZ, "nombre_zona": f"a{NUL}b"})
        self.no_500(r, (422,), "nombre_zona")
        z = self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": LA_PAZ}).json()["data"]["id_zona"]
        self.no_500(self.req("PUT", f"/{ag}/zonas/{z}", "GS", json={"nombre_zona": f"a{NUL}b"}), (422,), "PUT nombre_zona")
        # body: el validador compartido lo rechaza (422). Query: `ciudad` se compara en Python y no llega a la DB -> 404
        self.no_500(self.req("POST", f"/{ag}/zonas", "GS", json={"ciudad": f"La{NUL}Paz"}), (422,), "zona ciudad")
        self.no_500(self.req("GET", "/disponibles", "GS", params={"ciudad": f"La{NUL}Paz"}), (404,), "disponibles ciudad")
        self.no_500(self.req("GET", f"/{ag}/cotizacion", "GS", params={"ciudad": f"La{NUL}Paz", "peso_kg": "1", "volumen_m3": "1"}), (404,), "cotizacion ciudad")

    def test_nul_en_el_filtro_q_422_y_v_c_siguen_recibiendo_403(self):
        self.no_500(self.req("GET", "", "GS", params={"q": f"a{NUL}b"}), (422,), "q")
        self.no_500(self.req("GET", "", "D", params={"q": f"a{NUL}b"}), (422,), "q como D")
        for rol in ("V", "C"):
            self.assertEqual(self.req("GET", "", rol, params={"q": f"a{NUL}b"}).status_code, 403, rol)   # autorizacion primero
        self.assertEqual(self.req("GET", "", headers={}, params={"q": f"a{NUL}b"}).status_code, 401)
        for q in ("%", "_", "ab%", "'; drop table agencias_reparto; --"):                              # comodines/SQL: son texto literal, no error
            self.no_500(self.req("GET", "", "GS", params={"q": q}), (200,), f"q={q}")

    def test_nul_en_observacion_de_la_asignacion_422(self):
        ag = self.crear()["id_agencia"]
        r = self.client.patch("/api/v1/envios/1/asignar", headers=self.headers("GS"),
                              json={"id_agencia": ag, "peso_kg": "1", "volumen_m3": "1", "observacion": f"a{NUL}b"})
        self.no_500(r, (422,), "observacion")

    def test_los_textos_validos_no_cambian(self):
        r = self.req("POST", "", "GS", json=payload(razon_social="  Andes   Ñandú & Cía. S.R.L.  ", telefono="+591 7000-0000", direccion="Av. 6 de Agosto #123"))
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual((d["razon_social"], d["telefono"], d["direccion"]), ("Andes Ñandú & Cía. S.R.L.", "+591 7000-0000", "Av. 6 de Agosto #123"))

    # -- enteros fuera de rango -----------------------------------------------------------------------
    def test_enteros_gigantes_nunca_500(self):
        ag = self.crear()["id_agencia"]
        z = self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": LA_PAZ}).json()["data"]["id_zona"]
        for n in (BIG, HUGE, -BIG, 0, -1):
            ok = (404, 422)
            for etiqueta, metodo, ruta, kw in [
                ("agencia", "GET", f"/{n}", {}), ("agencia PUT", "PUT", f"/{n}", {"json": {"telefono": "1"}}),
                ("estado", "PATCH", f"/{n}/estado", {"json": {"is_active": False}}), ("agencia DELETE", "DELETE", f"/{n}", {}),
                ("zonas", "GET", f"/{n}/zonas", {}), ("zona", "GET", f"/{ag}/zonas/{n}", {}),
                ("zona PUT", "PUT", f"/{ag}/zonas/{n}", {"json": {"nombre_zona": "x"}}), ("zona DELETE", "DELETE", f"/{ag}/zonas/{n}", {}),
                ("zona id_ciudad", "POST", f"/{ag}/zonas", {"json": {"id_ciudad": n}}),
                ("tarifas", "GET", f"/{ag}/zonas/{n}/tarifas", {}), ("tarifa", "GET", f"/{ag}/zonas/{z}/tarifas/{n}", {}),
                ("tarifa POST zona", "POST", f"/{ag}/zonas/{n}/tarifas", {"json": TARIFA}),
                ("cotizacion", "GET", f"/{n}/cotizacion", {"params": {"ciudad": "La Paz", "peso_kg": "1", "volumen_m3": "1"}}),
            ]:
                self.no_500(self.req(metodo, ruta, "GS", **kw), ok if n > 0 or etiqueta.startswith(("zona id", "tarifa POST")) else (404, 422), f"{etiqueta} n={n}")
        for n in (BIG, HUGE):
            r = self.client.patch(f"/api/v1/envios/{n}/asignar", headers=self.headers("GS"), json={"id_agencia": ag, "peso_kg": "1", "volumen_m3": "1"})
            self.no_500(r, (404, 422), f"asignar envio {n}")
            r = self.client.patch("/api/v1/envios/1/asignar", headers=self.headers("GS"), json={"id_agencia": n, "peso_kg": "1", "volumen_m3": "1"})
            self.no_500(r, (404, 422), f"asignar id_agencia {n}")
        self.no_500(self.req("PUT", f"/{ag}/zonas/{z}", "GS", json={"id_ciudad": HUGE}), (404, 422), "PUT zona id_ciudad")

    # -- paginacion ------------------------------------------------------------------------------------
    def test_page_gigante_422_y_el_tope_sigue_dando_pagina_vacia(self):
        self.crear()
        for page in (HUGE, 10 ** 19, 100_001):
            self.no_500(self.req("GET", "", "GS", params={"page": page}), (422,), f"page={page}")
        r = self.req("GET", "", "GS", params={"page": 100_000, "limit": 100})              # el tope es valido y no revienta el OFFSET
        self.assertEqual((r.status_code, r.json()["data"], r.json()["total"]), (200, [], 1))
        for page in (0, -1, "x"):
            self.assertEqual(self.req("GET", "", "GS", params={"page": page}).status_code, 422)
        self.assertEqual(self.req("GET", "", "GS", params={"page": 1, "limit": 100}).status_code, 200)


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ListadosSinNMasUno(BaseAgenciasAPI):
    """Los listados/detalles de CU19 no emiten mas sentencias por tener mas filas."""

    n = 0

    def agencia(self, zonas: int, ciudad=LA_PAZ) -> int:
        ListadosSinNMasUno.n += 1
        ag = self.crear("GS", razon_social=f"Listado {ListadosSinNMasUno.n}", nit=f"4{ListadosSinNMasUno.n:08d}")["id_agencia"]
        for i in range(zonas):
            z = self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": ciudad, "nombre_zona": f"Z{i}"}).json()["data"]["id_zona"]
            self.req("POST", f"/{ag}/zonas/{z}/tarifas", "GS", json=TARIFA)
        return ag

    def sentencias(self, metodo, ruta, rol="GS", **kw) -> int:
        self.req(metodo, ruta, rol, **kw)                                    # calentamiento (objetos expirados tras commits del arnes)
        vistas: list[str] = []
        contar = lambda conn, cur, st, p, ctx, em: vistas.append(st)          # noqa: E731
        event.listen(self.conn, "before_cursor_execute", contar)
        try:
            r = self.req(metodo, ruta, rol, **kw)
        finally:
            event.remove(self.conn, "before_cursor_execute", contar)
        self.assertEqual(r.status_code, 200, r.text)
        return len(vistas)

    def test_listado_y_detalle_de_agencias(self):
        una = self.agencia(1)
        antes = {"lista": self.sentencias("GET", "", params={"limit": 100}), "lista D": self.sentencias("GET", "", "D", params={"limit": 100}),
                 "detalle": self.sentencias("GET", f"/{una}")}
        for _ in range(12):
            self.agencia(3)
        grande = self.agencia(15)
        despues = {"lista": self.sentencias("GET", "", params={"limit": 100}), "lista D": self.sentencias("GET", "", "D", params={"limit": 100}),
                   "detalle": self.sentencias("GET", f"/{grande}")}
        self.assertEqual(antes, despues, "el numero de sentencias crece con las filas")
        self.assertLessEqual(max(despues.values()), 12, despues)

    def test_zonas_y_tarifas(self):
        chica, grande = self.agencia(1), self.agencia(20)
        z_chica = self.req("GET", f"/{chica}/zonas", "GS").json()["data"][0]["id_zona"]
        z_grande = self.req("GET", f"/{grande}/zonas", "GS").json()["data"][0]["id_zona"]
        for etiqueta, ruta_c, ruta_g in [("zonas", f"/{chica}/zonas", f"/{grande}/zonas"),
                                         ("zona", f"/{chica}/zonas/{z_chica}", f"/{grande}/zonas/{z_grande}"),
                                         ("tarifas", f"/{chica}/zonas/{z_chica}/tarifas", f"/{grande}/zonas/{z_grande}/tarifas")]:
            with self.subTest(etiqueta):
                a, b = self.sentencias("GET", ruta_c), self.sentencias("GET", ruta_g)
                self.assertEqual(a, b, f"{etiqueta}: {a} sentencias con 1 zona vs {b} con 20")
                self.assertLessEqual(b, 12)

    def test_listado_de_envios_con_agencia(self):
        """El listado de CU18 precarga la agencia: una consulta por pagina, no por envio."""
        from sqlalchemy import text

        ag = self.agencia(1)
        z, t = self.db.execute(text("select z.id_zona, t.id_tarifa from agencia_zonas z join agencia_tarifas t on t.id_zona=z.id_zona where z.id_agencia=:a"), {"a": ag}).one()
        suc = self.db.execute(text("select codigo_sucursal from sucursales limit 1")).scalar()

        def envios(cuantos):
            for i in range(cuantos):
                ListadosSinNMasUno.n += 1
                cli = self.crear_usuario("C")
                v = self.db.execute(text("insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,nombre_cliente,correo,telefono,direccion,ciudad) values (:u,1,0,'QR','PAGADO',:k,'DOMICILIO','C','c@andes-express.com','1','d','La Paz') returning id_venta"),
                                    {"u": cli, "k": f"LN{ListadosSinNMasUno.n:07d}"}).scalar()
                self.db.execute(text("insert into envios (id_venta,estado,codigo_sucursal,id_agencia,id_tarifa_aplicada,costo_agencia,peso_kg,volumen_m3) values (:v,'ASIGNADO',:s,:a,:t,5,1,1)"),
                                {"v": v, "s": suc, "a": ag, "t": t})
            self.db.commit()

        def consultas_de_agencia():
            vistas: list[str] = []
            contar = lambda conn, cur, st, p, ctx, em: vistas.append(st)      # noqa: E731
            self.client.get("/api/v1/envios", headers=self.headers("GS"), params={"limit": 100})
            event.listen(self.conn, "before_cursor_execute", contar)
            try:
                r = self.client.get("/api/v1/envios", headers=self.headers("GS"), params={"limit": 100})
            finally:
                event.remove(self.conn, "before_cursor_execute", contar)
            self.assertEqual(r.status_code, 200, r.text)
            return len([s for s in vistas if "FROM agencias_reparto" in s]), len(r.json()["data"])

        envios(2)
        c1, n1 = consultas_de_agencia()
        envios(20)
        c2, n2 = consultas_de_agencia()
        self.assertGreater(n2, n1)
        self.assertEqual((c1, c2), (1, 1), "una consulta de agencias por pagina, sin importar cuantos envios")


if __name__ == "__main__":
    unittest.main()
