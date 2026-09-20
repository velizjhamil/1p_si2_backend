# Pruebas unitarias AISLADAS del flujo de estados de un envio con AGENCIA (CU19).
# Sin base de datos: sesion Mock y entidades en memoria (mismo estilo que
# test_delivery_service.py). El comportamiento real (SQL, JWT, historial en DB, matriz
# completa "ofrecido == aceptado") esta en tests/integration/test_flujo_envio_agencia.py.
#
# Decision funcional congelada:
#   - ASIGNADO -> EN_RUTA, EN_RUTA -> ENTREGADO y EN_RUTA -> INTENTO_FALLIDO valen
#     tambien para envios con agencia (sin repartidor), operados por ASU/GS.
#   - D NO opera ni avanza envios con agencia; V/C tampoco.
#   - agencia y repartidor nunca coexisten.
#   - `transiciones_permitidas` solo ofrece lo que el backend acepta.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_flujo_envio_agencia.py
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402

from app.modules.delivery import service  # noqa: E402
from app.modules.delivery.models import TRANSICIONES_ENVIO, Envio  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.modules.ventas.models import Venta  # noqa: E402
from app.schemas.envio import (  # noqa: E402
    AsignarEnvioPayload,
    CambiarEstadoPayload,
    IntentoFallidoPayload,
    ReprogramarEnvioPayload,
)

ADMIN = ("ASU", "GS")


