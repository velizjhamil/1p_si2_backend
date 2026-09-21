# backend/tests/test_cu15_cu21_carrito_pagos.py
# Tests de integración para CU15 (Carrito Persistente) y CU21 (Cobro Efectivo y Notificaciones CU14/CU10).
import unittest
from datetime import date, datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.notificaciones.models import Notificacion
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import Carrito, DetalleReserva, ItemCarrito, Reserva, TransaccionPago, Venta
from app.api.v1.endpoints.carrito import (
    agregar_item_carrito,
    actualizar_item_carrito,
    eliminar_item_carrito,
    obtener_carrito,
    vaciar_carrito,
)
from app.api.v1.endpoints.reservas import crear_reserva
from app.api.v1.endpoints.ventas import cobrar_en_efectivo, procesar_checkout
from app.schemas.carrito import ItemCarritoCreate, ItemCarritoUpdate
from app.schemas.reserva import ReservaCreatePayload, ReservaItemPayload
from app.schemas.venta import CheckoutItemPayload, CheckoutPayload, CobroEfectivoPayload, DatosEntregaPayload


class TestCU15CU21CarritoYPagos(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

        # Obtener roles
        self.rol_c = self.db.query(Rol).filter(Rol.nombre_rol == "C").first()
        self.rol_v = self.db.query(Rol).filter(Rol.nombre_rol == "V").first()
        self.rol_gs = self.db.query(Rol).filter(Rol.nombre_rol == "GS").first()
        self.rol_asu = self.db.query(Rol).filter(Rol.nombre_rol == "ASU").first()

        # Obtener sucursales
        self.sucursal_1 = self.db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()
        self.sucursal_2 = (
            self.db.query(Sucursal)
            .filter(
                Sucursal.is_active.is_(True),
                Sucursal.codigo_sucursal != self.sucursal_1.codigo_sucursal,
            )
            .first()
        )
        if not self.sucursal_2:
            self.sucursal_2 = self.sucursal_1

        # Usuario Cliente
        self.cliente = (
            self.db.query(Usuario)
            .filter(Usuario.id_rol == self.rol_c.id_rol, Usuario.estado.is_(True))
            .first()
        )

        # Vendedor de sucursal 1
        self.vendedor_suc1 = (
            self.db.query(Usuario)
            .filter(
                Usuario.id_rol == self.rol_v.id_rol,
                Usuario.id_sucursal == self.sucursal_1.codigo_sucursal,
                Usuario.estado.is_(True),
            )
            .first()
        )
        if not self.vendedor_suc1:
            self.vendedor_suc1 = (
                self.db.query(Usuario)
                .filter(Usuario.id_rol == self.rol_v.id_rol, Usuario.estado.is_(True))
                .first()
            )
            if self.vendedor_suc1:
                self.vendedor_suc1.id_sucursal = self.sucursal_1.codigo_sucursal
                self.db.commit()

        # Producto con stock disponible
        self.producto = (
            self.db.query(Producto)
            .filter(Producto.estado == "Activo", Producto.stock_total >= 10)
            .first()
        )

        # Asegurar stock en sucursal 1
        inv1 = (
            self.db.query(InventarioSucursal)
            .filter(
                InventarioSucursal.id_sucursal == self.sucursal_1.codigo_sucursal,
                InventarioSucursal.id_producto == self.producto.id_producto,
            )
            .first()
        )
        if not inv1:
            inv1 = InventarioSucursal(
                id_sucursal=self.sucursal_1.codigo_sucursal,
                id_producto=self.producto.id_producto,
                stock=20,
            )
            self.db.add(inv1)
            self.db.commit()
        elif inv1.stock < 10:
            inv1.stock = 20
            self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_flujo_completo_carrito_cu15(self):
        """CU15: Agregar, consultar, actualizar cantidad y vaciar carrito en DB."""
        # 1. Vaciar carrito previo del cliente
        vaciar_carrito(db=self.db, usuario_actual=self.cliente)

        # 2. Agregar ítem con variante
        payload_add = ItemCarritoCreate(
            id_producto=self.producto.id_producto,
            cantidad=2,
            talla="M",
            color="Azul",
            id_sucursal_preferida=self.sucursal_1.codigo_sucursal,
        )
        resp_add = agregar_item_carrito(
            payload=payload_add, db=self.db, usuario_actual=self.cliente
        )
        self.assertEqual(resp_add["status"], "success")
        data_cart = resp_add["data"]
        self.assertEqual(data_cart["cantidad_total_items"], 2)
        self.assertEqual(len(data_cart["items"]), 1)
        item_id = data_cart["items"][0]["id_item"]
        self.assertTrue(data_cart["todos_disponibles"])

        # 3. Consultar carrito
        resp_get = obtener_carrito(
            id_sucursal=self.sucursal_1.codigo_sucursal,
            db=self.db,
            usuario_actual=self.cliente,
        )
        self.assertEqual(resp_get["status"], "success")
        self.assertEqual(resp_get["data"]["id_carrito"], data_cart["id_carrito"])

        # 4. Actualizar cantidad
        payload_upd = ItemCarritoUpdate(cantidad=3)
        resp_upd = actualizar_item_carrito(
            id_item=item_id,
            payload=payload_upd,
            db=self.db,
            usuario_actual=self.cliente,
        )
        self.assertEqual(resp_upd["status"], "success")
        self.assertEqual(resp_upd["data"]["cantidad_total_items"], 3)

        # 5. Eliminar ítem
        resp_del = eliminar_item_carrito(
            id_item=item_id, db=self.db, usuario_actual=self.cliente
        )
        self.assertEqual(resp_del["status"], "success")
        self.assertEqual(resp_del["data"]["cantidad_total_items"], 0)

    def test_02_reserva_cu14_dispara_notificacion_cu10_y_liquidacion_efectivo_cu21(self):
        """CU14 -> CU10 -> CU21: Crear reserva, verificar notificación a tienda y liquidar en efectivo."""
        # 1. Crear reserva
        payload_reserva = ReservaCreatePayload(
            id_sucursal=self.sucursal_1.codigo_sucursal,
            fecha_expiracion=date.today() + timedelta(days=3),
            items=[
                ReservaItemPayload(
                    id_producto=self.producto.id_producto,
                    cantidad=1,
                    precio_unitario=float(self.producto.precio_venta),
                )
            ],
        )
        resp_res = crear_reserva(
            payload=payload_reserva,
            db=self.db,
            usuario_actual=self.cliente,
        )
        self.assertEqual(resp_res["status"], "success")
        reserva_id = resp_res["data"]["id_reserva"]
        self.assertEqual(resp_res["data"]["estado"], "PENDIENTE")

        # 2. Verificar que se emitió la Notificación (CU10) a empleados de la sucursal 1
        noti = (
            self.db.query(Notificacion)
            .filter(
                Notificacion.referencia_tipo == "reserva",
                Notificacion.referencia_id == str(reserva_id),
            )
            .first()
        )
        self.assertIsNotNone(noti, "Debe existir una notificación generada para la reserva")
        self.assertEqual(noti.tipo, "PEDIDO")
        self.assertIn(str(reserva_id), noti.titulo)

        # 3. Liquidar reserva en efectivo en mostrador por el Vendedor (CU21)
        total_reserva = float(resp_res["data"]["total_estimado"])
        payload_cobro = CobroEfectivoPayload(
            id_reserva=reserva_id,
            monto_recibido=total_reserva + 50.0,
        )
        resp_cobro = cobrar_en_efectivo(
            payload=payload_cobro,
            db=self.db,
            usuario_actual=self.vendedor_suc1,
        )
        self.assertEqual(resp_cobro["status"], "success")
        comprobante = resp_cobro["data"]
        self.assertEqual(comprobante["reserva_liquidada_id"], reserva_id)
        self.assertEqual(comprobante["cambio_devuelto"], 50.0)
        self.assertIn("RECIBO-ATT-", comprobante["codigo_comprobante"])
        self.assertEqual(comprobante["venta"]["estado_pago"], "PAGADO")
        self.assertEqual(comprobante["venta"]["metodo_pago"], "EFECTIVO")

        # 4. Verificar que la reserva quedó COMPLETADA en DB
        reserva_db = self.db.get(Reserva, reserva_id)
        self.assertEqual(reserva_db.estado, "COMPLETADA")

        # 5. Verificar transacción de pago registrada
        txn = (
            self.db.query(TransaccionPago)
            .filter(TransaccionPago.id_venta == comprobante["venta"]["id_venta"])
            .first()
        )
        self.assertIsNotNone(txn)
        self.assertEqual(txn.estado, "PAGADO")
        self.assertEqual(txn.metodo_pago, "EFECTIVO")

    def test_03_vendedor_otra_sucursal_no_puede_cobrar_reserva_ajena(self):
        """Aislamiento multi-sucursal: Vendedor de tienda B no puede liquidar reserva de tienda A."""
        if self.sucursal_1.codigo_sucursal == self.sucursal_2.codigo_sucursal:
            return  # Solo 1 sucursal en entorno, saltar

        # Crear reserva en sucursal 1
        payload_reserva = ReservaCreatePayload(
            id_sucursal=self.sucursal_1.codigo_sucursal,
            fecha_expiracion=date.today() + timedelta(days=2),
            items=[
                ReservaItemPayload(
                    id_producto=self.producto.id_producto,
                    cantidad=1,
                    precio_unitario=float(self.producto.precio_venta),
                )
            ],
        )
        resp_res = crear_reserva(
            payload=payload_reserva,
            db=self.db,
            usuario_actual=self.cliente,
        )
        reserva_id = resp_res["data"]["id_reserva"]

        # Crear o simular vendedor con id_sucursal = sucursal_2
        vendedor_suc2 = Usuario(
            id_usuario=self.vendedor_suc1.id_usuario,
            nombre="Vendedor Suc 2",
            correo="vendedor_suc2_test@attention.com",
            id_rol=self.rol_v.id_rol,
            id_sucursal=self.sucursal_2.codigo_sucursal,
            rol=self.rol_v,
        )

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            cobrar_en_efectivo(
                payload=CobroEfectivoPayload(id_reserva=reserva_id),
                db=self.db,
                usuario_actual=vendedor_suc2,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("No puede cobrar una reserva de otra sucursal", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
