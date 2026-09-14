# Smoke tests CU14 — Reservas (TestClient, sin necesidad del server levantado)
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu14.py
import sys
from datetime import date, timedelta
from pathlib import Path

# Inserta la raiz del backend en sys.path independientemente del CWD.
# scripts/smoke/smoke_cu14.py -> scripts/smoke -> scripts -> <backend root>
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app

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


# --- 1. Login ASU (token para todas las operaciones) --------------------------
r = client.post(
    "/api/v1/auth/login",
    json={"correo": "admin@attention.com", "password": "Admin123!"},
)
check("login ASU", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}

# Producto para reservar: 1 = Camisa Oxford Formal (stock 42)
r = client.get("/api/v1/productos/1", headers=H)
# GET por id no existe; usar listado
r = client.get("/api/v1/productos?limit=100", headers=H)
productos = r.json()["data"]
p1 = next(p for p in productos if p["nombre"] == "Camisa Oxford Formal")
stock_inicial = p1["stock_total"]
print(f"  .. stock inicial producto 1 ({p1['nombre']}): {stock_inicial}")

# Cliente demo (rol C)
r = client.post(
    "/api/v1/auth/login",
    json={"correo": "cliente@attention.com", "password": "Cliente123!"},
)
cliente_id = None
if r.status_code == 200:
    cliente_id = r.json()["data"]["user"]["id_usuario"]
    print(f"  .. cliente demo id: {cliente_id}")

# --- 2. POST crear reserva (aparta stock) ------------------------------------
manana = (date.today() + timedelta(days=7)).isoformat()
payload = {
    "fecha_expiracion": manana,
    "items": [{"id_producto": p1["id_producto"], "cantidad": 3, "precio_unitario": 189.90}],
}
if cliente_id:
    payload["id_cliente"] = cliente_id
r = client.post(BASE, json=payload, headers=H)
check("POST crear reserva 201", r.status_code == 201, f"-> {r.status_code} {r.text[:300]}")
reserva_id = r.json()["data"]["id_reserva"] if r.status_code == 201 else None
total_esperado = round(3 * 189.90, 2)
if reserva_id:
    data = r.json()["data"]
    check(
        "  total calculado server-side",
        float(data["total_estimado"]) == total_esperado,
        f"-> {data['total_estimado']}",
    )
    check("  estado inicial PENDIENTE", data["estado"] == "PENDIENTE")
    check(
        "  cliente embebido",
        data["cliente"]["correo"] == "cliente@attention.com" if cliente_id else True,
    )

# Stock apartado
r = client.get("/api/v1/productos?limit=100", headers=H)
p1_after = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check(
    "  stock apartado (-3)",
    p1_after["stock_total"] == stock_inicial - 3,
    f"-> {p1_after['stock_total']} (esperado {stock_inicial - 3})",
)

# --- 3. Validaciones de creación ----------------------------------------------
# 422 producto inexistente
r = client.post(
    BASE,
    json={"fecha_expiracion": manana, "items": [{"id_producto": 9999, "cantidad": 1, "precio_unitario": 10}]},
    headers=H,
)
check("POST producto inexistente 422", r.status_code == 422, f"-> {r.status_code}")

# 409 stock insuficiente
r = client.post(
    BASE,
    json={"fecha_expiracion": manana, "items": [{"id_producto": p1["id_producto"], "cantidad": 99999, "precio_unitario": 189.90}]},
    headers=H,
)
check("POST stock insuficiente 409", r.status_code == 409, f"-> {r.status_code}")

# 422 fecha expiración pasada
r = client.post(
    BASE,
    json={"fecha_expiracion": "2020-01-01", "items": [{"id_producto": 1, "cantidad": 1, "precio_unitario": 100}]},
    headers=H,
)
check("POST fecha pasada 422", r.status_code == 422, f"-> {r.status_code}")

# 422 items duplicados
r = client.post(
    BASE,
    json={"fecha_expiracion": manana, "items": [
        {"id_producto": 1, "cantidad": 1, "precio_unitario": 100},
        {"id_producto": 1, "cantidad": 2, "precio_unitario": 100},
    ]},
    headers=H,
)
check("POST items duplicados 422", r.status_code == 422, f"-> {r.status_code}")

# 401 sin token
r = client.post(BASE, json=payload)
check("POST sin token 401", r.status_code == 401, f"-> {r.status_code}")

# --- 4. GET listado + filtros -------------------------------------------------
r = client.get(BASE, headers=H)
check("GET listar 200", r.status_code == 200, f"-> {r.status_code}")
if r.status_code == 200:
    total_reservas = r.json()["total"]
    print(f"  .. total reservas: {total_reservas}")

r = client.get(f"{BASE}?estado=PENDIENTE", headers=H)
check("GET filtro estado 200", r.status_code == 200)
if r.status_code == 200:
    check(
        "  filtro devuelve solo PENDIENTE",
        all(x["estado"] == "PENDIENTE" for x in r.json()["data"]),
    )

r = client.get(f"{BASE}?q=cliente", headers=H)
check("GET búsqueda por cliente 200", r.status_code == 200)

r = client.get(f"{BASE}?estado=INVALIDO", headers=H)
check("GET estado inválido 422", r.status_code == 422, f"-> {r.status_code}")