def usuario(rol, uid=None):
    return Usuario(id_usuario=uid or uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def envio(estado="ASIGNADO", *, repartidor=None, agencia=None):
    e = Envio(id_envio=1, id_venta=1, estado=estado, intentos_fallidos=0, codigo_sucursal=1,
              id_repartidor=repartidor, id_agencia=agencia)
    e.venta = Venta(id_venta=1, codigo="ATT-1", id_cliente=uuid.uuid4())
    return e


def estado_payload(estado, obs=None):
    return CambiarEstadoPayload(estado=estado, observacion=obs)


def ejecutar(fn, e, *args):
    """Corre una operacion del service sobre `e` con sesion y notificacion simuladas."""
    db = mock.Mock()
    with mock.patch.object(service, "obtener_envio", return_value=e), \
         mock.patch.object(service, "_notificar_cliente") as notif:
        resultado = fn(db, *args)
    return db, notif, resultado


class TieneTransportista(unittest.TestCase):
    def test_repartidor_o_agencia(self):
        self.assertTrue(service.tiene_transportista(envio(repartidor=uuid.uuid4())))
        self.assertTrue(service.tiene_transportista(envio(agencia=4)))
        self.assertFalse(service.tiene_transportista(envio()))


class TransicionesPermitidas(unittest.TestCase):
    """Lo que ofrece la UI = lo que el backend acepta, por tipo de asignacion."""

    def test_asignado_con_agencia_admin_ofrece_en_ruta_y_cancelar(self):
        for rol in ADMIN:
            self.assertEqual(service.transiciones_permitidas(envio("ASIGNADO", agencia=4), usuario(rol)), ["EN_RUTA", "CANCELADO"])

    def test_envio_con_agencia_no_ofrece_nada_a_d_v_c(self):
        for rol in ("D", "V", "C", None):
            for estado in ("ASIGNADO", "EN_RUTA", "INTENTO_FALLIDO", "REPROGRAMADO"):
                self.assertEqual(service.transiciones_permitidas(envio(estado, agencia=4), usuario(rol)), [], (rol, estado))

    def test_en_ruta_con_agencia_ofrece_entregado_e_intento_fallido(self):
        for rol in ADMIN:
            self.assertEqual(service.transiciones_permitidas(envio("EN_RUTA", agencia=4), usuario(rol)), ["ENTREGADO", "INTENTO_FALLIDO"])

    def test_reprogramado_con_agencia_puede_volver_a_en_ruta(self):
        self.assertEqual(service.transiciones_permitidas(envio("REPROGRAMADO", agencia=4), usuario("GS")), ["EN_RUTA", "CANCELADO"])

    def test_repartidor_sin_cambios_de_cu18(self):
        d = usuario("D")
        e = envio("ASIGNADO", repartidor=d.id_usuario)
        self.assertEqual(service.transiciones_permitidas(e, usuario("GS")), ["EN_RUTA", "CANCELADO"])
        self.assertEqual(service.transiciones_permitidas(e, d), ["EN_RUTA"])                       # D no cancela
        self.assertEqual(service.transiciones_permitidas(e, usuario("D")), [])                     # otro D: nada
        self.assertEqual(service.transiciones_permitidas(envio("EN_RUTA", repartidor=d.id_usuario), d), ["ENTREGADO", "INTENTO_FALLIDO"])
        self.assertEqual(service.transiciones_permitidas(envio("INTENTO_FALLIDO", repartidor=d.id_usuario), d), ["REPROGRAMADO"])

    def test_no_se_ofrece_despachar_un_envio_sin_transportista(self):
        for rol in ADMIN:
            self.assertEqual(service.transiciones_permitidas(envio("ASIGNADO"), usuario(rol)), ["CANCELADO"])
            self.assertEqual(service.transiciones_permitidas(envio("REPROGRAMADO"), usuario(rol)), ["CANCELADO"])

    def test_estados_previos_y_terminales_no_cambian(self):
        gs = usuario("GS")
        self.assertEqual(service.transiciones_permitidas(envio("PREPARANDO"), gs), ["LISTO_ENVIO", "CANCELADO"])
        self.assertEqual(service.transiciones_permitidas(envio("LISTO_ENVIO"), gs), ["ASIGNADO", "CANCELADO"])
        for terminal in ("ENTREGADO", "CANCELADO"):
            self.assertEqual(service.transiciones_permitidas(envio(terminal, agencia=4), gs), [])

    def test_lo_ofrecido_siempre_esta_en_la_maquina_de_estados(self):
        for estado, permitidos in TRANSICIONES_ENVIO.items():
            for transportista in ({}, {"agencia": 4}, {"repartidor": uuid.uuid4()}):
                for rol in ("ASU", "GS", "D", "V", "C"):
                    ofrecido = service.transiciones_permitidas(envio(estado, **transportista), usuario(rol))
                    self.assertTrue(set(ofrecido) <= set(permitidos), (estado, transportista, rol, ofrecido))


class DespacharConAgencia(unittest.TestCase):
    def test_asu_gs_asignado_a_en_ruta(self):
        for rol in ADMIN:
            e = envio("ASIGNADO", agencia=4)
            db, notif, _ = ejecutar(service.cambiar_estado, e, usuario(rol), 1, estado_payload("EN_RUTA", "sale con la agencia"))
            self.assertEqual(e.estado, "EN_RUTA")
            (h,) = e.historial
            self.assertEqual((h.estado_anterior, h.estado_nuevo, h.observacion), ("ASIGNADO", "EN_RUTA", "sale con la agencia"))
            notif.assert_called_once()
            db.commit.assert_called_once()

    def test_d_v_c_no_pueden_y_el_envio_no_cambia(self):
        for rol in ("D", "V", "C"):
            e = envio("ASIGNADO", agencia=4)
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                ejecutar(service.cambiar_estado, e, usuario(rol), 1, estado_payload("EN_RUTA"))
            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual((e.estado, e.historial), ("ASIGNADO", []))

    def test_d_con_otro_envio_asignado_tampoco_puede(self):
        e = envio("ASIGNADO", agencia=4)
        with self.assertRaises(HTTPException) as cm:
            ejecutar(service.cambiar_estado, e, usuario("D"), 1, estado_payload("EN_RUTA"))
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("no esta asignado a este repartidor", cm.exception.detail)

    def test_sin_transportista_sigue_siendo_400(self):
        e = envio("ASIGNADO")
        with self.assertRaises(HTTPException) as cm:
            ejecutar(service.cambiar_estado, e, usuario("GS"), 1, estado_payload("EN_RUTA"))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("sin repartidor ni agencia", cm.exception.detail)
        self.assertEqual((e.estado, e.historial), ("ASIGNADO", []))

    def test_repartidor_sigue_igual(self):
        d = usuario("D")
        for actor in (d, usuario("GS")):
            e = envio("ASIGNADO", repartidor=d.id_usuario)
            ejecutar(service.cambiar_estado, e, actor, 1, estado_payload("EN_RUTA"))
            self.assertEqual(e.estado, "EN_RUTA")


class EntregarYFallarConAgencia(unittest.TestCase):
    def test_en_ruta_a_entregado_admin(self):
        for rol in ADMIN:
            e = envio("EN_RUTA", agencia=4)
            _, notif, _ = ejecutar(service.cambiar_estado, e, usuario(rol), 1, estado_payload("ENTREGADO"))
            self.assertEqual(e.estado, "ENTREGADO")
            self.assertIsNotNone(e.fecha_entrega_real)
            self.assertEqual((e.historial[0].estado_anterior, e.historial[0].estado_nuevo), ("EN_RUTA", "ENTREGADO"))
            notif.assert_called_once()

    def test_en_ruta_a_intento_fallido_admin(self):
        for rol in ADMIN:
            e = envio("EN_RUTA", agencia=4)
            ejecutar(service.registrar_intento_fallido, e, usuario(rol), 1, IntentoFallidoPayload(motivo="cliente ausente"))
            self.assertEqual((e.estado, e.intentos_fallidos, e.motivo_fallo), ("INTENTO_FALLIDO", 1, "cliente ausente"))
            self.assertEqual((e.historial[0].estado_anterior, e.historial[0].estado_nuevo), ("EN_RUTA", "INTENTO_FALLIDO"))
            self.assertIn("cliente ausente", e.historial[0].observacion)

    def test_reprogramar_y_volver_a_en_ruta_con_agencia(self):
        e = envio("INTENTO_FALLIDO", agencia=4)
        fecha = datetime.now(timezone.utc) + timedelta(days=2)
        ejecutar(service.reprogramar_entrega, e, usuario("GS"), 1, ReprogramarEnvioPayload(nueva_fecha_entrega=fecha))
        self.assertEqual(e.estado, "REPROGRAMADO")
        ejecutar(service.cambiar_estado, e, usuario("GS"), 1, estado_payload("EN_RUTA"))
        self.assertEqual(e.estado, "EN_RUTA")
        self.assertEqual([(h.estado_anterior, h.estado_nuevo) for h in e.historial],
                         [("INTENTO_FALLIDO", "REPROGRAMADO"), ("REPROGRAMADO", "EN_RUTA")])

    def test_d_v_c_no_avanzan_un_envio_en_ruta_con_agencia(self):
        for rol in ("D", "V", "C"):
            for op in (lambda e, r: ejecutar(service.cambiar_estado, e, usuario(r), 1, estado_payload("ENTREGADO")),
                       lambda e, r: ejecutar(service.registrar_intento_fallido, e, usuario(r), 1, IntentoFallidoPayload(motivo="cliente ausente"))):
                e = envio("EN_RUTA", agencia=4)
                with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                    op(e, rol)
                self.assertEqual(cm.exception.status_code, 403)
                self.assertEqual((e.estado, e.historial), ("EN_RUTA", []))

    def test_transiciones_invalidas_siguen_dando_409(self):
        for estado, destino in (("ASIGNADO", "ENTREGADO"), ("EN_RUTA", "EN_RUTA"), ("ENTREGADO", "EN_RUTA"), ("CANCELADO", "EN_RUTA")):
            e = envio(estado, agencia=4)
            with self.subTest(estado=estado, destino=destino), self.assertRaises(HTTPException) as cm:
                ejecutar(service.cambiar_estado, e, usuario("GS"), 1, estado_payload(destino))
            self.assertEqual(cm.exception.status_code, 409)


class CancelacionSinCambios(unittest.TestCase):
    def test_admin_cancela_desde_los_mismos_estados_con_o_sin_agencia(self):
        for estado in ("PREPARANDO", "LISTO_ENVIO", "ASIGNADO", "INTENTO_FALLIDO", "REPROGRAMADO"):
            for transportista in ({}, {"agencia": 4}, {"repartidor": uuid.uuid4()}):
                e = envio(estado, **transportista)
                ejecutar(service.cambiar_estado, e, usuario("GS"), 1, estado_payload("CANCELADO", "motivo"))
                self.assertEqual(e.estado, "CANCELADO", (estado, transportista))
                self.assertEqual(e.id_agencia, transportista.get("agencia"))            # el snapshot se conserva

    def test_no_se_cancela_en_ruta_ni_terminales(self):
        for estado in ("EN_RUTA", "ENTREGADO", "CANCELADO"):
            e = envio(estado, agencia=4)
            with self.subTest(estado), self.assertRaises(HTTPException) as cm:
                ejecutar(service.cambiar_estado, e, usuario("GS"), 1, estado_payload("CANCELADO", "motivo"))
            self.assertEqual(cm.exception.status_code, 409)

    def test_d_nunca_cancela(self):
        d = usuario("D")
        e = envio("ASIGNADO", repartidor=d.id_usuario)
        with self.assertRaises(HTTPException) as cm:
            ejecutar(service.cambiar_estado, e, d, 1, estado_payload("CANCELADO", "motivo"))
        self.assertEqual(cm.exception.status_code, 403)

    def test_cancelar_exige_motivo(self):
        with self.assertRaises(Exception):
            estado_payload("CANCELADO")


class ExclusionAgenciaRepartidor(unittest.TestCase):
    def test_asignar_repartidor_a_un_envio_con_agencia_409(self):
        e = envio("LISTO_ENVIO", agencia=4)            # estado inconsistente forzado: igual se rechaza
        db = mock.Mock()
        with mock.patch.object(service, "obtener_envio", return_value=e):
            with self.assertRaises(HTTPException) as cm:
                service.asignar_repartidor(db, usuario("GS"), 1, AsignarEnvioPayload(id_repartidor=uuid.uuid4()))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("agencia", cm.exception.detail)
        self.assertEqual((e.estado, e.id_repartidor, e.historial), ("LISTO_ENVIO", None, []))
        db.commit.assert_not_called()

    def test_ninguna_transicion_toca_el_transportista(self):
        for transportista, otro in (({"agencia": 4}, "id_repartidor"), ({"repartidor": uuid.uuid4()}, "id_agencia")):
            e = envio("ASIGNADO", **transportista)
            antes = (e.id_agencia, e.id_repartidor)
            gs = usuario("GS")
            ejecutar(service.cambiar_estado, e, gs, 1, estado_payload("EN_RUTA"))
            ejecutar(service.registrar_intento_fallido, e, gs, 1, IntentoFallidoPayload(motivo="cliente ausente"))
            ejecutar(service.reprogramar_entrega, e, gs, 1, ReprogramarEnvioPayload(nueva_fecha_entrega=datetime.now(timezone.utc) + timedelta(days=1)))
            ejecutar(service.cambiar_estado, e, gs, 1, estado_payload("EN_RUTA"))
            ejecutar(service.cambiar_estado, e, gs, 1, estado_payload("ENTREGADO"))
            self.assertEqual((e.id_agencia, e.id_repartidor), antes)
            self.assertIsNone(getattr(e, otro))
            self.assertEqual(e.estado, "ENTREGADO")

    def test_un_envio_con_agencia_no_le_aparece_a_d(self):
        d = usuario("D")
        e = envio("ASIGNADO", agencia=4)
        e.repartidor = None
        with self.assertRaises(HTTPException) as cm:
            service.validar_acceso(e, d)
        self.assertEqual(cm.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
