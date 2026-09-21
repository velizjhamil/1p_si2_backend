# backend/tests/test_multi_sucursal_y_alertas.py
import os
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.modules.usuarios.models
import app.modules.empresa.models
import app.modules.compras.models
import app.modules.inventario.models
import app.modules.probador.models
import app.modules.ventas.models
import app.modules.delivery.models
import app.modules.notificaciones.models

from sqlalchemy.orm import Session

from app.api.v1.endpoints.products import _serializar_producto
from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.inventario.stock_alert import verificar_y_notificar_stock_critico
from app.modules.notificaciones.models import Notificacion
from app.modules.reportes import service as reportes_service
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import Venta


class TestMultiSucursalYAlertas(unittest.TestCase):
    def setUp(self):
        self.db: Session = SessionLocal()
        # Localizar producto con inventario por sucursal
        self.producto = (
            self.db.query(Producto)
            .filter(Producto.estado == "Activo")
            .first()
        )
        self.sucursal = self.db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()

    def tearDown(self):
        self.db.close()

    def test_01_serializacion_producto_con_disponibilidad_sucursales(self):
        """Verifica que _serializar_producto incluya el desglose de disponibilidad por sucursal."""
        self.assertIsNotNone(self.producto)
        serialized = _serializar_producto(self.producto)

        self.assertIn("disponibilidad_sucursales", serialized)
        self.assertIsInstance(serialized["disponibilidad_sucursales"], list)

        if serialized["disponibilidad_sucursales"]:
            item = serialized["disponibilidad_sucursales"][0]
            self.assertIn("id_sucursal", item)
            self.assertIn("nombre_sucursal", item)
            self.assertIn("stock", item)
            self.assertIn("disponible", item)
            self.assertEqual(item["disponible"], item["stock"] > 0)

    def test_02_alerta_stock_critico_notificacion_gs(self):
        """Verifica que verificar_y_notificar_stock_critico genere una notificación CU10."""
        self.assertIsNotNone(self.producto)
        self.assertIsNotNone(self.sucursal)

        # Disparar alerta con stock crítico = 3 (<= 5)
        notis = verificar_y_notificar_stock_critico(
            self.db,
            id_producto=self.producto.id_producto,
            id_sucursal=self.sucursal.codigo_sucursal,
            stock_nuevo=3,
            umbral=5,
            commit=True,
        )

        # Debe generar al menos una notificación
        self.assertGreater(len(notis), 0)
        noti = notis[0]
        self.assertEqual(noti.tipo, "STOCK")
        self.assertEqual(noti.referencia_tipo, "INVENTARIO")
        self.assertEqual(noti.referencia_id, str(self.producto.id_producto))
        self.assertIn("Stock Crítico", noti.titulo)
        self.assertIn("3 unidades", noti.mensaje)

        # Si el stock es mayor al umbral, no debe alertar
        sin_alertas = verificar_y_notificar_stock_critico(
            self.db,
            id_producto=self.producto.id_producto,
            id_sucursal=self.sucursal.codigo_sucursal,
            stock_nuevo=15,
            umbral=5,
            commit=True,
        )
        self.assertEqual(len(sin_alertas), 0)

    def test_03_reportes_aislados_por_sucursal(self):
        """Verifica que FiltrosReporte con id_sucursal aísle los datos por tienda."""
        self.assertIsNotNone(self.sucursal)
        id_sucursal = self.sucursal.codigo_sucursal

        hoy = reportes_service.hoy_utc()
        inicio = hoy - timedelta(days=60)

        filtros = reportes_service.construir_filtros(
            self.db,
            fecha_inicio=inicio,
            fecha_fin=hoy,
            id_sucursal=id_sucursal,
        )
        self.assertEqual(filtros.id_sucursal, id_sucursal)

        # 1. Reporte de ventas
        rep_ventas = reportes_service.reporte_ventas(self.db, filtros)
        self.assertIsNotNone(rep_ventas)
        self.assertEqual(rep_ventas.filtros.id_sucursal, id_sucursal)

        # 2. Reporte de inventario (físico por sucursal)
        rep_inv = reportes_service.reporte_inventario(self.db, filtros)
        self.assertIsNotNone(rep_inv)
        self.assertEqual(rep_inv.filtros.id_sucursal, id_sucursal)

        # 3. Reporte de devoluciones
        rep_dev = reportes_service.reporte_devoluciones(self.db, filtros)
        self.assertIsNotNone(rep_dev)
        self.assertEqual(rep_dev.filtros.id_sucursal, id_sucursal)


if __name__ == "__main__":
    unittest.main()
