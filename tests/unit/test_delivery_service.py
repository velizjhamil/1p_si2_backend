# Pruebas unitarias AISLADAS de CU18 (app/modules/delivery/service.py).
# Sin base de datos: la sesion es un Mock y las entidades son objetos en
# memoria, asi que NO tocan Supabase ni ningun dato real.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_delivery_service.py
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
from app.modules.delivery.models import (  # noqa: E402
    ESTADOS_ENVIO,
    ESTADOS_TERMINALES_ENVIO,
    TRANSICIONES_ENVIO,
    Envio,
)
from app.modules.empresa.models import Sucursal  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.envio import AsignarEnvioPayload, ConfirmarPreparacionPayload  # noqa: E402


def usuario(rol: str, activo: bool = True) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A",
        correo=f"{rol}@x.test", estado=activo, rol=Rol(nombre_rol=rol),
    )


def envio(estado: str = "PREPARANDO") -> Envio:
    return Envio(id_envio=1, id_venta=1, estado=estado, intentos_fallidos=0)


class ConfirmarPreparacionSucursal(unittest.TestCase):
    """Validacion de la sucursal al confirmar la preparacion (400)."""

    def _ejecutar(self, sucursal, e=None):
        e = e or envio()
        db = mock.Mock()
        db.get.return_value = sucursal
        with mock.patch.object(service, "obtener_envio", return_value=e):
            payload = ConfirmarPreparacionPayload(codigo_sucursal=9)
            return e, db, lambda: service.confirmar_preparacion(db, usuario("GS"), 1, payload)

    def test_sucursal_inactiva_se_rechaza(self):
        suc = Sucursal(codigo_sucursal=9, nombre="Sucursal Cerrada", is_active=False)
        e, db, run = self._ejecutar(suc)
        with mock.patch.object(service, "obtener_envio", return_value=e):
            with self.assertRaises(HTTPException) as ctx:
                run()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("inactiva", ctx.exception.detail)
        self.assertIn("Sucursal Cerrada", ctx.exception.detail)
        # no cambio nada: ni estado, ni sucursal, ni historial, ni commit
        self.assertEqual(e.estado, "PREPARANDO")
        self.assertIsNone(e.codigo_sucursal)
        self.assertEqual(len(e.historial), 0)
        db.commit.assert_not_called()

    def test_sucursal_inexistente_se_rechaza(self):
        e, db, run = self._ejecutar(None)
        with mock.patch.object(service, "obtener_envio", return_value=e):
            with self.assertRaises(HTTPException) as ctx:
                run()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("No existe la sucursal", ctx.exception.detail)
        db.commit.assert_not_called()

    def test_sucursal_activa_confirma_y_hace_un_solo_commit(self):
        suc = Sucursal(codigo_sucursal=9, nombre="Sucursal Ok", is_active=True)
        e, db, run = self._ejecutar(suc)
        with mock.patch.object(service, "obtener_envio", return_value=e):
            run()
        self.assertEqual(e.estado, "LISTO_ENVIO")
        self.assertEqual(e.codigo_sucursal, 9)
        self.assertEqual([(h.estado_anterior, h.estado_nuevo) for h in e.historial],
                         [("PREPARANDO", "LISTO_ENVIO")])
        db.commit.assert_called_once()


class AsignarRepartidor(unittest.TestCase):
    """Validaciones del repartidor sin usar cuentas reales."""

    def _asignar(self, repartidor):
        e = envio("LISTO_ENVIO")
        db = mock.Mock()
        db.get.return_value = repartidor
        payload = AsignarEnvioPayload(id_repartidor=uuid.uuid4())
        with mock.patch.object(service, "obtener_envio", return_value=e):
            return e, db, payload

    def test_repartidor_inactivo(self):
        e, db, payload = self._asignar(usuario("D", activo=False))
        with mock.patch.object(service, "obtener_envio", return_value=e):
            with self.assertRaises(HTTPException) as ctx:
                service.asignar_repartidor(db, usuario("GS"), 1, payload)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("inactivo", ctx.exception.detail)
        self.assertEqual(e.estado, "LISTO_ENVIO")

    def test_usuario_que_no_es_repartidor(self):
        e, db, payload = self._asignar(usuario("V"))
        with mock.patch.object(service, "obtener_envio", return_value=e):
            with self.assertRaises(HTTPException) as ctx:
                service.asignar_repartidor(db, usuario("GS"), 1, payload)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("no es repartidor", ctx.exception.detail)


class MaquinaDeEstados(unittest.TestCase):
    def test_todos_los_estados_tienen_transiciones_definidas(self):
        self.assertEqual(set(TRANSICIONES_ENVIO), set(ESTADOS_ENVIO))

    def test_terminales_no_tienen_salida(self):
        for est in ESTADOS_TERMINALES_ENVIO:
            self.assertEqual(TRANSICIONES_ENVIO[est], ())

    def test_intento_fallido_no_es_cancelado(self):
        self.assertNotIn("INTENTO_FALLIDO", ESTADOS_TERMINALES_ENVIO)
        self.assertIn("REPROGRAMADO", TRANSICIONES_ENVIO["INTENTO_FALLIDO"])
        self.assertIn("CANCELADO", ESTADOS_TERMINALES_ENVIO)

    def test_reprogramado_solo_tras_intento_fallido(self):
        origenes = [o for o, d in TRANSICIONES_ENVIO.items() if "REPROGRAMADO" in d]
        self.assertEqual(origenes, ["INTENTO_FALLIDO"])

    def test_entregado_solo_desde_en_ruta(self):
        origenes = [o for o, d in TRANSICIONES_ENVIO.items() if "ENTREGADO" in d]
        self.assertEqual(origenes, ["EN_RUTA"])

    def test_cancelado_no_repone_stock_ni_toca_pagos(self):
        """Decision de alcance: cancelar solo cierra el flujo logistico.

        El service de delivery no importa inventario ni devoluciones.
        """
        fuente = Path(service.__file__).read_text(encoding="utf-8")
        for prohibido in ("MovimientoInventario", "stock_total", "Devolucion", "estado_pago ="):
            self.assertNotIn(prohibido, fuente)


class Fechas(unittest.TestCase):
    def test_naive_se_asume_utc(self):
        f = service._a_utc(datetime(2030, 1, 1, 12, 0))
        self.assertEqual(f.tzinfo, timezone.utc)
        self.assertEqual(f.hour, 12)

    def test_offset_se_convierte_a_utc(self):
        bolivia = timezone(timedelta(hours=-4))
        f = service._a_utc(datetime(2030, 1, 1, 8, 0, tzinfo=bolivia))
        self.assertEqual((f.tzinfo, f.hour), (timezone.utc, 12))

    def test_fecha_pasada_400_y_futura_ok(self):
        with self.assertRaises(HTTPException) as ctx:
            service._exigir_fecha_futura(datetime.now(timezone.utc) - timedelta(minutes=1), "F")
        self.assertEqual(ctx.exception.status_code, 400)
        futura = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertEqual(service._exigir_fecha_futura(futura, "F"), futura)


if __name__ == "__main__":
    unittest.main(verbosity=2)
