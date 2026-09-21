# backend/tests/test_stripe_checkout_session.py
# CU21 — Tests de Integración Oficial de Stripe Checkout Sessions
import unittest
from unittest.mock import MagicMock, patch

from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.notificaciones.models import Notificacion
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import DetalleVenta, TransaccionPago, Venta
from app.api.v1.endpoints.pagos import (
    confirmar_sesion_stripe_endpoint,
    crear_sesion_stripe_endpoint,
)
from app.schemas.pago import ConfirmarSesionStripePayload, CrearSesionStripePayload
from app.schemas.venta import CheckoutItemPayload, DatosEntregaPayload


class TestStripeCheckoutSession(unittest.TestCase):
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

    def test_01_crear_sesion_stripe_checkout(self):
        """Verifica la generación de sesión oficial de Stripe Checkout con URL de redirección."""
        payload = CrearSesionStripePayload(
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
                direccion="Av. San Martín #200",
                ciudad="Santa Cruz",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
            success_url="http://localhost:4200/tienda/checkout?stripe_session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://localhost:4200/tienda/checkout?cancel=true",
        )

        resp = crear_sesion_stripe_endpoint(
            payload=payload,
            db=self.db,
            usuario_actual=self.cliente,
        )

        self.assertEqual(resp["status"], "success")
        data = resp["data"]
        self.assertTrue(data["session_id"].startswith("cs_"))
        self.assertTrue(data["url"].startswith("https://checkout.stripe.com/"))
        self.assertIn("codigo_venta", data)
        self.assertGreater(data["total"], 0)

        # Verificar que la transacción quedó en estado PENDIENTE
        txn = (
            self.db.query(TransaccionPago)
            .filter(TransaccionPago.codigo_transaccion == data["session_id"])
            .first()
        )
        self.assertIsNotNone(txn)
        self.assertEqual(txn.estado, "PENDIENTE")
        self.assertEqual(txn.pasarela, "Stripe")

    def test_02_confirmar_sesion_stripe_y_descontar_stock(self):
        """Verifica la confirmación tras retorno de Stripe Checkout y el descuento de inventario."""
        stock_suc_antes = self.inv.stock
        stock_prod_antes = self.producto.stock_total

        # 1. Crear sesión
        payload_create = CrearSesionStripePayload(
            items=[
                CheckoutItemPayload(
                    producto_id=self.producto.id_producto,
                    cantidad=1,
                    talla="L",
                    color="Azul",
                )
            ],
            datos_entrega=DatosEntregaPayload(
                nombre_cliente=self.cliente.nombre or "Cliente Stripe",
                correo=self.cliente.correo,
                telefono="77123456",
                direccion="Av. Banzer Km 5",
                ciudad="Santa Cruz",
            ),
            id_sucursal=self.sucursal.codigo_sucursal,
            tipo_entrega="DOMICILIO",
        )
        res_create = crear_sesion_stripe_endpoint(
            payload=payload_create,
            db=self.db,
            usuario_actual=self.cliente,
        )
        session_id = res_create["data"]["session_id"]

        # 2. Simular retorno exitoso de Stripe (mockeando retrieve de Stripe como 'paid')
        mock_session = MagicMock()
        mock_session.payment_status = "paid"
        mock_session.payment_intent = "pi_mock_12345"

        with patch("stripe.checkout.Session.retrieve", return_value=mock_session):
            payload_conf = ConfirmarSesionStripePayload(session_id=session_id)
            resp_conf = confirmar_sesion_stripe_endpoint(
                payload=payload_conf,
                db=self.db,
                usuario_actual=self.cliente,
            )

        self.assertEqual(resp_conf["status"], "success")
        data_conf = resp_conf["data"]
        self.assertEqual(data_conf["estado_pago"], "PAGADO")
        self.assertEqual(data_conf["stripe_session_id"], session_id)
        self.assertIn("comprobante_fiscal", data_conf)
        self.assertEqual(data_conf["comprobante_fiscal"]["nit"], "1028374029")

        # 3. Validar descuento en BD
        self.db.refresh(self.inv)
        self.db.refresh(self.producto)
        self.assertEqual(self.inv.stock, stock_suc_antes - 1)
        self.assertEqual(self.producto.stock_total, stock_prod_antes - 1)


if __name__ == "__main__":
    unittest.main()
