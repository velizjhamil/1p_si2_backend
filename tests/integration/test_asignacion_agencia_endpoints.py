# Integracion CU19 fase 9 - ASIGNACION de agencia a un envio:
# PATCH /api/v1/envios/{id}/asignar con `id_agencia` (endpoint EXISTENTE de CU18,
# ampliado). BD LOCAL de pruebas (jamas Supabase ni Render), JWT reales, cada test
# dentro de una transaccion que siempre se revierte.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_asignacion_agencia_endpoints.py
#
# La SELECCION de tarifa es la de Fase 8 (mayor costo -> PESO -> ciudad completa ->
# 409); aqui se comprueba que la asignacion guarda exactamente lo que ella elige.
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import jwt  # noqa: E402

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

from sqlalchemy import event, text  # noqa: E402

from app.core.security import ALGORITHM, SECRET_KEY, crear_token_acceso  # noqa: E402
from tests.support.agencias_api import BaseAgenciasAPI, bd_lista  # noqa: E402

LA_PAZ, COCHABAMBA = 2, 3
ENVIOS = "/api/v1/envios"
INTERNOS = ("costo_agencia", "id_tarifa_aplicada", "peso_kg", "volumen_m3")


def hoy():
    return datetime.now(timezone.utc).date()


def dia(delta):
    return (hoy() + timedelta(days=delta)).isoformat()


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class AsignacionAgencia(BaseAgenciasAPI):
    n_ag = 0
    n_venta = 0

    # -- helpers ------------------------------------------------------------------------
    def agencia(self, nombre="Andes Express") -> int:
        AsignacionAgencia.n_ag += 1
        return self.crear("GS", razon_social=nombre, nit=f"9{AsignacionAgencia.n_ag:08d}")["id_agencia"]

    def zona(self, ag, id_ciudad=LA_PAZ, subzona=None) -> int:
        cuerpo = {"id_ciudad": id_ciudad, **({"nombre_zona": subzona} if subzona else {})}
        r = self.req("POST", f"/{ag}/zonas", "GS", json=cuerpo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id_zona"]

    def tarifa(self, ag, z, criterio="PESO", rmin="0", rmax="5", costo="10", desde="2020-01-01", hasta=None, activa=True) -> int:
        r = self.req("POST", f"/{ag}/zonas/{z}/tarifas", "GS", json={
            "criterio": criterio, "rango_min": rmin, "rango_max": rmax, "costo": costo,
            "vigente_desde": desde, "vigente_hasta": hasta, "is_active": activa})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id_tarifa"]

    def simple(self, nombre="Andes Express", **kw):
        ag = self.agencia(nombre)
        z = self.zona(ag)
        return ag, z, self.tarifa(ag, z, **kw)

    def envio(self, estado="LISTO_ENVIO", ciudad="La Paz"):
        """Envio real (venta + envio + historial inicial). Devuelve (id_envio, id_cliente)."""
        AsignacionAgencia.n_venta += 1
        cliente = self.crear_usuario("C")
        v = self.db.execute(text(
            "insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,"
            "nombre_cliente,correo,telefono,direccion,ciudad) values (:u,100,25,'QR','PAGADO',:k,'DOMICILIO',"
            "'Cliente','c@andes-express.com','1','Calle 1',:c) returning id_venta"),
            {"u": cliente, "k": f"F9{AsignacionAgencia.n_venta:06d}{uuid.uuid4().hex[:4]}", "c": ciudad}).scalar()
        suc = self.db.execute(text("select codigo_sucursal from sucursales limit 1")).scalar()
        e = self.db.execute(text("insert into envios (id_venta,estado,codigo_sucursal) values (:v,:e,:s) returning id_envio"),
                            {"v": v, "e": estado, "s": suc}).scalar()
        self.db.execute(text("insert into envio_historial (id_envio, estado_nuevo, observacion) values (:e,'PREPARANDO','inicio')"), {"e": e})
        self.db.commit()
        return e, cliente

    def asignar(self, id_envio, rol="GS", headers=None, **json):
        h = headers if headers is not None else self.headers(rol)
        return self.client.patch(f"{ENVIOS}/{id_envio}/asignar", headers=h, json=json)

    def con_agencia(self, id_envio, ag, peso="2", vol="1", rol="GS", **extra):
        return self.asignar(id_envio, rol, id_agencia=ag, peso_kg=peso, volumen_m3=vol, **extra)

    def fila(self, id_envio):
        return tuple(self.db.execute(text(
            "select estado, id_agencia, id_tarifa_aplicada, costo_agencia, peso_kg, volumen_m3, id_repartidor, "
            "fecha_estimada_entrega, (select count(*) from envio_historial h where h.id_envio=envios.id_envio) "
            "from envios where id_envio=:e"), {"e": id_envio}).one())

    def sin_cambios(self, id_envio, antes):
        self.db.expire_all()
        self.assertEqual(self.fila(id_envio), antes, "el envio cambio aunque la asignacion fallo")

    # -- exito y persistencia ------------------------------------------------------------------
    def test_asignacion_exitosa_persiste_el_snapshot(self):
        ag, z, t = self.simple(criterio="PESO", rmin="0", rmax="5", costo="10.50")
        e, _ = self.envio()
        r = self.con_agencia(e, ag, peso="2.5", vol="0.75")
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(set(b), {"status", "data", "message"})
        self.assertEqual(b["status"], "success")
        self.assertIn("Andes Express", b["message"])
        d = b["data"]
        self.assertEqual((d["estado"], d["agencia_id"], d["agencia_nombre"], d["repartidor_id"]), ("ASIGNADO", ag, "Andes Express", None))
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"], d["peso_kg"], d["volumen_m3"]), (10.5, t, 2.5, 0.75))
        # persistido EXACTAMENTE en la BD
        self.db.expire_all()
        estado, id_ag, id_t, costo, peso, vol, rep, _, n_hist = self.fila(e)
        self.assertEqual((estado, id_ag, id_t, float(costo), float(peso), float(vol), rep, n_hist), ("ASIGNADO", ag, t, 10.5, 2.5, 0.75, None, 2))

    def test_historial_registra_la_agencia_sin_el_costo_interno(self):
        ag, _, _ = self.simple(costo="123.45")
        e, cliente = self.envio()
        self.assertEqual(self.con_agencia(e, ag, observacion="urgente").status_code, 200)
        cli = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(cliente), 'rol': 'C'})}"}
        h = self.client.get(f"{ENVIOS}/{e}/historial", headers=cli)                # el CLIENTE lee su historial
        self.assertEqual(h.status_code, 200, h.text)
        ultimo = h.json()["data"][-1]
        self.assertEqual((ultimo["estado_anterior"], ultimo["estado_nuevo"]), ("LISTO_ENVIO", "ASIGNADO"))
        self.assertIn("Andes Express", ultimo["observacion"])
        self.assertIn("urgente", ultimo["observacion"])
        self.assertNotIn("123", h.text)                                            # ni el costo interno

    def test_fecha_estimada_opcional(self):
        ag, _, _ = self.simple()
        e, _ = self.envio()
        futura = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
        self.assertEqual(self.con_agencia(e, ag, fecha_estimada_entrega=futura.isoformat()).status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.fila(e)[7], futura)
        e2, _ = self.envio()
        antes = self.fila(e2)
        r = self.con_agencia(e2, ag, fecha_estimada_entrega=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 400, r.text)
        self.sin_cambios(e2, antes)

    def test_asignar_ambas_a_distintos_envios_y_misma_agencia(self):
        ag, _, _ = self.simple(rmax=None)
        for _ in range(3):
            e, _c = self.envio()
            self.assertEqual(self.con_agencia(e, ag).status_code, 200)

    # -- reglas de Fase 8 aplicadas a la asignacion ---------------------------------------------
    def test_tarifa_peso(self):
        ag, _, t = self.simple(criterio="PESO", rmin="0", rmax="5", costo="10")
        e, _ = self.envio()
        d = self.con_agencia(e, ag, peso="2", vol="99").json()["data"]
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"]), (10.0, t))

    def test_tarifa_volumen(self):
        ag, _, t = self.simple(criterio="VOLUMEN", rmin="0", rmax="3", costo="22")
        e, _ = self.envio()
        d = self.con_agencia(e, ag, peso="99", vol="1.5").json()["data"]
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"]), (22.0, t))

    def test_ambas_aplican_gana_el_mayor_costo(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", costo="10")
        tv = self.tarifa(ag, z, "VOLUMEN", costo="25")
        e, _ = self.envio()
        d = self.con_agencia(e, ag).json()["data"]
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"]), (25.0, tv))
        ag2 = self.agencia("Otra")
        z2 = self.zona(ag2)
        tp = self.tarifa(ag2, z2, "PESO", costo="40")
        self.tarifa(ag2, z2, "VOLUMEN", costo="25")
        e2, _ = self.envio()
        d2 = self.con_agencia(e2, ag2).json()["data"]
        self.assertEqual((d2["costo_agencia"], d2["id_tarifa_aplicada"]), (40.0, tp))

    def test_empate_de_costo_gana_peso(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "VOLUMEN", costo="15")
        tp = self.tarifa(ag, z, "PESO", costo="15")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, ag).json()["data"]["id_tarifa_aplicada"], tp)

    def test_empate_prefiere_ciudad_completa_sobre_subzona(self):
        ag = self.agencia()
        completa, sopo = self.zona(ag), self.zona(ag, LA_PAZ, "Sopocachi")
        self.tarifa(ag, sopo, "PESO", costo="15")
        t_completa = self.tarifa(ag, completa, "PESO", costo="15")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, ag).json()["data"]["id_tarifa_aplicada"], t_completa)

    def test_mayor_costo_entre_zonas(self):
        ag = self.agencia()
        completa, sopo = self.zona(ag), self.zona(ag, LA_PAZ, "Sopocachi")
        self.tarifa(ag, completa, costo="10")
        t_sopo = self.tarifa(ag, sopo, costo="18")
        e, _ = self.envio()
        d = self.con_agencia(e, ag).json()["data"]
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"]), (18.0, t_sopo))

    def test_ambiguedad_final_409_no_persiste_nada(self):
        ag = self.agencia()
        a, b = self.zona(ag, LA_PAZ, "Sopocachi"), self.zona(ag, LA_PAZ, "Miraflores")
        self.tarifa(ag, a, "PESO", costo="15")
        self.tarifa(ag, b, "PESO", costo="15")
        e, _ = self.envio()
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("equivalentes", r.json()["detail"])
        self.sin_cambios(e, antes)

    def test_limites_de_rango_minimo_incluido_maximo_excluido(self):
        ag, _, t = self.simple(criterio="PESO", rmin="5", rmax="10", costo="20")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, ag, peso="10").status_code, 404)              # maximo EXCLUIDO
        self.assertEqual(self.con_agencia(e, ag, peso="4.999").status_code, 404)
        self.sin_cambios(e, (("LISTO_ENVIO", None, None, None, None, None, None, None, 1)))
        self.assertEqual(self.con_agencia(e, ag, peso="5").status_code, 200)               # minimo INCLUIDO
        self.db.expire_all()
        self.assertEqual(self.fila(e)[2], t)

    def test_rango_abierto(self):
        ag, _, _ = self.simple(criterio="VOLUMEN", rmin="2", rmax=None, costo="30")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, ag, vol="9999999.999").json()["data"]["costo_agencia"], 30.0)

    def test_tarifa_inexistente_404_con_mensaje_propio(self):
        ag = self.agencia()
        self.zona(ag)                                     # cubre la ciudad pero sin tarifas
        e, _ = self.envio()
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no existe tarifa aplicable", r.json()["detail"])
        self.assertNotIn("no tiene cobertura", r.json()["detail"])
        self.sin_cambios(e, antes)

    def test_tarifa_inactiva_expirada_futura_no_se_usan(self):
        ag = self.agencia()
        z = self.zona(ag)
        self.tarifa(ag, z, "PESO", "0", "5", "99", activa=False)
        self.tarifa(ag, z, "PESO", "5", "10", "98", desde="2020-01-01", hasta=dia(-1))
        self.tarifa(ag, z, "PESO", "10", "15", "97", desde=dia(1))
        e, _ = self.envio()
        antes = self.fila(e)
        for peso in ("2", "7", "12"):
            r = self.con_agencia(e, ag, peso=peso)
            self.assertEqual(r.status_code, 404, (peso, r.text))
            self.assertIn("no existe tarifa aplicable", r.json()["detail"])
        self.sin_cambios(e, antes)

    # -- validaciones previas -------------------------------------------------------------------------
    def test_envio_inexistente_404(self):
        ag, _, _ = self.simple()
        r = self.con_agencia(999999, ag)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("envio", r.json()["detail"])

    def test_agencia_inexistente_404(self):
        e, _ = self.envio()
        antes = self.fila(e)
        r = self.con_agencia(e, 999999)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("agencia", r.json()["detail"])
        self.sin_cambios(e, antes)

    def test_agencia_deshabilitada_400(self):
        ag, _, _ = self.simple()
        self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": False})
        e, _ = self.envio()
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("deshabilitada", r.json()["detail"])
        self.sin_cambios(e, antes)
        self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": True})
        self.assertEqual(self.con_agencia(e, ag).status_code, 200)

    def test_agencia_sin_cobertura_404(self):
        ag = self.agencia()
        z = self.zona(ag, COCHABAMBA)
        self.tarifa(ag, z)
        e, _ = self.envio(ciudad="La Paz")
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no tiene cobertura", r.json()["detail"])
        self.sin_cambios(e, antes)
        e2, _ = self.envio(ciudad="Cochabamba")
        self.assertEqual(self.con_agencia(e2, ag).status_code, 200)

    def test_ciudad_del_envio_inexistente_404(self):
        ag, _, _ = self.simple()
        e, _ = self.envio(ciudad="Atlantida")
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("Atlantida", r.json()["detail"])
        self.sin_cambios(e, antes)

    def test_ciudad_del_envio_ambigua_409(self):
        ag, _, _ = self.simple()
        self.db.execute(text("insert into ciudades (nombre, departamento) values ('LA  PAZ','Otro')"))
        self.db.commit()
        e, _ = self.envio(ciudad="la paz")
        antes = self.fila(e)
        r = self.con_agencia(e, ag)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ambiguo", r.json()["detail"])
        self.sin_cambios(e, antes)

    def test_ciudad_del_envio_se_normaliza(self):
        ag, _, _ = self.simple()
        for ciudad in ("  la   PAZ ", "LA PAZ"):
            e, _ = self.envio(ciudad=ciudad)
            self.assertEqual(self.con_agencia(e, ag).status_code, 200, ciudad)

    def test_peso_y_volumen_invalidos_o_ausentes_422_sin_cambios(self):
        ag, _, _ = self.simple(rmax=None)
        e, _ = self.envio()
        antes = self.fila(e)
        casos = [dict(id_agencia=ag), dict(id_agencia=ag, peso_kg="2"), dict(id_agencia=ag, volumen_m3="1")]
        for peso, vol in (("0", "1"), ("-1", "1"), ("1", "0"), ("1", "-2"), ("NaN", "1"), ("1", "Infinity"), ("-Infinity", "1"),
                          ("abc", "1"), ("1.2345", "1"), ("1", "0.0001"), ("10000000", "1")):
            casos.append(dict(id_agencia=ag, peso_kg=peso, volumen_m3=vol))
        for cuerpo in casos:
            with self.subTest(cuerpo=cuerpo):
                r = self.asignar(e, "GS", **cuerpo)
                self.assertEqual(r.status_code, 422, (cuerpo, r.text))
        self.sin_cambios(e, antes)

    # -- exclusion mutua repartidor/agencia -----------------------------------------------------------
    def test_payload_con_ambos_o_ninguno_422(self):
        ag, _, _ = self.simple()
        d = self.crear_usuario("D")
        e, _ = self.envio()
        antes = self.fila(e)
        for cuerpo in (dict(id_agencia=ag, id_repartidor=str(d), peso_kg="2", volumen_m3="1"), {}, dict(observacion="x"),
                       dict(id_repartidor=str(d), peso_kg="2"), dict(id_repartidor=str(d), volumen_m3="1")):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.asignar(e, "GS", **cuerpo).status_code, 422)
        self.sin_cambios(e, antes)

    def test_envio_asignado_a_repartidor_no_admite_agencia_y_viceversa(self):
        ag, _, _ = self.simple(rmax=None)
        d = self.crear_usuario("D")
        e1, _ = self.envio()
        self.assertEqual(self.asignar(e1, "GS", id_repartidor=str(d)).status_code, 200)       # flujo CU18 original
        antes = self.fila(e1)
        r = self.con_agencia(e1, ag)
        self.assertEqual(r.status_code, 409, r.text)
        self.sin_cambios(e1, antes)
        e2, _ = self.envio()
        self.assertEqual(self.con_agencia(e2, ag).status_code, 200)
        antes2 = self.fila(e2)
        r2 = self.asignar(e2, "GS", id_repartidor=str(d))
        self.assertEqual(r2.status_code, 409, r2.text)
        self.sin_cambios(e2, antes2)
        self.db.expire_all()
        for e in (e1, e2):                                     # invariante en la DB: nunca los dos
            self.assertEqual(self.db.execute(text("select count(*) from envios where id_envio=:e and id_agencia is not null and id_repartidor is not null"), {"e": e}).scalar(), 0)

    def test_check_repartidor_o_agencia_de_la_db_sigue_activo(self):
        ag, _, t = self.simple()
        d = self.crear_usuario("D")
        e, _ = self.envio()
        with self.assertRaises(Exception) as cm:
            self.db.execute(text("update envios set id_agencia=:a, id_tarifa_aplicada=:t, costo_agencia=1, peso_kg=1, volumen_m3=1, id_repartidor=:d where id_envio=:e"),
                            {"a": ag, "t": t, "d": d, "e": e})
        self.assertIn("ck_envios_repartidor_o_agencia", str(cm.exception))
        self.db.rollback()

    def test_asignar_repartidor_de_cu18_sigue_igual_y_sin_datos_de_agencia(self):
        d = self.crear_usuario("D")
        e, _ = self.envio()
        r = self.asignar(e, "GS", id_repartidor=str(d))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["message"], "Repartidor asignado.")
        data = r.json()["data"]
        self.assertEqual((data["estado"], data["repartidor_id"], data["agencia_id"], data["agencia_nombre"]), ("ASIGNADO", str(d), None, None))
        self.assertEqual((data["costo_agencia"], data["id_tarifa_aplicada"], data["peso_kg"], data["volumen_m3"]), (None, None, None, None))
        self.assertEqual(self.asignar(e, "GS", id_repartidor=str(d)).status_code, 409)         # doble asignacion: igual que antes
        self.assertEqual(self.asignar(999999, "GS", id_repartidor=str(d)).status_code, 404)

    # -- estado / doble asignacion --------------------------------------------------------------------
    def test_solo_desde_listo_envio_409(self):
        ag, _, _ = self.simple(rmax=None)
        for estado in ("PREPARANDO", "ASIGNADO", "EN_RUTA", "ENTREGADO", "INTENTO_FALLIDO", "REPROGRAMADO", "CANCELADO"):
            e, _ = self.envio(estado)
            antes = self.fila(e)
            r = self.con_agencia(e, ag)
            with self.subTest(estado=estado):
                self.assertEqual(r.status_code, 409, (estado, r.text))
                self.sin_cambios(e, antes)

    def test_segunda_asignacion_al_mismo_envio_409_y_conserva_la_primera(self):
        a1, _, t1 = self.simple(costo="10")
        a2, _, _ = self.simple("Segunda Agencia", costo="99")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, a1).status_code, 200)
        antes = self.fila(e)
        r = self.con_agencia(e, a2)
        self.assertEqual(r.status_code, 409, r.text)
        self.sin_cambios(e, antes)
        self.assertEqual(antes[1:3], (a1, t1))

    # -- autorizacion ---------------------------------------------------------------------------------
    def test_asu_y_gs_asignan(self):
        ag, _, _ = self.simple(rmax=None)
        for rol in ("ASU", "GS"):
            e, _ = self.envio()
            r = self.con_agencia(e, ag, rol=rol)
            self.assertEqual(r.status_code, 200, (rol, r.text))
            self.assertIn("costo_agencia", r.json()["data"])

    def test_d_v_c_403_y_no_cambian_nada(self):
        ag, _, _ = self.simple(rmax=None)
        e, _ = self.envio()
        antes = self.fila(e)
        for rol in ("D", "V", "C"):
            r = self.con_agencia(e, ag, rol=rol)
            with self.subTest(rol=rol):
                self.assertEqual(r.status_code, 403, (rol, r.text))
                self.assertNotIn("data", r.json())
        self.sin_cambios(e, antes)

    def test_d_asignado_al_envio_tampoco_asigna_agencia(self):
        ag, _, _ = self.simple(rmax=None)
        d = self.crear_usuario("D")
        e, _ = self.envio()
        self.assertEqual(self.asignar(e, "GS", id_repartidor=str(d)).status_code, 200)
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(d), 'rol': 'D'})}"}
        self.assertEqual(self.con_agencia(e, ag, headers=h).status_code, 403)

    def test_sin_token_401_y_tokens_invalidos(self):
        ag, _, _ = self.simple()
        e, _ = self.envio()
        antes = self.fila(e)
        self.assertEqual(self.con_agencia(e, ag, headers={}).status_code, 401)
        uid = str(self.crear_usuario("GS"))
        futuro = datetime.now(timezone.utc) + timedelta(hours=1)
        pasado = datetime.now(timezone.utc) - timedelta(hours=1)
        tokens = {
            "basura": "no-es-un-jwt",
            "firma_ajena": jwt.encode({"sub": uid, "exp": futuro}, "otra-clave-secreta-de-32-bytes-min!!", algorithm=ALGORITHM),
            "expirado": jwt.encode({"sub": uid, "exp": pasado}, SECRET_KEY, algorithm=ALGORITHM),
            "usuario_inexistente": crear_token_acceso({"sub": str(uuid.uuid4()), "rol": "GS"}),
        }
        for tipo, token in tokens.items():
            with self.subTest(token=tipo):
                self.assertEqual(self.con_agencia(e, ag, headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        self.sin_cambios(e, antes)

    def test_usuario_inactivo_403(self):
        ag, _, _ = self.simple()
        e, _ = self.envio()
        antes = self.fila(e)
        uid = self.crear_usuario("GS", activo=False)
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'GS'})}"}
        self.assertEqual(self.con_agencia(e, ag, headers=h).status_code, 403)
        self.sin_cambios(e, antes)

    def test_rol_real_de_la_bd_no_el_claim_del_token(self):
        ag, _, _ = self.simple(rmax=None)
        e, _ = self.envio()
        antes = self.fila(e)
        for real, declarado in (("V", "GS"), ("C", "ASU"), ("D", "GS"), ("D", "ASU")):
            uid = self.crear_usuario(real)
            h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': declarado})}"}
            with self.subTest(real=real, declarado=declarado):
                self.assertEqual(self.con_agencia(e, ag, headers=h).status_code, 403)
        self.sin_cambios(e, antes)
        uid = self.crear_usuario("GS")                       # y un GS real que declara V si puede
        h = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(uid), 'rol': 'V'})}"}
        self.assertEqual(self.con_agencia(e, ag, headers=h).status_code, 200)

    def test_validacion_de_envio_y_permiso_siguen_el_orden_de_cu18(self):
        """Igual que asignar repartidor: envio inexistente 404 antes que el 403."""
        ag, _, _ = self.simple()
        for rol in ("V", "C", "D"):
            self.assertEqual(self.con_agencia(999999, ag, rol=rol).status_code, 404, rol)

    # -- lo asignado se puede consultar despues ----------------------------------------------------------
    def test_detalle_muestra_agencia_y_costo_solo_a_asu_gs(self):
        ag, _, t = self.simple(costo="33")
        e, cliente = self.envio()
        self.assertEqual(self.con_agencia(e, ag).status_code, 200)
        for rol in ("ASU", "GS"):
            d = self.client.get(f"{ENVIOS}/{e}", headers=self.headers(rol)).json()["data"]
            self.assertEqual((d["agencia_id"], d["agencia_nombre"], d["costo_agencia"], d["id_tarifa_aplicada"]), (ag, "Andes Express", 33.0, t))
        cli = {"Authorization": f"Bearer {crear_token_acceso({'sub': str(cliente), 'rol': 'C'})}"}
        r = self.client.get(f"{ENVIOS}/{e}", headers=cli)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual((d["agencia_id"], d["agencia_nombre"]), (ag, "Andes Express"))
        for interno in INTERNOS:
            self.assertNotIn(f'"{interno}"', r.text)                     # el cliente NUNCA ve el costo interno

    def test_el_snapshot_no_cambia_si_luego_se_edita_la_tarifa_o_se_deshabilita_la_agencia(self):
        ag, z, t = self.simple(costo="10")
        e, _ = self.envio()
        self.assertEqual(self.con_agencia(e, ag).status_code, 200)
        self.assertEqual(self.req("PUT", f"/{ag}/zonas/{z}/tarifas/{t}", "GS", json={"costo": "999"}).status_code, 200)
        self.req("PATCH", f"/{ag}/estado", "GS", json={"is_active": False})
        d = self.client.get(f"{ENVIOS}/{e}", headers=self.headers("GS")).json()["data"]
        self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"], d["agencia_id"]), (10.0, t, ag))
        self.assertEqual(self.req("DELETE", f"/{ag}/zonas/{z}/tarifas/{t}", "GS").status_code, 409)   # la FK protege lo asignado
        self.assertEqual(self.req("DELETE", f"/{ag}", "GS").status_code, 409)

    def test_listado_incluye_la_agencia_sin_n_mas_uno(self):
        ag, _, _ = self.simple(rmax=None)
        ids = []
        for _ in range(6):
            e, _c = self.envio()
            self.assertEqual(self.con_agencia(e, ag).status_code, 200)
            ids.append(e)
        vistas: list[str] = []

        def contar(conn, cursor, statement, parameters, context, executemany):
            vistas.append(statement)

        self.client.get(ENVIOS, headers=self.headers("GS"), params={"limit": 100})   # calentamiento
        event.listen(self.conn, "before_cursor_execute", contar)
        try:
            r = self.client.get(ENVIOS, headers=self.headers("GS"), params={"limit": 100})
        finally:
            event.remove(self.conn, "before_cursor_execute", contar)
        self.assertEqual(r.status_code, 200, r.text)
        propios = [x for x in r.json()["data"] if x["id_envio"] in ids]
        self.assertEqual(len(propios), 6)
        self.assertTrue(all(x["agencia_nombre"] == "Andes Express" and x["costo_agencia"] == 10.0 for x in propios))
        self.assertLessEqual(len([s for s in vistas if "FROM agencias_reparto" in s]), 1, "una consulta de agencias por pagina, no por envio")

    def test_envio_con_agencia_puede_despacharse(self):
        """Decision posterior a la Fase 9: un envio con agencia SI pasa a EN_RUTA (la
        matriz completa del flujo de agencia vive en test_flujo_envio_agencia.py)."""
        ag, _, _ = self.simple(rmax=None)
        e, _ = self.envio()
        d = self.con_agencia(e, ag).json()["data"]
        self.assertEqual((d["estado"], d["transiciones_permitidas"]), ("ASIGNADO", ["EN_RUTA", "CANCELADO"]))
        r = self.client.patch(f"{ENVIOS}/{e}/estado", headers=self.headers("GS"), json={"estado": "EN_RUTA"})
        self.assertEqual(r.status_code, 200, r.text)

    # -- alcance: nada mas cambia ------------------------------------------------------------------------
    def test_no_toca_ventas_ni_costo_envio_del_cliente(self):
        ag, _, _ = self.simple()
        e, _ = self.envio()
        antes = self.db.execute(text("select total, costo_envio, ciudad, estado_pago from ventas where id_venta=(select id_venta from envios where id_envio=:e)"), {"e": e}).one()
        self.assertEqual(self.con_agencia(e, ag).status_code, 200)
        self.db.expire_all()
        despues = self.db.execute(text("select total, costo_envio, ciudad, estado_pago from ventas where id_venta=(select id_venta from envios where id_envio=:e)"), {"e": e}).one()
        self.assertEqual(tuple(antes), tuple(despues))
        self.assertEqual(float(despues.costo_envio), 25.0)                     # importe cobrado al cliente intacto

    def test_no_modifica_agencias_zonas_ni_tarifas(self):
        ag, z, t = self.simple()
        e, _ = self.envio()
        q = "select md5(coalesce(string_agg(x::text,'|' order by x::text),'')) from {} x"
        antes = [self.db.execute(text(q.format(tb))).scalar() for tb in ("agencias_reparto", "agencia_zonas", "agencia_tarifas")]
        self.assertEqual(self.con_agencia(e, ag).status_code, 200)
        self.db.expire_all()
        self.assertEqual(antes, [self.db.execute(text(q.format(tb))).scalar() for tb in ("agencias_reparto", "agencia_zonas", "agencia_tarifas")])


if __name__ == "__main__":
    unittest.main()
