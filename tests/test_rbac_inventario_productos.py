# backend/tests/test_rbac_inventario_productos.py
import os
import sys
import unittest
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.v1.endpoints.inventario import listar_movimientos, listar_stock, registrar_movimiento
from app.api.v1.endpoints.products import listar_productos
from app.core.database import SessionLocal
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.usuarios.models import Rol, Usuario
from app.schemas.inventario import MovimientoCreatePayload


class TestRbacInventarioProductos(unittest.TestCase):
    def setUp(self):
        self.db: Session = SessionLocal()
        self.rol_asu = self.db.query(Rol).filter(Rol.nombre_rol == "ASU").first()
        self.rol_gs = self.db.query(Rol).filter(Rol.nombre_rol == "GS").first()
        self.rol_v = self.db.query(Rol).filter(Rol.nombre_rol == "V").first()
        self.rol_c = self.db.query(Rol).filter(Rol.nombre_rol == "C").first()
        self.sucursal = self.db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()
        self.producto = self.db.query(Producto).filter(Producto.estado == "Activo").first()

    def tearDown(self):
        self.db.close()

    def test_01_cliente_puede_consultar_catalogo_con_disponibilidad_multi_sucursal(self):
        """Cliente consulta productos y recibe el desglose de disponibilidad por sucursal."""
        res = listar_productos(db=self.db, limit=5)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["data"]) > 0)
        prod = res["data"][0]
        self.assertIn("disponibilidad_sucursales", prod)
        self.assertIsInstance(prod["disponibilidad_sucursales"], list)

    def test_02_gs_y_v_aislados_a_su_propia_sucursal_en_stock(self):
        """GS y V solo pueden consultar el stock de su propia sucursal asignada."""
        self.assertIsNotNone(self.sucursal)

        # Usuario GS con sucursal asignada
        usuario_gs = Usuario(
            id_usuario=uuid4(),
            correo="gerente.test@attention.com",
            nombre="Gerente",
            apellido="Test",
            rol=self.rol_gs,
            id_sucursal=self.sucursal.codigo_sucursal,
        )

        res = listar_stock(db=self.db, _usuario=usuario_gs, limit=5)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["sucursal_id"], self.sucursal.codigo_sucursal)

        # Si GS intenta consultar otra sucursal inexistente/ajena -> 403
        with self.assertRaises(HTTPException) as ctx:
            listar_stock(db=self.db, _usuario=usuario_gs, sucursal_id=99999)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_03_vendedor_y_cliente_permisos_en_movimientos(self):
        """Vendedor puede consultar kardex de su sucursal; Cliente es rechazado con 403."""
        self.assertIsNotNone(self.sucursal)

        usuario_v = Usuario(
            id_usuario=uuid4(),
            correo="vendedor.test@attention.com",
            nombre="Vendedor",
            apellido="Test",
            rol=self.rol_v,
            id_sucursal=self.sucursal.codigo_sucursal,
        )
        res = listar_movimientos(db=self.db, _usuario=usuario_v, limit=5)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["sucursal_id"], self.sucursal.codigo_sucursal)

        # Cliente intentando consultar kardex -> 403
        usuario_c = Usuario(
            id_usuario=uuid4(),
            correo="cliente.test@attention.com",
            nombre="Cliente",
            apellido="Test",
            rol=self.rol_c,
        )
        with self.assertRaises(HTTPException) as ctx:
            listar_movimientos(db=self.db, _usuario=usuario_c)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_04_vendedor_no_puede_alterar_inventario_de_otra_sucursal(self):
        """Vendedor intentando registrar movimiento en otra sucursal es rechazado con 403."""
        self.assertIsNotNone(self.sucursal)
        self.assertIsNotNone(self.producto)

        usuario_v = Usuario(
            id_usuario=uuid4(),
            correo="vendedor2.test@attention.com",
            nombre="Vendedor",
            apellido="Test",
            rol=self.rol_v,
            id_sucursal=self.sucursal.codigo_sucursal,
        )

        payload_otra_sucursal = MovimientoCreatePayload(
            id_producto=self.producto.id_producto,
            tipo="ENTRADA",
            cantidad=5,
            motivo="Intento no autorizado",
            id_sucursal=99999,  # Otra sucursal
        )

        with self.assertRaises(HTTPException) as ctx:
            registrar_movimiento(
                payload=payload_otra_sucursal,
                db=self.db,
                usuario_actual=usuario_v,
            )
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
