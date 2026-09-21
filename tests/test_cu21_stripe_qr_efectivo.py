# backend/tests/test_cu21_stripe_qr_efectivo.py
# CU21 — Validación de Métodos de Pago:
# 1. Pago con Tarjeta (Stripe API Real)
# 2. Pago por QR Simple Institucional con Validación de Abono
# 3. Pago en Efectivo en Caja de Sucursal
# Transaccionalidad atómica, descuento de inventario por sucursal y comprobantes fiscales.

import unittest
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import DetalleVenta, TransaccionPago, Venta
from app.api.v1.endpoints.pagos import cobrar_con_tarjeta, confirmar_abono_qr_endpoint, procesar_pago
from app.api.v1.endpoints.ventas import cobrar_en_efectivo
from app.schemas.pago import (
    ConfirmarAbonoQRPayload,
    DatosTarjetaPayload,
    PagoTarjetaPayload,
    ProcesarPagoPayload,
)
from app.schemas.venta import CheckoutItemPayload, CobroEfectivoPayload, DatosEntregaPayload


class TestCU21PasarelaPagosCompleta(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

        self.rol_c = self.db.query(Rol).filter(Rol.nombre_rol == "C").first()
        self.rol_v = self.db.query(Rol).filter(Rol.nombre_rol == "V").first()

        self.sucursal = self.db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()
        self.cliente = (
            self.db.query(Usuario)
            .filter(Usuario.id_rol == self.rol_c.id_rol, Usuario.estado.is_(True))
            .first()
        )
        self.vendedor = (
            self.db.query(Usuario)
            .filter(
                Usuario.id_rol == self.rol_v.id_rol,
                Usuario.id_sucursal == self.sucursal.codigo_sucursal,
                Usuario.estado.is_(True),
            )
            .first()
        )

        self.producto = (
            self.db.query(Producto)
            .filter(Producto.estado == "Activo", Producto.stock_total > 5)
            .first()
        )
        self.inv = (
            self.db.query(InventarioSucursal)
            .filter(
                InventarioSucursal.id_producto == self.producto.id_producto,
                InventarioSucursal.id_sucursal == self.sucursal.codigo_sucursal,
            )
            .first()
        )
        if not self.inv or self.inv.stock < 3:
            if not self.inv:
                self.inv = InventarioSucursal(
                    id_producto=self.producto.id_producto,
                    id_sucursal=self.sucursal.codigo_sucursal,
                    stock=20,
                )
                self.db.add(self.inv)
            else:
                self.inv.stock = 20
            self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_pago_tarjeta_stripe_real(self):
        """CU21: Verifica que el cobro con tarjeta vía Stripe API real procesa exitosamente."""
        stock_suc_antes = self.inv.stock
        stock_prod_antes = self.producto.stock_total

        payload = PagoTarjetaPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="M",
                    color="Negro",
                )
            ],
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre or "Cliente Stripe",
                correo=self.cliente.correo,
                telefono="77123456",
                direccion="Av. Banzer Km 5",
                ciudad="Santa Cruz",
            ),
            datos_tarjeta=DatosTarjetaPayload(
                titular="JUAN PEREZ",
                numero_tarjeta="4242 4242 4242 4242",
                expiracion="12/28",
                cvv="123",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
        )

        resp = cobrar_con_tarjeta(payload=payload, db=self.db, usuario_actual=self.cliente)
        self.assertEqual(resp["status"], "success")
        data = resp["data"]

        # Validar identificador de Stripe y factura
        self.assertIn("stripe_payment_intent_id", data)
        self.assertTrue(data["stripe_payment_intent_id"].startswith("pi_"))
        self.assertEqual(data["estado_pago"], "PAGADO")
        self.assertEqual(data["metodo_pago"], "TARJETA")

        # Comprobante fiscal
        self.assertIn("comprobante_fiscal", data)
        self.assertEqual(data["comprobante_fiscal"]["nit"], "1028374029")

        # Validar descuento atómico de inventario
        self.db.refresh(self.inv)
        self.db.refresh(self.producto)
        self.assertEqual(self.inv.stock, stock_suc_antes - 1)
        self.assertEqual(self.producto.stock_total, stock_prod_antes - 1)

    def test_02_pago_qr_institucional_y_validacion_abono(self):
        """CU21: Flujo completo de QR — Generación en estado PENDIENTE y posterior confirmación de abono."""
        stock_suc_antes = self.inv.stock

        payload_init = ProcesarPagoPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="L",
                    color="Azul",
                )
            ],
            metodo_pago="QR",
            datos_entrega=DatosEntregaPayload(
                nombre_cliente="Cliente QR",
                correo="qr@attention.bo",
                telefono="78901234",
                direccion="Calle Sucre 45",
                ciudad="Santa Cruz",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
            tipo_venta="ONLINE",
        )

        # 1. Generación de orden QR
        res_init = procesar_pago(payload=payload_init, db=self.db, usuario_actual=self.cliente)
        self.assertEqual(res_init["status"], "success")
        data_init = res_init["data"]
        self.assertEqual(data_init["estado_pago"], "PENDIENTE")

        codigo_txn = data_init["transaccion"]["codigo_transaccion"]
        self.assertTrue(codigo_txn.startswith("TXN-ATT-"))

        # El stock no se descuenta aún
        self.db.refresh(self.inv)
        self.assertEqual(self.inv.stock, stock_suc_antes)

        # 2. Confirmación / validación del abono por QR
        payload_conf = ConfirmarAbonoQRPayload(
            codigo_transaccion=codigo_txn,
            comprobante_referencia="DEP-BNB-7749102",
            notas="Transferencia confirmada por app móvil",
        )
        res_conf = confirmar_abono_qr_endpoint(
            payload=payload_conf, db=self.db, usuario_actual=self.cliente
        )
        self.assertEqual(res_conf["status"], "success")
        data_conf = res_conf["data"]
        self.assertEqual(data_conf["estado_pago"], "PAGADO")

        # Comprobante fiscal emitido
        self.assertIn("comprobante_fiscal", data_conf)
        self.assertEqual(data_conf["comprobante_fiscal"]["nro_factura"], data_init["codigo_venta"])

        # Stock descontado tras la confirmación del abono
        self.db.refresh(self.inv)
        self.assertEqual(self.inv.stock, stock_suc_antes - 1)

    def test_03_pago_efectivo_en_caja_sucursal(self):
        """CU21: Orden generada en efectivo y liquidada por el vendedor en caja con cambio exacto."""
        payload_init = ProcesarPagoPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="S",
                    color="Blanco",
                )
            ],
            metodo_pago="EFECTIVO",
            datos_entrega=DatosEntregaPayload(
                nombre_cliente="Cliente Efectivo",
                correo="efectivo@attention.bo",
                telefono="71234567",
                direccion="Av. Cristo Redentor 123",
                ciudad="Santa Cruz",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
            tipo_venta="ONLINE",
        )

        res_init = procesar_pago(payload=payload_init, db=self.db, usuario_actual=self.cliente)
        id_venta = res_init["data"]["id_venta"]
        total_pagar = res_init["data"]["total"]

        # Vendedor cobra en caja de sucursal
        payload_cobro = CobroEfectivoPayload(
            id_venta=id_venta,
            monto_recibido=total_pagar + 50.0,
            observaciones="Pago en caja sucursal física",
        )
        res_cobro = cobrar_en_efectivo(
            payload=payload_cobro, db=self.db, usuario_actual=self.vendedor
        )
        self.assertEqual(res_cobro["status"], "success")
        data_cobro = res_cobro["data"]
        self.assertEqual(data_cobro["cambio_devuelto"], 50.0)
        self.assertEqual(data_cobro["venta"]["estado_pago"], "PAGADO")
        self.assertTrue(data_cobro["codigo_comprobante"].startswith("RECIBO-ATT-"))


if __name__ == "__main__":
    unittest.main()
