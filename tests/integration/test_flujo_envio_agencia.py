# Integracion CU19 - FLUJO DE ESTADOS de un envio asignado a AGENCIA, sobre la BD
# LOCAL de pruebas (jamas Supabase ni Render), con JWT reales y cada test dentro de
# una transaccion que siempre se revierte.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_flujo_envio_agencia.py
#
# Decision funcional congelada:
#   - envio con agencia (sin repartidor): ASU/GS lo llevan ASIGNADO -> EN_RUTA ->
#     ENTREGADO / INTENTO_FALLIDO (-> REPROGRAMADO -> EN_RUTA ...);
#   - D NO opera ni ve esos envios; V/C tampoco;
#   - envio con repartidor: flujo de CU18 sin cambios (D asignado + ASU/GS);
#   - agencia y repartidor nunca coexisten;
#   - `transiciones_permitidas` ofrece EXACTAMENTE lo que el backend acepta.
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import jwt  # noqa: E402

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import text  # noqa: E402

from app.core.security import ALGORITHM, SECRET_KEY, crear_token_acceso  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

ENVIOS = "/api/v1/envios"
ESTADOS = ("PREPARANDO", "LISTO_ENVIO", "ASIGNADO", "EN_RUTA", "INTENTO_FALLIDO", "REPROGRAMADO", "ENTREGADO", "CANCELADO")
DESTINOS = ("LISTO_ENVIO", "ASIGNADO", "EN_RUTA", "ENTREGADO", "CANCELADO", "INTENTO_FALLIDO", "REPROGRAMADO")
ROLES = ("ASU", "GS", "D", "V", "C")