# --- 5. PATCH transiciones de estado ------------------------------------------
# PENDIENTE -> CONFIRMADA
r = client.patch(f"{BASE}/{reserva_id}/estado", json={"estado": "CONFIRMADA"}, headers=H)
check("PATCH PENDIENTE->CONFIRMADA 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")

# Transición inválida: CONFIRMADA -> CONFIRMADA
r = client.patch(f"{BASE}/{reserva_id}/estado", json={"estado": "CONFIRMADA"}, headers=H)
check("PATCH CONFIRMADA->CONFIRMADA 409", r.status_code == 409, f"-> {r.status_code}")

# CONFIRMADA -> CANCELADA (devuelve stock)
r = client.patch(
    f"{BASE}/{reserva_id}/estado",
    json={"estado": "CANCELADA", "motivo_cancelacion": "Cliente desistió"},
    headers=H,
)
check("PATCH CONFIRMADA->CANCELADA 200", r.status_code == 200, f"-> {r.status_code}")

r = client.get("/api/v1/productos?limit=100", headers=H)
p1_final = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check(
    "  stock devuelto tras cancelar (+3)",
    p1_final["stock_total"] == stock_inicial,
    f"-> {p1_final['stock_total']} (esperado {stock_inicial})",
)

# Estado destino inválido (422)
r = client.patch(f"{BASE}/{reserva_id}/estado", json={"estado": "PENDIENTE"}, headers=H)
check("PATCH estado destino inválido 422", r.status_code == 422, f"-> {r.status_code}")

# 404 reserva inexistente
r = client.patch(f"{BASE}/99999/estado", json={"estado": "CONFIRMADA"}, headers=H)
check("PATCH reserva inexistente 404", r.status_code == 404, f"-> {r.status_code}")

# --- 6. Segunda reserva: flujo completo hasta COMPLETADA ----------------------
payload2 = {
    "fecha_expiracion": manana,
    "items": [{"id_producto": p1["id_producto"], "cantidad": 2, "precio_unitario": 189.90}],
}
if cliente_id:
    payload2["id_cliente"] = cliente_id
r = client.post(BASE, json=payload2, headers=H)
check("POST segunda reserva 201", r.status_code == 201, f"-> {r.status_code}")
reserva2_id = r.json()["data"]["id_reserva"] if r.status_code == 201 else None

if reserva2_id:
    r = client.patch(f"{BASE}/{reserva2_id}/estado", json={"estado": "CONFIRMADA"}, headers=H)
    check("PATCH PENDIENTE->CONFIRMADA 200", r.status_code == 200)
    r = client.patch(f"{BASE}/{reserva2_id}/estado", json={"estado": "COMPLETADA"}, headers=H)
    check("PATCH CONFIRMADA->COMPLETADA 200", r.status_code == 200, f"-> {r.status_code}")

    # DELETE de COMPLETADA debe ser 409
    r = client.delete(f"{BASE}/{reserva2_id}", headers=H)
    check("DELETE completada 409", r.status_code == 409, f"-> {r.status_code}")

# --- 7. DELETE anula PENDIENTE y devuelve stock -------------------------------
payload3 = {
    "fecha_expiracion": manana,
    "items": [{"id_producto": p1["id_producto"], "cantidad": 1, "precio_unitario": 189.90}],
}
r = client.post(BASE, json=payload3, headers=H)
reserva3_id = r.json()["data"]["id_reserva"] if r.status_code == 201 else None
check("POST tercera reserva 201", r.status_code == 201)

r = client.get("/api/v1/productos?limit=100", headers=H)
p1_r3 = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")

r = client.delete(f"{BASE}/{reserva3_id}", headers=H)
check("DELETE anular reserva 200", r.status_code == 200, f"-> {r.status_code}")

r = client.get("/api/v1/productos?limit=100", headers=H)
p1_r3_after = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check(
    "  stock devuelto tras DELETE",
    p1_r3_after["stock_total"] == p1_r3["stock_total"] + 1,
    f"-> {p1_r3_after['stock_total']}",
)

# 404 delete inexistente
r = client.delete(f"{BASE}/99999", headers=H)
check("DELETE inexistente 404", r.status_code == 404, f"-> {r.status_code}")

# --- 8. Limpieza: borrar la reserva COMPLETADA de prueba directamente en DB ----
# (el DELETE por API está impedido por diseño para COMPLETADA)
from app.core.database import SessionLocal
from app.modules.ventas.models import Reserva as ReservaModel, DetalleReserva as DetalleModel

db = SessionLocal()
try:
    db.query(DetalleModel).filter(DetalleModel.id_reserva == reserva2_id).delete()
    db.query(ReservaModel).filter(ReservaModel.id_reserva == reserva2_id).delete()
    if reserva_id:
        db.query(ReservaModel).filter(ReservaModel.id_reserva == reserva_id).delete()
    db.commit()
    print("  .. limpieza OK (reservas de prueba eliminadas)")
finally:
    db.close()

# Stock final: inicial MENOS las 2 unidades de la reserva COMPLETADA
# (venta concretada — el stock consumido NO se devuelve). Las reservas
# 1 (cancelada) y 3 (anulada) sí devolvieron su stock.
r = client.get("/api/v1/productos?limit=100", headers=H)
p1_limpio = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check(
    "stock final == inicial - vendidas (COMPLETADA consume)",
    p1_limpio["stock_total"] == stock_inicial - 2,
    f"-> {p1_limpio['stock_total']} (esperado {stock_inicial - 2})",
)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
