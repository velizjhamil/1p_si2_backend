# backend/tests/test_multi_sucursal.py
import os
import sys
sys.path.insert(0, os.path.abspath("."))

from app.core.database import SessionLocal
from app.modules.compras.models import Proveedor
from app.modules.empresa.models import Sucursal
from app.modules.usuarios.models import Usuario, Rol
from app.modules.inventario.models import InventarioSucursal, Producto, MovimientoInventario
from app.modules.ventas.models import Venta


def test_multi_sucursal_model_integrity():
    db = SessionLocal()
    try:
        # Check sucursales
        sucursales = db.query(Sucursal).all()
        assert len(sucursales) >= 1
        print(f"Sucursales encontradas: {len(sucursales)}")
        for s in sucursales:
            print(f"  - [{s.codigo_sucursal}] {s.nombre} | Gerente: {s.id_gerente} | Personal: {len(s.personal)}")

        # Check inventario_sucursal
        inv_suc = db.query(InventarioSucursal).all()
        assert len(inv_suc) >= 1
        print(f"Filas en inventario_sucursal: {len(inv_suc)}")

        # Check GS user
        gs_user = db.query(Usuario).join(Rol).filter(Rol.nombre_rol == "GS").first()
        if gs_user:
            print(f"Gerente de sucursal: {gs_user.correo} (sucursal={gs_user.id_sucursal}, nombre={gs_user.sucursal_nombre})")

        print("INTEGRIDAD MODELOS MULTI-SUCURSAL: OK")
    finally:
        db.close()


if __name__ == "__main__":
    test_multi_sucursal_model_integrity()