def futuro(dias=3):
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class FlujoEnvioAgencia(BaseAgenciasAPI):
    n = 0

    # -- fixtures ---------------------------------------------------------------------------
    def setUp(self):
        super().setUp()
        FlujoEnvioAgencia.n += 1
        self.suc = self.db.execute(text("select codigo_sucursal from sucursales limit 1")).scalar()
        self.d = self.crear_usuario("D")                          # repartidor propio
        self.d_otro = self.crear_usuario("D")                     # otro D sin envios
        ag = self.crear("GS", razon_social=f"Flujo Agencia {FlujoEnvioAgencia.n}", nit=f"6{FlujoEnvioAgencia.n:08d}")["id_agencia"]
        z = self.req("POST", f"/{ag}/zonas", "GS", json={"id_ciudad": 2}).json()["data"]["id_zona"]
        t = self.req("POST", f"/{ag}/zonas/{z}/tarifas", "GS", json={
            "criterio": "PESO", "rango_min": "0", "rango_max": None, "costo": "10", "vigente_desde": "2020-01-01"}).json()["data"]["id_tarifa"]
        self.ag, self.tarifa = ag, t

    def envio(self, estado="LISTO_ENVIO", transportista=None):
        """Envio real en `estado` con transportista 'agencia' | 'repartidor' | None. -> (id_envio, id_cliente)."""
        FlujoEnvioAgencia.n += 1
        cliente = self.crear_usuario("C")
        v = self.db.execute(text(
            "insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
            "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,100,25,'QR','PAGADO',:k,'DOMICILIO',"
            "'Cliente','c@andes-express.com','1','Calle 1','La Paz') returning id_venta"),
            {"u": cliente, "k": f"FL{FlujoEnvioAgencia.n:06d}{uuid.uuid4().hex[:5]}"}).scalar()
        cols = {"v": v, "e": estado, "s": self.suc}
        extra_c, extra_v = "", ""
        if transportista == "agencia":
            extra_c = ",id_agencia,id_tarifa_aplicada,costo_agencia,peso_kg,volumen_m3"
            extra_v = ",:a,:t,10,2,1"
            cols.update(a=self.ag, t=self.tarifa)
        elif transportista == "repartidor":
            extra_c, extra_v = ",id_repartidor", ",:d"
            cols["d"] = self.d
        e = self.db.execute(text(f"insert into envios (id_venta,estado,codigo_sucursal{extra_c}) values (:v,:e,:s{extra_v}) returning id_envio"), cols).scalar()
        self.db.execute(text("insert into envio_historial (id_envio, estado_nuevo, observacion) values (:e,:est,'inicio')"), {"e": e, "est": estado})
        self.db.commit()
        return e, cliente

    def token(self, rol, envio_cliente=None, envio_repartidor=False):
        if rol == "C":
            uid = envio_cliente
        elif rol == "D":
            uid = self.d if envio_repartidor else self.d_otro
        else:
            uid = self._usuarios.get(rol) or self.crear_usuario(rol)
            self._usuarios[rol] = uid
        return {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': rol})}"}

    def _h(self, rol, headers):
        """`headers={}` significa SIN credenciales (no se cae al rol por defecto)."""
        return self.headers(rol) if headers is None else headers

    def estado(self, e, rol="GS", **kw):
        headers = kw.pop("headers", None)
        return self.client.patch(f"{ENVIOS}/{e}/estado", headers=self._h(rol, headers), json=kw)

    def fallo(self, e, rol="GS", headers=None):
        return self.client.patch(f"{ENVIOS}/{e}/intento-fallido", headers=self._h(rol, headers), json={"motivo": "cliente ausente"})

    def reprogramar(self, e, rol="GS", headers=None):
        return self.client.patch(f"{ENVIOS}/{e}/reprogramar", headers=self._h(rol, headers), json={"nueva_fecha_entrega": futuro()})

    def fila(self, e):
        self.db.expire_all()
        return tuple(self.db.execute(text("select estado, id_agencia, id_repartidor, intentos_fallidos, fecha_entrega_real is not null from envios where id_envio=:e"), {"e": e}).one())

    def historial(self, e):
        self.db.expire_all()
        return [tuple(r) for r in self.db.execute(text(
            "select estado_anterior, estado_nuevo, id_usuario, observacion from envio_historial where id_envio=:e order by id_historial"), {"e": e})]

    def nunca_ambos(self, e):
        self.db.expire_all()
        self.assertEqual(self.db.execute(text("select count(*) from envios where id_envio=:e and id_agencia is not null and id_repartidor is not null"), {"e": e}).scalar(), 0)

    # -- ASIGNADO -> EN_RUTA con agencia --------------------------------------------------------
    def test_asu_y_gs_despachan_un_envio_con_agencia(self):
        for rol in ("ASU", "GS"):
            e, _ = self.envio("ASIGNADO", "agencia")
            r = self.estado(e, rol, estado="EN_RUTA", observacion="sale con la agencia")
            self.assertEqual(r.status_code, 200, (rol, r.text))
            d = r.json()["data"]
            self.assertEqual((d["estado"], d["agencia_id"], d["repartidor_id"], d["transiciones_permitidas"]), ("EN_RUTA", self.ag, None, ["ENTREGADO", "INTENTO_FALLIDO"]))
            self.assertEqual(self.fila(e)[:3], ("EN_RUTA", self.ag, None))
            h = self.historial(e)[-1]
            self.assertEqual((h[0], h[1], h[3]), ("ASIGNADO", "EN_RUTA", "sale con la agencia"))
            self.assertEqual(str(h[2]), str(self._usuarios[rol]))                 # el responsable del cambio queda registrado

    def test_d_v_c_no_pueden_despachar_un_envio_con_agencia(self):
        e, cliente = self.envio("ASIGNADO", "agencia")
        for rol in ("D", "V", "C"):
            h = self.token(rol, envio_cliente=cliente)
            r = self.estado(e, rol, headers=h, estado="EN_RUTA")
            with self.subTest(rol=rol):
                self.assertEqual(r.status_code, 403, (rol, r.text))
        self.assertEqual(self.fila(e)[:3], ("ASIGNADO", self.ag, None))
        self.assertEqual(len(self.historial(e)), 1)                               # nada se escribio

    def test_d_no_ve_ni_opera_envios_con_agencia(self):
        e, _ = self.envio("ASIGNADO", "agencia")
        h = self.token("D")
        self.assertEqual(self.client.get(f"{ENVIOS}/{e}", headers=h).status_code, 403)
        self.assertEqual(self.client.get(f"{ENVIOS}/{e}/historial", headers=h).status_code, 403)
        lista = self.client.get(ENVIOS, headers=h, params={"limit": 100}).json()["data"]
        self.assertNotIn(e, [x["id_envio"] for x in lista])
        for r in (self.estado(e, headers=h, estado="EN_RUTA"), self.fallo(e, headers=h), self.reprogramar(e, headers=h)):
            self.assertEqual(r.status_code, 403, r.text)

    def test_sin_token_y_tokens_invalidos_401(self):
        e, _ = self.envio("ASIGNADO", "agencia")
        uid = str(self.crear_usuario("GS"))
        malos = {"basura": "no-es-un-jwt",
                 "firma_ajena": jwt.encode({"sub": uid, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "otra-clave-secreta-de-32-bytes-min!!", algorithm=ALGORITHM),
                 "expirado": jwt.encode({"sub": uid, "exp": datetime.now(timezone.utc) - timedelta(hours=1)}, SECRET_KEY, algorithm=ALGORITHM),
                 "usuario_inexistente": crear_token_acceso({"sub": str(uuid.uuid4()), "rol": "GS"})}
        self.assertEqual(self.estado(e, headers={}, estado="EN_RUTA").status_code, 401)
        for tipo, token in malos.items():
            with self.subTest(token=tipo):
                self.assertEqual(self.estado(e, headers={"Authorization": f"Bearer {token}"}, estado="EN_RUTA").status_code, 401)
        self.assertEqual(self.fila(e)[0], "ASIGNADO")

    def test_usuario_inactivo_y_claim_manipulado(self):
        e, _ = self.envio("ASIGNADO", "agencia")
        inactivo = self.crear_usuario("GS", activo=False)
        self.assertEqual(self.estado(e, headers={"Authorization": f"Bearer {crear_token_acceso({'sub': str(inactivo), 'rol': 'GS'})}"}, estado="EN_RUTA").status_code, 403)
        for real, declarado in (("D", "ASU"), ("D", "GS"), ("V", "GS"), ("C", "ASU")):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': declarado})}"}
            with self.subTest(real=real, declarado=declarado):
                self.assertEqual(self.estado(e, headers=h, estado="EN_RUTA").status_code, 403)
        self.assertEqual(self.fila(e)[0], "ASIGNADO")

    # -- EN_RUTA -> ENTREGADO / INTENTO_FALLIDO --------------------------------------------------
    def test_en_ruta_a_entregado_con_agencia(self):
        for rol in ("ASU", "GS"):
            e, cliente = self.envio("EN_RUTA", "agencia")
            r = self.estado(e, rol, estado="ENTREGADO", observacion="recibio Ana")
            self.assertEqual(r.status_code, 200, (rol, r.text))
            d = r.json()["data"]
            self.assertEqual((d["estado"], d["transiciones_permitidas"], d["agencia_id"]), ("ENTREGADO", [], self.ag))
            self.assertEqual(self.fila(e), ("ENTREGADO", self.ag, None, 0, True))       # fecha_entrega_real registrada
            self.assertEqual(self.historial(e)[-1][:2], ("EN_RUTA", "ENTREGADO"))
            self.assertEqual(self.estado(e, rol, estado="EN_RUTA").status_code, 409)     # terminal: sin mas cambios

    def test_en_ruta_a_intento_fallido_con_agencia(self):
        for rol in ("ASU", "GS"):
            e, _ = self.envio("EN_RUTA", "agencia")
            r = self.fallo(e, rol)
            self.assertEqual(r.status_code, 200, (rol, r.text))
            d = r.json()["data"]
            self.assertEqual((d["estado"], d["intentos_fallidos"], d["motivo_fallo"], d["transiciones_permitidas"]), ("INTENTO_FALLIDO", 1, "cliente ausente", ["REPROGRAMADO", "CANCELADO"]))
            h = self.historial(e)[-1]
            self.assertEqual((h[0], h[1]), ("EN_RUTA", "INTENTO_FALLIDO"))
            self.assertIn("cliente ausente", h[3])

    def test_d_v_c_no_avanzan_un_envio_en_ruta_con_agencia(self):
        e, cliente = self.envio("EN_RUTA", "agencia")
        for rol in ("D", "V", "C"):
            h = self.token(rol, envio_cliente=cliente)
            with self.subTest(rol=rol):
                self.assertEqual(self.estado(e, headers=h, estado="ENTREGADO").status_code, 403)
                self.assertEqual(self.fallo(e, headers=h).status_code, 403)
        self.assertEqual(self.fila(e)[0], "EN_RUTA")
        self.assertEqual(len(self.historial(e)), 1)

    def test_ciclo_completo_con_agencia_y_su_historial(self):
        e, cliente = self.envio("LISTO_ENVIO")
        r = self.client.patch(f"{ENVIOS}/{e}/asignar", headers=self.headers("GS"), json={"id_agencia": self.ag, "peso_kg": "2", "volumen_m3": "1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.estado(e, "GS", estado="EN_RUTA").status_code, 200)
        self.assertEqual(self.fallo(e, "ASU").status_code, 200)
        self.assertEqual(self.reprogramar(e, "GS").status_code, 200)
        self.assertEqual(self.estado(e, "ASU", estado="EN_RUTA").status_code, 200)             # reprogramado vuelve a salir
        self.assertEqual(self.estado(e, "GS", estado="ENTREGADO").status_code, 200)
        self.assertEqual([h[:2] for h in self.historial(e)], [
            (None, "LISTO_ENVIO"), ("LISTO_ENVIO", "ASIGNADO"), ("ASIGNADO", "EN_RUTA"), ("EN_RUTA", "INTENTO_FALLIDO"),
            ("INTENTO_FALLIDO", "REPROGRAMADO"), ("REPROGRAMADO", "EN_RUTA"), ("EN_RUTA", "ENTREGADO")])
        self.assertTrue(all(h[2] is not None for h in self.historial(e)[1:]))                # cada cambio tiene responsable
        self.assertEqual(self.fila(e), ("ENTREGADO", self.ag, None, 1, True))
        self.nunca_ambos(e)
        # el cliente recibio los avisos de CU10 (en camino, fallido, reprogramado, en camino, entregado)
        titulos = [r[0] for r in self.db.execute(text("select titulo from notificaciones where id_usuario=:u and referencia_id=:e order by 1"), {"u": cliente, "e": str(e)})]
        self.assertEqual(sorted(titulos), sorted(["Pedido en camino", "Intento de entrega fallido", "Entrega reprogramada", "Pedido en camino", "Pedido entregado"]))

    def test_costo_interno_no_llega_al_cliente_durante_el_flujo(self):
        e, cliente = self.envio("ASIGNADO", "agencia")
        self.estado(e, "GS", estado="EN_RUTA")
        cli = self.token("C", envio_cliente=cliente)
        for ruta in (f"{ENVIOS}/{e}", f"{ENVIOS}/{e}/historial"):
            r = self.client.get(ruta, headers=cli)
            self.assertEqual(r.status_code, 200, r.text)
            for interno in ("costo_agencia", "id_tarifa_aplicada", "peso_kg", "volumen_m3"):
                self.assertNotIn(f'"{interno}"', r.text)
        self.assertEqual(self.client.get(f"{ENVIOS}/{e}", headers=cli).json()["data"]["transiciones_permitidas"], [])

    # -- guardas ------------------------------------------------------------------------------------
    def test_envio_sin_transportista_sigue_sin_poder_despacharse(self):
        e, _ = self.envio("ASIGNADO", None)                       # ni repartidor ni agencia: no ofrecible ni aceptable
        d = self.client.get(f"{ENVIOS}/{e}", headers=self.headers("GS")).json()["data"]
        self.assertEqual(d["transiciones_permitidas"], ["CANCELADO"])
        r = self.estado(e, "GS", estado="EN_RUTA")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("sin repartidor ni agencia", r.json()["detail"])
        self.assertEqual(self.fila(e)[0], "ASIGNADO")

    def test_transiciones_invalidas_siguen_siendo_409(self):
        for estado, destino in (("ASIGNADO", "ENTREGADO"), ("EN_RUTA", "EN_RUTA"), ("ENTREGADO", "EN_RUTA"), ("CANCELADO", "EN_RUTA")):
            e, _ = self.envio(estado, "agencia")
            with self.subTest(estado=estado, destino=destino):
                self.assertEqual(self.estado(e, "GS", estado=destino).status_code, 409)
        e, _ = self.envio("ASIGNADO", "agencia")
        self.assertEqual(self.fallo(e, "GS").status_code, 409)                              # INTENTO_FALLIDO solo desde EN_RUTA
        self.assertEqual(self.reprogramar(e, "GS").status_code, 409)

    # -- exclusion agencia / repartidor ------------------------------------------------------------------
    def test_no_se_asigna_repartidor_a_un_envio_con_agencia_ni_al_reves(self):
        e, _ = self.envio("ASIGNADO", "agencia")
        r = self.client.patch(f"{ENVIOS}/{e}/asignar", headers=self.headers("GS"), json={"id_repartidor": str(self.d)})
        self.assertEqual(r.status_code, 409)                                                # ya ASIGNADO
        forzado, _ = self.envio("LISTO_ENVIO", "agencia")                                    # inconsistente forzado: LISTO con agencia
        r = self.client.patch(f"{ENVIOS}/{forzado}/asignar", headers=self.headers("GS"), json={"id_repartidor": str(self.d)})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("agencia", r.json()["detail"])
        r = self.client.patch(f"{ENVIOS}/{forzado}/asignar", headers=self.headers("GS"), json={"id_agencia": self.ag, "peso_kg": "1", "volumen_m3": "1"})
        self.assertEqual(r.status_code, 409, r.text)
        e2, _ = self.envio("ASIGNADO", "repartidor")
        r = self.client.patch(f"{ENVIOS}/{e2}/asignar", headers=self.headers("GS"), json={"id_agencia": self.ag, "peso_kg": "1", "volumen_m3": "1"})
        self.assertEqual(r.status_code, 409)
        for x in (e, forzado, e2):
            self.nunca_ambos(x)
        self.assertEqual(self.fila(e2)[1:3], (None, self.d))

    def test_la_db_impide_ambos_a_la_vez_en_cualquier_estado(self):
        e, _ = self.envio("EN_RUTA", "agencia")
        with self.assertRaises(Exception) as cm:
            self.db.execute(text("update envios set id_repartidor=:d where id_envio=:e"), {"d": self.d, "e": e})
        self.assertIn("ck_envios_repartidor_o_agencia", str(cm.exception))
        self.db.rollback()

    def test_ninguna_transicion_altera_el_transportista(self):
        for transportista in ("agencia", "repartidor"):
            e, _ = self.envio("ASIGNADO", transportista)
            esperado = (self.ag, None) if transportista == "agencia" else (None, self.d)
            for paso in (lambda: self.estado(e, "GS", estado="EN_RUTA"), lambda: self.fallo(e, "GS"), lambda: self.reprogramar(e, "GS"),
                         lambda: self.estado(e, "GS", estado="EN_RUTA"), lambda: self.estado(e, "GS", estado="ENTREGADO")):
                self.assertEqual(paso().status_code, 200)
                self.db.expire_all()
                self.assertEqual(self.fila(e)[1:3], esperado)
                self.nunca_ambos(e)

    # -- CU18 con repartidor: regresion completa ----------------------------------------------------------
    def test_flujo_completo_con_repartidor_operado_por_d(self):
        e, _ = self.envio("ASIGNADO", "repartidor")
        h = self.token("D", envio_repartidor=True)
        self.assertEqual(self.client.get(f"{ENVIOS}/{e}", headers=h).json()["data"]["transiciones_permitidas"], ["EN_RUTA"])
        self.assertEqual(self.estado(e, headers=h, estado="EN_RUTA").status_code, 200)
        self.assertEqual(self.fallo(e, headers=h).status_code, 200)
        self.assertEqual(self.reprogramar(e, headers=h).status_code, 200)
        self.assertEqual(self.estado(e, headers=h, estado="EN_RUTA").status_code, 200)
        self.assertEqual(self.estado(e, headers=h, estado="ENTREGADO").status_code, 200)
        self.assertEqual(self.fila(e), ("ENTREGADO", None, self.d, 1, True))
        self.assertEqual([x[:2] for x in self.historial(e)][1:], [("ASIGNADO", "EN_RUTA"), ("EN_RUTA", "INTENTO_FALLIDO"),
                         ("INTENTO_FALLIDO", "REPROGRAMADO"), ("REPROGRAMADO", "EN_RUTA"), ("EN_RUTA", "ENTREGADO")])
        self.assertTrue(all(str(x[2]) == str(self.d) for x in self.historial(e)[1:]))

    def test_repartidor_tambien_lo_pueden_operar_asu_y_gs_y_solo_el_d_asignado(self):
        e, _ = self.envio("ASIGNADO", "repartidor")
        self.assertEqual(self.estado(e, headers=self.token("D"), estado="EN_RUTA").status_code, 403)        # otro D
        self.assertEqual(self.estado(e, "GS", estado="EN_RUTA").status_code, 200)
        e2, _ = self.envio("EN_RUTA", "repartidor")
        self.assertEqual(self.estado(e2, "ASU", estado="ENTREGADO").status_code, 200)

    def test_d_no_puede_cancelar_ni_preparar_ni_asignar(self):
        e, _ = self.envio("ASIGNADO", "repartidor")
        h = self.token("D", envio_repartidor=True)
        self.assertEqual(self.estado(e, headers=h, estado="CANCELADO", observacion="x motivo").status_code, 403)
        self.assertEqual(self.client.patch(f"{ENVIOS}/{e}/confirmar-preparacion", headers=h, json={"codigo_sucursal": self.suc}).status_code, 403)
        self.assertEqual(self.client.patch(f"{ENVIOS}/{e}/asignar", headers=h, json={"id_repartidor": str(self.d)}).status_code, 403)

    # -- cancelacion: reglas sin cambios ---------------------------------------------------------------------------
    def test_cancelacion_igual_con_y_sin_agencia(self):
        for transportista in (None, "agencia", "repartidor"):
            for estado in ("PREPARANDO", "LISTO_ENVIO", "ASIGNADO", "INTENTO_FALLIDO", "REPROGRAMADO"):
                if estado in ("PREPARANDO", "LISTO_ENVIO") and transportista:
                    continue
                if estado in ("ASIGNADO", "INTENTO_FALLIDO", "REPROGRAMADO") and not transportista:
                    continue
                e, _ = self.envio(estado, transportista)
                r = self.estado(e, "GS", estado="CANCELADO", observacion="cliente desistio")
                with self.subTest(estado=estado, transportista=transportista):
                    self.assertEqual(r.status_code, 200, r.text)
                    self.assertEqual(self.fila(e)[0], "CANCELADO")
                    self.assertEqual(self.historial(e)[-1][:2], (estado, "CANCELADO"))
                    if transportista == "agencia":
                        self.assertEqual(self.fila(e)[1], self.ag)                         # el snapshot se conserva
        for estado in ("EN_RUTA", "ENTREGADO", "CANCELADO"):
            e, _ = self.envio(estado, "agencia")
            self.assertEqual(self.estado(e, "GS", estado="CANCELADO", observacion="motivo").status_code, 409, estado)
        e, _ = self.envio("ASIGNADO", "agencia")
        self.assertEqual(self.estado(e, "GS", estado="CANCELADO").status_code, 422)          # exige motivo
        self.assertEqual(self.estado(e, headers=self.token("D"), estado="CANCELADO", observacion="motivo").status_code, 403)

    # -- coherencia: lo ofrecido == lo aceptado ----------------------------------------------------------------------
    def probar(self, destino, e, headers):
        if destino == "LISTO_ENVIO":
            return self.client.patch(f"{ENVIOS}/{e}/confirmar-preparacion", headers=headers, json={"codigo_sucursal": self.suc})
        if destino == "ASIGNADO":
            return self.client.patch(f"{ENVIOS}/{e}/asignar", headers=headers, json={"id_repartidor": str(self.d_otro)})
        if destino == "INTENTO_FALLIDO":
            return self.client.patch(f"{ENVIOS}/{e}/intento-fallido", headers=headers, json={"motivo": "cliente ausente"})
        if destino == "REPROGRAMADO":
            return self.client.patch(f"{ENVIOS}/{e}/reprogramar", headers=headers, json={"nueva_fecha_entrega": futuro()})
        cuerpo = {"estado": destino, **({"observacion": "prueba de cancelacion"} if destino == "CANCELADO" else {})}
        return self.client.patch(f"{ENVIOS}/{e}/estado", headers=headers, json=cuerpo)

    def test_transiciones_permitidas_coincide_con_lo_que_acepta_el_backend(self):
        combos = []
        for estado in ESTADOS:
            transportistas = (None,) if estado in ("PREPARANDO", "LISTO_ENVIO") else ("agencia", "repartidor")
            combos += [(estado, t) for t in transportistas]
        sondeos = 0
        for estado, transportista in combos:
            for rol in ROLES:
                e, cliente = self.envio(estado, transportista)
                h = self.token(rol, envio_cliente=cliente, envio_repartidor=(transportista == "repartidor"))
                r = self.client.get(f"{ENVIOS}/{e}", headers=h)
                ofrecido = set(r.json()["data"]["transiciones_permitidas"]) if r.status_code == 200 else set()
                aceptado = set()
                for destino in DESTINOS:
                    fresco, _c = self.envio(estado, transportista)                 # cada sondeo sobre un envio identico y nuevo
                    hh = h if rol != "C" else self.token("C", envio_cliente=_c)
                    if self.probar(destino, fresco, hh).status_code == 200:
                        aceptado.add(destino)
                    sondeos += 1
                with self.subTest(estado=estado, transportista=transportista, rol=rol):
                    self.assertEqual(ofrecido, aceptado, f"UI ofrece {sorted(ofrecido)} pero el backend acepta {sorted(aceptado)}")
        self.assertGreater(sondeos, 400)

    def test_lo_que_se_ofrece_para_agencia_es_exactamente_la_decision_congelada(self):
        esperado = {"ASIGNADO": ["EN_RUTA", "CANCELADO"], "EN_RUTA": ["ENTREGADO", "INTENTO_FALLIDO"],
                    "INTENTO_FALLIDO": ["REPROGRAMADO", "CANCELADO"], "REPROGRAMADO": ["EN_RUTA", "CANCELADO"],
                    "ENTREGADO": [], "CANCELADO": []}
        for estado, lista in esperado.items():
            e, cliente = self.envio(estado, "agencia")
            for rol in ("ASU", "GS"):
                d = self.client.get(f"{ENVIOS}/{e}", headers=self.headers(rol)).json()["data"]
                self.assertEqual(d["transiciones_permitidas"], lista, (estado, rol))
            for rol in ("D", "V"):
                self.assertEqual(self.client.get(f"{ENVIOS}/{e}", headers=self.token(rol)).status_code, 403, (estado, rol))
            cli = self.client.get(f"{ENVIOS}/{e}", headers=self.token("C", envio_cliente=cliente)).json()["data"]
            self.assertEqual(cli["transiciones_permitidas"], [], estado)

    def test_listado_ofrece_las_mismas_transiciones_que_el_detalle(self):
        e, _ = self.envio("ASIGNADO", "agencia")
        lista = self.client.get(ENVIOS, headers=self.headers("GS"), params={"limit": 100}).json()["data"]
        (x,) = [i for i in lista if i["id_envio"] == e]
        self.assertEqual(x["transiciones_permitidas"], ["EN_RUTA", "CANCELADO"])
        self.assertEqual(x["agencia_id"], self.ag)
        self.assertTrue(x["agencia_nombre"].startswith("Flujo Agencia"))


if __name__ == "__main__":
    unittest.main()
