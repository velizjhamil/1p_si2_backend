# backend/tests/test_pasarela_pagos.py
import os
import sys
import unittest

# Asegura que el backend esté en el sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.modules.usuarios.models
import app.modules.empresa.models
import app.modules.compras.models
import app.modules.inventario.models
import app.modules.probador.models
import app.modules.ventas.models
import app.modules.delivery.models

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.inventario.models import InventarioSucursal, MovimientoInventario, Producto
from app.modules.pagos.service import (
    calcular_firma_webhook,
    iniciar_transaccion_pago,
    obtener_estado_pago,
    procesar_webhook_pasarela,
    verificar_firma_webhook,
)
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import TransaccionPago, Venta
from app.schemas.pago import (
    DatosTarjetaPayload,
    ProcesarPagoPayload,
    WebhookPayload,
)
from app.schemas.venta import CheckoutItemPayload, DatosEntregaPayload


class TestPasarelaPagos(unittest.TestCase):
    def setUp(self):
        self.db: Session = SessionLocal()
        # Obtener un cliente real (rol C)
        self.cliente = (
            self.db.query(Usuario)
            .join(Usuario.rol)
            .filter(Usuario.rol.has(nombre_rol="C"))
            .first()
        )
        # Obtener un producto activo con stock
        self.producto = (
            self.db.query(Producto)
            .filter(Producto.estado == "Activo", Producto.stock_total > 5)
            .first()
        )
        # Obtener un inventario físico de sucursal para ese producto
        self.inv_suc = (
            self.db.query(InventarioSucursal)
            .filter(InventarioSucursal.id_producto == self.producto.id_producto)
            .first()
        )

    def tearDown(self):
        self.db.close()

    def test_01_firma_hmac_sha256(self):
        """Verifica la generación y validación de firmas criptográficas HMAC."""
        codigo = "TXN-ATT-TEST-001"
        monto = 150.00
        status = "APPROVED"

        firma = calcular_firma_webhook(codigo, monto, status)
        self.assertTrue(isinstance(firma, str))
        self.assertEqual(len(firma), 64)  # SHA-256 hex length

        # Validación exitosa
        self.assertTrue(verificar_firma_webhook(codigo, monto, status, firma))

        # Validación fallida por monto manipulado
        self.assertFalse(verificar_firma_webhook(codigo, 200.00, status, firma))

        # Validación fallida por status alterado
        self.assertFalse(verificar_firma_webhook(codigo, monto, "REJECTED", firma))

        # Validación fallida por firma corrupta
        self.assertFalse(verificar_firma_webhook(codigo, monto, status, "firma_falsa_invalida"))

    def test_02_iniciar_pago_qr_y_webhook_aprobado(self):
        """Inicia un pago por QR y confirma mediante Webhook atómico."""
        self.assertIsNotNone(self.cliente, "Debe existir al menos un usuario con rol C")
        self.assertIsNotNone(self.producto, "Debe existir al menos un producto activo con stock")

        stock_inicial_prod = self.producto.stock_total
        id_sucursal_test = self.inv_suc.id_sucursal if self.inv_suc else None
        stock_inicial_suc = self.inv_suc.stock if self.inv_suc else None

        cant_compra = 2

        payload = ProcesarPagoPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=cant_compra,
                    talla="M",
                    color="Azul",
                )
            ],
            metodo_pago="QR",
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre,
                correo=self.cliente.correo,
                telefono="70012345",
                direccion="Av. San Martín 123",
                ciudad="Santa Cruz",
                referencia="Casa blanca",
            ),
            id_sucursal=id_sucursal_test,
            tipo_entrega="DOMICILIO",
        )

        # 1. Iniciar pago
        res_inicio = iniciar_transaccion_pago(self.db, payload, self.cliente, "ONLINE")
        self.assertEqual(res_inicio["estado_pago"], "PENDIENTE")
        self.assertIn("transaccion", res_inicio)

        txn = res_inicio["transaccion"]
        codigo_txn = txn["codigo_transaccion"]
        monto_txn = txn["monto"]
        self.assertEqual(txn["estado"], "PENDIENTE")
        self.assertIsNotNone(txn["qr_data"])

        # Verificar que el stock NO se descontó todavía
        self.db.refresh(self.producto)
        self.assertEqual(
            self.producto.stock_total,
            stock_inicial_prod,
            "El stock no debe descontarse hasta la confirmación del webhook",
        )

        # 2. Confirmación asíncrona mediante Webhook
        firma_valida = calcular_firma_webhook(codigo_txn, monto_txn, "APPROVED")
        webhook_payload = WebhookPayload(
            codigo_transaccion=codigo_txn,
            status="APPROVED",
            monto=monto_txn,
            signature=firma_valida,
        )

        res_webhook = procesar_webhook_pasarela(self.db, webhook_payload, firma_valida)
        self.assertEqual(res_webhook["status"], "success")
        self.assertEqual(res_webhook["estado"], "PAGADO")

        # Verificar que la venta y transacción están en PAGADO
        estado_live = obtener_estado_pago(self.db, codigo_txn)
        self.assertEqual(estado_live["estado"], "PAGADO")
        self.assertEqual(estado_live["estado_venta"], "PAGADO")

        # 3. Verificar que el stock AHORA SÍ se descontó atómicamente
        self.db.refresh(self.producto)
        self.assertEqual(
            self.producto.stock_total,
            stock_inicial_prod - cant_compra,
            "El stock_total debe reducirse por la cantidad comprada",
        )

        if self.inv_suc:
            self.db.refresh(self.inv_suc)
            self.assertEqual(
                self.inv_suc.stock,
                stock_inicial_suc - cant_compra,
                "El stock físico de la sucursal debe reducirse por la cantidad comprada",
            )

        # 4. Verificar idempotencia: procesar el mismo webhook no debe duplicar el descuento
        res_idempotente = procesar_webhook_pasarela(self.db, webhook_payload, firma_valida)
        self.assertEqual(res_idempotente["status"], "success")
        self.db.refresh(self.producto)
        self.assertEqual(
            self.producto.stock_total,
            stock_inicial_prod - cant_compra,
            "El reintento del webhook no debe duplicar el descuento de stock",
        )

    def test_03_iniciar_pago_tarjeta_y_webhook_rechazado(self):
        """Inicia un pago con tarjeta y procesa rechazo por pasarela."""
        payload = ProcesarPagoPayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="S",
                    color="Negro",
                )
            ],
            metodo_pago="TARJETA",
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre,
                correo=self.cliente.correo,
                telefono="70012345",
                direccion="Av. Las Palmas 456",
                ciudad="Santa Cruz",
            ),
            datos_tarjeta=DatosTarjetaPayload(
                titular="JUAN PEREZ",
                numero_tarjeta="4500123456789012",
                expiracion="12/28",
                cvv="123",
            ),
        )

        stock_antes = self.producto.stock_total

        res_inicio = iniciar_transaccion_pago(self.db, payload, self.cliente, "ONLINE")
        txn = res_inicio["transaccion"]
        codigo_txn = txn["codigo_transaccion"]
        monto_txn = txn["monto"]

        # Confirmación con rechazo (fondos insuficientes o error bancario)
        firma = calcular_firma_webhook(codigo_txn, monto_txn, "REJECTED")
        webhook_payload = WebhookPayload(
            codigo_transaccion=codigo_txn,
            status="REJECTED",
            monto=monto_txn,
            signature=firma,
            motivo="Fondos insuficientes",
        )

        res_webhook = procesar_webhook_pasarela(self.db, webhook_payload, firma)
        self.assertEqual(res_webhook["estado"], "RECHAZADO")

        # Verificar estado final
        estado_live = obtener_estado_pago(self.db, codigo_txn)
        self.assertEqual(estado_live["estado"], "RECHAZADO")
        self.assertEqual(estado_live["estado_venta"], "RECHAZADO")

        # Verificar que el stock no fue alterado
        self.db.refresh(self.producto)
        self.assertEqual(self.producto.stock_total, stock_antes)


if __name__ == "__main__":
    unittest.main()
