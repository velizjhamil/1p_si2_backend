# Pruebas unitarias AISLADAS de CU19 fase 9 (payload de asignar + asignacion_agencia_service).
# Sin base de datos: la sesion es un Mock y las entidades viven en memoria. Los
# efectos reales (SQL, CHECK/FK, JWT, concurrencia, rollback) se prueban en
# tests/integration/test_asignacion_agencia_*.py contra la BD local.
#
# La SELECCION de tarifa NO se prueba aqui: es la de Fase 8 (cotizacion_service),
# que este servicio llama sin reimplementarla. Se verifica justamente eso.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_asignacion_agencia_service.py
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.modules.delivery import asignacion_agencia_service as svc  # noqa: E402
from app.modules.delivery import cotizacion_service, service as cu18  # noqa: E402
from app.modules.delivery.models import AgenciaReparto, Envio  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.modules.ventas.models import Venta  # noqa: E402
from app.schemas.envio import AsignarEnvioPayload  # noqa: E402

AG = 4


def usuario(rol):
    return Usuario(id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def envio(estado="LISTO_ENVIO", repartidor=None, agencia=None, ciudad="La Paz"):
    e = Envio(id_envio=1, id_venta=1, estado=estado, intentos_fallidos=0, codigo_sucursal=1,
              id_repartidor=repartidor, id_agencia=agencia)
    e.venta = Venta(id_venta=1, ciudad=ciudad, codigo="ATT-1")
    return e


def payload(**extra):
    base = dict(id_agencia=AG, peso_kg="2", volumen_m3="1")
    base.update(extra)
    return AsignarEnvioPayload(**base)


COTIZACION = {"tarifa": {"id_tarifa": 77}, "costo_agencia": D("12.50"), "peso_kg": D("2"), "volumen_m3": D("1")}


def agencia():
    return AgenciaReparto(id_agencia=AG, razon_social="Andes Express", nit="1020304050",
                          correo_facturacion="f@andes-express.com", direccion_fiscal="Av 1", is_active=True)


class Diag:
    def __init__(self, n):
        self.constraint_name = n


class Orig(Exception):
    def __init__(self, n):
        super().__init__(n)
        self.diag = Diag(n)


def integrity(n):
    return IntegrityError("x", {}, Orig(n))


class PayloadRepartidorOAgencia(unittest.TestCase):
    def test_contrato_original_de_cu18_intacto(self):
        rid = uuid.uuid4()
        p = AsignarEnvioPayload(id_repartidor=rid)
        self.assertEqual((p.id_repartidor, p.id_agencia, p.peso_kg, p.volumen_m3), (rid, None, None, None))
        self.assertIsNotNone(AsignarEnvioPayload(id_repartidor=rid, observacion=" hola ", fecha_estimada_entrega=datetime.now(timezone.utc)))

    def test_con_agencia_exige_peso_y_volumen(self):
        p = payload()
        self.assertEqual((p.id_agencia, p.peso_kg, p.volumen_m3, p.id_repartidor), (AG, D(2), D(1), None))
        for faltante in ({"peso_kg": None}, {"volumen_m3": None}, {"peso_kg": None, "volumen_m3": None}):
            with self.subTest(faltante), self.assertRaises(ValidationError):
                payload(**faltante)

    def test_exclusion_mutua_y_ninguno(self):
        with self.assertRaises(ValidationError) as cm:
            payload(id_repartidor=uuid.uuid4())
        self.assertIn("mutuamente excluyentes", str(cm.exception))
        with self.assertRaises(ValidationError):
            AsignarEnvioPayload()
        with self.assertRaises(ValidationError):
            AsignarEnvioPayload(fecha_estimada_entrega=datetime.now(timezone.utc))

    def test_peso_y_volumen_solo_con_agencia(self):
        with self.assertRaises(ValidationError) as cm:
            AsignarEnvioPayload(id_repartidor=uuid.uuid4(), peso_kg="2")
        self.assertIn("solo se admiten junto con 'id_agencia'", str(cm.exception))
        with self.assertRaises(ValidationError):
            AsignarEnvioPayload(id_repartidor=uuid.uuid4(), volumen_m3="1")

    def test_dimensiones_invalidas_en_el_schema(self):
        for extra in ({"peso_kg": "0"}, {"peso_kg": "-1"}, {"volumen_m3": "0"}, {"volumen_m3": "-0.5"},
                      {"peso_kg": "NaN"}, {"peso_kg": "Infinity"}, {"volumen_m3": "-Infinity"}, {"peso_kg": "abc"},
                      {"peso_kg": "1.2345"}, {"volumen_m3": "0.0001"}, {"peso_kg": "10000000"}, {"id_agencia": 0},
                      {"id_agencia": -3}):
            with self.subTest(extra), self.assertRaises(ValidationError):
                payload(**extra)

    def test_precision_valida(self):
        for v in ("0.001", "2.5", "9999999.999", "5.0000"):
            self.assertEqual(payload(peso_kg=v, volumen_m3=v).peso_kg, D(v))


class ReutilizaSinDuplicar(unittest.TestCase):
    def test_usa_la_cotizacion_de_fase_8_y_los_helpers_de_cu18(self):
        self.assertIs(svc.cotizar, cotizacion_service.cotizar)
        self.assertIs(svc.validar_dimensiones, cotizacion_service.validar_dimensiones)
        self.assertIs(svc.obtener_envio, cu18.obtener_envio)
        self.assertIs(svc._exigir_permiso, cu18._exigir_permiso)
        self.assertIs(svc._validar_transicion, cu18._validar_transicion)
        self.assertIs(svc._cambiar_estado, cu18._cambiar_estado)

    def test_no_reimplementa_la_seleccion_de_tarifas(self):
        for nombre in ("seleccionar", "candidatas", "Candidata", "rango_contiene", "vigente_en", "prioridad"):
            self.assertFalse(hasattr(svc, nombre), nombre)


class Flujo(unittest.TestCase):
    def ejecutar(self, rol="GS", e=None, p=None, cot=COTIZACION, db=None, agencia_=None):
        e = e or envio()
        db = db or mock.Mock()
        with mock.patch.object(svc, "obtener_envio", return_value=e), \
             mock.patch.object(svc, "_buscar", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "cotizar", return_value=cot) as c:
            return e, db, c, svc.asignar_agencia(db, usuario(rol), 1, p or payload())

    def test_exito_guarda_el_snapshot_y_asigna(self):
        e, db, c, r = self.ejecutar()
        self.assertEqual((e.id_agencia, e.id_tarifa_aplicada, e.costo_agencia, e.peso_kg, e.volumen_m3),
                         (AG, 77, D("12.50"), D(2), D(1)))
        self.assertIsNone(e.id_repartidor)                     # exclusion mutua
        self.assertEqual(e.estado, "ASIGNADO")
        (h,) = e.historial
        self.assertEqual((h.estado_anterior, h.estado_nuevo), ("LISTO_ENVIO", "ASIGNADO"))
        self.assertIn("Andes Express", h.observacion)
        self.assertNotIn("12", h.observacion)                  # el costo interno NO va al historial (lo lee el cliente)
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(e)
        args = c.call_args.args
        self.assertEqual((args[2], args[3], args[4], args[5]), (AG, "La Paz", D(2), D(1)))   # cotiza con la ciudad de la venta

    def test_observacion_y_fecha_estimada(self):
        fecha = datetime.now(timezone.utc) + timedelta(days=2)
        e, _, _, _ = self.ejecutar(p=payload(observacion="  urgente ", fecha_estimada_entrega=fecha))
        self.assertIn("urgente", e.historial[0].observacion)
        self.assertEqual(e.fecha_estimada_entrega, fecha)

    def test_fecha_no_futura_400_sin_cambios(self):
        e = envio()
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self.ejecutar(e=e, db=db, p=payload(fecha_estimada_entrega=datetime.now(timezone.utc) - timedelta(days=1)))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual((e.estado, e.id_agencia, e.historial), ("LISTO_ENVIO", None, []))
        db.rollback.assert_called_once()
        db.commit.assert_not_called()

    def test_solo_asu_gs_asignan(self):
        for rol in ("ASU", "GS"):
            self.ejecutar(rol)
        for rol in ("D", "V", "C", None):
            e, db = envio(), mock.Mock()
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                self.ejecutar(rol, e=e, db=db)
            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual((e.estado, e.id_agencia), ("LISTO_ENVIO", None))
            db.commit.assert_not_called()

    def test_d_asignado_al_envio_tampoco_puede_asignar_agencia(self):
        d = usuario("D")
        e = envio()
        e.id_repartidor = d.id_usuario
        with mock.patch.object(svc, "obtener_envio", return_value=e), mock.patch.object(svc, "cotizar") as c:
            with self.assertRaises(HTTPException) as cm:
                svc.asignar_agencia(mock.Mock(), d, 1, payload())
        self.assertEqual(cm.exception.status_code, 403)
        c.assert_not_called()

    def test_solo_desde_listo_envio_409(self):
        for estado in ("PREPARANDO", "ASIGNADO", "EN_RUTA", "ENTREGADO", "INTENTO_FALLIDO", "REPROGRAMADO", "CANCELADO"):
            e = envio(estado)
            with self.subTest(estado), self.assertRaises(HTTPException) as cm:
                self.ejecutar(e=e)
            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual((e.estado, e.id_agencia, e.historial), (estado, None, []))

    def test_doble_asignacion_409(self):
        e = envio("ASIGNADO", agencia=AG)
        with self.assertRaises(HTTPException) as cm:
            self.ejecutar(e=e)
        self.assertEqual(cm.exception.status_code, 409)

    def test_con_repartidor_409_exclusion_mutua(self):
        e = envio("LISTO_ENVIO", repartidor=uuid.uuid4())     # estado inconsistente forzado: igual se rechaza
        with self.assertRaises(HTTPException) as cm:
            self.ejecutar(e=e)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("repartidor o una agencia", cm.exception.detail)
        self.assertIsNone(e.id_agencia)

    def test_dimensiones_invalidas_422_via_servicio(self):
        p = payload()
        p.peso_kg = D("0")                                     # el schema lo frena; el servicio se defiende igual
        with self.assertRaises(HTTPException) as cm:
            self.ejecutar(p=p)
        self.assertEqual(cm.exception.status_code, 422)

    def test_agencia_inexistente_404(self):
        e, db = envio(), mock.Mock()
        with mock.patch.object(svc, "obtener_envio", return_value=e), \
             mock.patch.object(svc, "_buscar", side_effect=HTTPException(404, "No existe la agencia con id 4.")), \
             mock.patch.object(svc, "cotizar") as c:
            with self.assertRaises(HTTPException) as cm:
                svc.asignar_agencia(db, usuario("GS"), 1, payload())
        self.assertEqual(cm.exception.status_code, 404)
        c.assert_not_called()
        self.assertEqual((e.estado, e.id_agencia), ("LISTO_ENVIO", None))
        db.rollback.assert_called_once()

    def test_errores_de_la_cotizacion_se_propagan_y_no_dejan_cambios(self):
        for codigo, detalle in ((400, "deshabilitada"), (404, "no tiene cobertura"), (404, "no existe tarifa aplicable"),
                                (404, "ciudad"), (409, "ambiguo"), (409, "equivalentes"), (422, "peso_kg")):
            e, db = envio(), mock.Mock()
            with self.subTest(codigo=codigo, detalle=detalle), \
                 mock.patch.object(svc, "obtener_envio", return_value=e), \
                 mock.patch.object(svc, "_buscar", return_value=agencia()), \
                 mock.patch.object(svc, "cotizar", side_effect=HTTPException(codigo, detalle)):
                with self.assertRaises(HTTPException) as cm:
                    svc.asignar_agencia(db, usuario("GS"), 1, payload())
            self.assertEqual((cm.exception.status_code, cm.exception.detail), (codigo, detalle))
            self.assertEqual((e.estado, e.id_agencia, e.id_tarifa_aplicada, e.costo_agencia, e.peso_kg, e.volumen_m3, e.historial),
                             ("LISTO_ENVIO", None, None, None, None, None, []))
            db.rollback.assert_called_once()
            db.commit.assert_not_called()

    def test_check_de_la_db_se_traduce_nunca_500(self):
        casos = [("ck_envios_repartidor_o_agencia", 409), ("fk_envios_id_agencia_agencias_reparto", 409),
                 ("fk_envios_id_tarifa_aplicada_agencia_tarifas", 409), ("ck_envios_agencia_snapshot_completo", 422),
                 ("ck_envios_costo_agencia_no_negativo", 422), ("otra_cosa", 409)]
        for nombre, codigo in casos:
            db = mock.Mock()
            db.commit.side_effect = integrity(nombre)
            with self.subTest(nombre), self.assertRaises(HTTPException) as cm:
                self.ejecutar(db=db)
            self.assertEqual(cm.exception.status_code, codigo)
            db.rollback.assert_called_once()


class Serializacion(unittest.TestCase):
    def envio_asignado(self):
        e = envio("ASIGNADO", agencia=AG)
        e.id_tarifa_aplicada, e.costo_agencia, e.peso_kg, e.volumen_m3 = 77, D("12.50"), D(2), D(1)
        e.agencia = agencia()
        e.venta.id_cliente = uuid.uuid4()
        e.venta.total = D(100)
        e.venta.nombre_cliente = "C"
        e.venta.detalles = []
        e.fecha_creacion = e.fecha_actualizacion = datetime.now(timezone.utc)
        return e

    def test_admin_ve_agencia_y_costo_interno(self):
        for rol in ("ASU", "GS"):
            d = cu18.serializar_envio(self.envio_asignado(), usuario(rol))
            self.assertEqual((d["agencia_id"], d["agencia_nombre"]), (AG, "Andes Express"))
            self.assertEqual((d["costo_agencia"], d["id_tarifa_aplicada"], d["peso_kg"], d["volumen_m3"]), (D("12.50"), 77, D(2), D(1)))

    def test_cliente_y_d_no_ven_el_costo_interno(self):
        for rol in ("C", "D", "V"):
            d = cu18.serializar_envio(self.envio_asignado(), usuario(rol))
            self.assertEqual((d["agencia_id"], d["agencia_nombre"]), (AG, "Andes Express"))     # como repartidor_nombre
            for interno in ("costo_agencia", "id_tarifa_aplicada", "peso_kg", "volumen_m3"):
                self.assertNotIn(interno, d, (rol, interno))

    def test_envio_sin_agencia_no_consulta_y_queda_en_none(self):
        e = self.envio_asignado()
        e.id_agencia, e.agencia = None, None
        e.id_tarifa_aplicada = e.costo_agencia = e.peso_kg = e.volumen_m3 = None
        d = cu18.serializar_envio(e, usuario("GS"))
        self.assertEqual((d["agencia_id"], d["agencia_nombre"], d["costo_agencia"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
