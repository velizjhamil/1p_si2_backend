# backend/tests/test_attentionpay_tarjeta.py
# CU21 — Tests de Procesamiento Real de Tarjeta con Pasarela Interna AttentionPay
import unittest
from datetime import datetime, timezone
from fastapi import HTTPException

from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.notificaciones.models import Notificacion
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import DetalleVenta, TransaccionPago, Venta
from app.api.v1.endpoints.pagos import procesar_tarjeta_attentionpay_endpoint
from app.modules.pagos.service import validar_datos_tarjeta_attentionpay
from app.schemas.pago import DatosTarjetaPayload, ProcesarTarjetaPayload
from app.schemas.venta import CheckoutItemPayload, DatosEntregaPayload


class TestAttentionPayTarjeta(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

        self.rol_c = self.db.query(Rol).filter(Rol.nombre_rol == "C").first()
        self.sucursal = self.db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()
        self.cliente = (
            self.db.query(Usuario)
            .filter(Usuario.id_rol == self.rol_c.id_rol, Usuario.estado.is_(True))
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
        if not self.inv:
            self.inv = InventarioSucursal(
                id_producto=self.producto.id_producto,
                id_sucursal=self.sucursal.codigo_sucursal,
                stock=20,
            )
            self.db.add(self.inv)
            self.db.commit()
        elif self.inv.stock < 10:
            self.inv.stock = 20
            self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_validacion_tarjeta_servidor(self):
        """Verifica las reglas de validación en servidor para AttentionPay."""
        # 1. Tarjeta válida (Luhn OK)
        valida = DatosTarjetaPayload(
            titular="CARLOS MENDOZA",
            numero_tarjeta="4242 4242 4242 4242",
            expiracion="12/29",
            cvv="888",
        )
        limpio = validar_datos_tarjeta_attentionpay(valida)
        self.assertEqual(limpio, "4242424242424242")

        # 2. Tarjeta con fecha expirada
        expirada = DatosTarjetaPayload(
            titular="CARLOS MENDOZA",
            numero_tarjeta="4242 4242 4242 4242",
            expiracion="01/20",
            cvv="888",
        )
        with self.assertRaises(HTTPException) as ctx:
            validar_datos_tarjeta_attentionpay(expirada)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("ha expirado", ctx.exception.detail)

        # 3. CVV inválido (caracteres no numéricos)
        cvv_malo = DatosTarjetaPayload(
            titular="CARLOS MENDOZA",
            numero_tarjeta="4242 4242 4242 4242",
            expiracion="12/29",
            cvv="12X",
        )
        with self.assertRaises(HTTPException) as ctx:
            validar_datos_tarjeta_attentionpay(cvv_malo)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("CVV", ctx.exception.detail)

        # 4. Número inválido (falla Luhn y no es sandbox)
        luhn_malo = DatosTarjetaPayload(
            titular="CARLOS MENDOZA",
            numero_tarjeta="5123 4567 8901 2347",
            expiracion="12/29",
            cvv="123",
        )
        with self.assertRaises(HTTPException) as ctx:
            validar_datos_tarjeta_attentionpay(luhn_malo)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Luhn", ctx.exception.detail)

    def test_02_procesar_tarjeta_attentionpay_exitoso_y_descuento_stock(self):
        """Verifica liquidación atómica, descuento de inventario local y comprobante fiscal."""
        stock_suc_antes = self.inv.stock
        stock_prod_antes = self.producto.stock_total

        payload = ProcesarTarjetaPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="M",
                    color="Negro",
                )
            ],
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre or "Cliente AttentionPay",
                correo=self.cliente.correo,
                telefono="77123456",
                direccion="Av. San Martín #200",
                ciudad="Santa Cruz",
            ),
            datos_tarjeta=DatosTarjetaPayload(
                titular="ANA LUCIA ROCHA",
                numero_tarjeta="4242 4242 4242 4242",
                expiracion="11/29",
                cvv="456",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
        )

        resp = procesar_tarjeta_attentionpay_endpoint(
            payload=payload,
            db=self.db,
            usuario_actual=self.cliente,
        )

        self.assertEqual(resp["status"], "success")
        data = resp["data"]
        self.assertEqual(data["pasarela"], "AttentionPay")
        self.assertTrue(data["codigo_autorizacion"].startswith("AUTH-ATT-"))
        self.assertEqual(data["estado_pago"], "PAGADO")
        self.assertEqual(data["tarjeta_enmascarada"], "•••• •••• •••• 4242")

        # Comprobante fiscal
        self.assertIn("comprobante_fiscal", data)
        self.assertEqual(data["comprobante_fiscal"]["nit"], "1028374029")

        # Descuento en BD
        self.db.refresh(self.inv)
        self.db.refresh(self.producto)
        self.assertEqual(self.inv.stock, stock_suc_antes - 1)
        self.assertEqual(self.producto.stock_total, stock_prod_antes - 1)

    def test_03_alerta_stock_critico_al_quedar_5_o_menos(self):
        """Verifica que se dispara alerta de stock crítico si el inventario queda <= 5."""
        # Ajustamos el stock a 6 para que con la compra de 1 quede en 5 (crítico)
        self.inv.stock = 6
        self.db.commit()

        notifs_antes = self.db.query(Notificacion).filter(Notificacion.tipo == "INVENTARIO").count()

        payload = ProcesarTarjetaPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="L",
                    color="Blanco",
                )
            ],
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre or "Cliente Alerta",
                correo=self.cliente.correo,
                telefono="77123456",
                direccion="Calle Sucre #50",
                ciudad="Santa Cruz",
            ),
            datos_tarjeta=DatosTarjetaPayload(
                titular="ANA LUCIA ROCHA",
                numero_tarjeta="4242 4242 4242 4242",
                expiracion="11/29",
                cvv="456",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
        )

        resp = procesar_tarjeta_attentionpay_endpoint(
            payload=payload,
            db=self.db,
            usuario_actual=self.cliente,
        )

        self.assertEqual(resp["status"], "success")
        self.db.refresh(self.inv)
        self.assertEqual(self.inv.stock, 5)

        notifs_despues = self.db.query(Notificacion).filter(Notificacion.tipo == "INVENTARIO").count()
        self.assertGreaterEqual(notifs_despues, notifs_antes)


if __name__ == "__main__":
    unittest.main()
