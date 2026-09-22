# Smoke test — Reservas Simplificadas (Efectivo, Sin Anticipos, Retiro/Domicilio, Sincronizacion de Stock)
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.modules.ventas.models import Reserva as ReservaModel, DetalleReserva as DetalleModel
from app.modules.inventario.models import InventarioSucursal

client = TestClient(app)
BASE = "/api/v1/reservas"
PASS = 0
FAIL = 0

def check(nombre, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {nombre}")
    else:
        FAIL += 1
        print(f"  [FAIL] {nombre} {extra}")

print("=== TEST RESERVAS SIMPLIFICADAS (EFECTIVO, SIN ANTICIPOS, POR SUCURSAL) ===")

# 1. Login ASU
r = client.post(
    "/api/v1/auth/login",
    json={"correo": "admin@attention.com", "password": "Admin123!"},
)
check("Login ASU 200", r.status_code == 200)
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}

# 2. Obtener producto y stock en sucursal 1
r = client.get("/api/v1/productos?limit=100", headers=H)
productos = r.json()["data"]
p1 = next(p for p in productos if p["nombre"] == "Camisa Oxford Formal")
id_prod = p1["id_producto"]
stock_total_inicial = p1["stock_total"]

db = SessionLocal()
try:
    inv1 = db.query(InventarioSucursal).filter(
        InventarioSucursal.id_producto == id_prod,
        InventarioSucursal.id_sucursal == 1
    ).first()
    stock_sucursal_inicial = inv1.stock if inv1 else 0
finally:
    db.close()

print(f"  .. Producto {id_prod} stock total: {stock_total_inicial}, stock sucursal 1: {stock_sucursal_inicial}")

# 3. Crear Reserva modalidad RETIRO (Efectivo, sin anticipo)
manana = (date.today() + timedelta(days=7)).isoformat()
payload_retiro = {
    "id_sucursal": 1,
    "tipo_entrega": "RETIRO",
    "fecha_expiracion": manana,
    "items": [{"id_producto": id_prod, "cantidad": 2, "precio_unitario": 189.90}],
}
r = client.post(BASE, json=payload_retiro, headers=H)
check("POST crear reserva RETIRO 201", r.status_code == 201, f"-> {r.status_code} {r.text[:200]}")

reserva_retiro = r.json()["data"] if r.status_code == 201 else None
id_reserva_1 = reserva_retiro["id_reserva"] if reserva_retiro else None

if reserva_retiro:
    check("  Estado inmediato CONFIRMADA", reserva_retiro["estado"] == "CONFIRMADA", f"-> {reserva_retiro['estado']}")
    check("  Sin anticipo exigido (0.0)", float(reserva_retiro["monto_anticipo"]) == 0.0, f"-> {reserva_retiro['monto_anticipo']}")
    check("  Metodo de pago EFECTIVO", reserva_retiro["metodo_pago_anticipo"] == "EFECTIVO", f"-> {reserva_retiro['metodo_pago_anticipo']}")
    check("  Modalidad RETIRO persistida", reserva_retiro["tipo_entrega"] == "RETIRO", f"-> {reserva_retiro['tipo_entrega']}")
    check("  Temporizador 48h activo", reserva_retiro["fecha_expiracion_dt"] is not None)

# 4. Verificar que el stock de sucursal 1 se desconto inmediatamente (-2)
db = SessionLocal()
try:
    inv1_after = db.query(InventarioSucursal).filter(
        InventarioSucursal.id_producto == id_prod,
        InventarioSucursal.id_sucursal == 1
    ).first()
    stock_sucursal_descontado = inv1_after.stock if inv1_after else 0
finally:
    db.close()

check(
    "  Stock sucursal 1 descontado en tiempo real (-2)",
    stock_sucursal_descontado == stock_sucursal_inicial - 2,
    f"-> {stock_sucursal_descontado} (esperado {stock_sucursal_inicial - 2})"
)

# 5. Crear Reserva modalidad DOMICILIO
payload_domicilio = {
    "id_sucursal": 1,
    "tipo_entrega": "DOMICILIO",
    "direccion_entrega": "Av. San Martin #123, Equipetrol",
    "telefono_entrega": "70012345",
    "fecha_expiracion": manana,
    "items": [{"id_producto": id_prod, "cantidad": 1, "precio_unitario": 189.90}],
}
r = client.post(BASE, json=payload_domicilio, headers=H)
check("POST crear reserva DOMICILIO 201", r.status_code == 201, f"-> {r.status_code} {r.text[:200]}")

reserva_domicilio = r.json()["data"] if r.status_code == 201 else None
id_reserva_2 = reserva_domicilio["id_reserva"] if reserva_domicilio else None

if reserva_domicilio:
    check("  Modalidad DOMICILIO persistida", reserva_domicilio["tipo_entrega"] == "DOMICILIO")
    check("  Direccion de entrega guardada", reserva_domicilio["direccion_entrega"] == "Av. San Martin #123, Equipetrol")
    check("  Telefono de entrega guardado", reserva_domicilio["telefono_entrega"] == "70012345")
    check("  Estado CONFIRMADA", reserva_domicilio["estado"] == "CONFIRMADA")

# 6. Cancelar reserva 1 y verificar devolucion de stock
r = client.patch(
    f"{BASE}/{id_reserva_1}/estado",
    json={"estado": "CANCELADA", "motivo_cancelacion": "Cliente cancelo pedido"},
    headers=H,
)
check("PATCH cancelar reserva 200", r.status_code == 200, f"-> {r.status_code}")

db = SessionLocal()
try:
    inv1_devuelto = db.query(InventarioSucursal).filter(
        InventarioSucursal.id_producto == id_prod,
        InventarioSucursal.id_sucursal == 1
    ).first()
    stock_sucursal_devuelto = inv1_devuelto.stock if inv1_devuelto else 0
finally:
    db.close()

check(
    "  Stock sucursal 1 restaurado tras cancelacion (+2)",
    stock_sucursal_devuelto == stock_sucursal_inicial - 1, # -1 porque reserva 2 sigue activa
    f"-> {stock_sucursal_devuelto} (esperado {stock_sucursal_inicial - 1})"
)

# 7. Completar venta de reserva 2 (Entrega contra entrega)
r = client.patch(
    f"{BASE}/{id_reserva_2}/estado",
    json={"estado": "COMPLETADA"},
    headers=H,
)
check("PATCH completar venta (entrega realizada) 200", r.status_code == 200)

# Limpieza de pruebas en DB
db = SessionLocal()
try:
    if id_reserva_1:
        db.query(DetalleModel).filter(DetalleModel.id_reserva == id_reserva_1).delete()
        db.query(ReservaModel).filter(ReservaModel.id_reserva == id_reserva_1).delete()
    if id_reserva_2:
        db.query(DetalleModel).filter(DetalleModel.id_reserva == id_reserva_2).delete()
        db.query(ReservaModel).filter(ReservaModel.id_reserva == id_reserva_2).delete()
        # Restaurar la 1 prenda de reserva 2 para dejar el stock intacto
        inv1_restore = db.query(InventarioSucursal).filter(
            InventarioSucursal.id_producto == id_prod,
            InventarioSucursal.id_sucursal == 1
        ).first()
        if inv1_restore:
            inv1_restore.stock = stock_sucursal_inicial
        from app.modules.inventario.models import Producto
        prod = db.query(Producto).filter(Producto.id_producto == id_prod).first()
        if prod:
            prod.stock_total = stock_total_inicial
    db.commit()
    print("  .. Limpieza de datos de prueba exitosa.")
finally:
    db.close()

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
