# Smoke tests CU15+CU21 — Checkout (TestClient, sin necesidad del server levantado)
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu1521.py
import sys
from pathlib import Path

# Inserta la raiz del backend en sys.path independientemente del CWD.
# scripts/smoke/smoke_cu1521.py -> scripts/smoke -> scripts -> <backend root>
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/ventas"
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


def login(correo, password):
    r = client.post("/api/v1/auth/login", json={"correo": correo, "password": password})
    if r.status_code != 200:
        return None, r
    return r.json()["data"]["access_token"], r


# --- 1. Login Cliente (el checkout es del rol C) -------------------------------
# Credenciales demo: los 4 usuarios usan la misma password (Admin123!)
token_c, r = login("cliente@attention.com", "Admin123!")
check("login Cliente", token_c is not None, f"-> {r.status_code} {r.text[:200]}")
HC = {"Authorization": f"Bearer {token_c}"}

token_a, _ = login("admin@attention.com", "Admin123!")
HA = {"Authorization": f"Bearer {token_a}"}

# --- 2. Stock inicial de los productos -----------------------------------------
r = client.get("/api/v1/productos?limit=100", headers=HA)
productos = {p["nombre"]: p for p in r.json()["data"]}
p_camisa = productos["Camisa Oxford Formal"]  # precio 189.90, stock 42
p_polo = productos["Polo Básico Algodón"]  # precio 79.50, stock 120
stock_camisa, stock_polo = p_camisa["stock_total"], p_polo["stock_total"]
print(f"  .. Camisa: {stock_camisa} x Bs {p_camisa['precio_venta']}, Polo: {stock_polo} x Bs {p_polo['precio_venta']}")

# --- 3. POST /checkout — compra exitosa (envío gratis >= 300) ------------------
payload = {
    "items": [
        {"producto_id": p_camisa["id_producto"], "cantidad": 2, "talla": "M", "color": "Blanco"},
        {"producto_id": p_polo["id_producto"], "cantidad": 1, "talla": "L", "color": "Negro"},
    ],
    "metodo_pago": "QR",
    "datos_entrega": {
        "nombre_cliente": "Cliente Demo Atencion",
        "correo": "cliente@attention.com",
        "telefono": "77123456",
        "direccion": "Av. Monseñor Rivero #123",
        "ciudad": "Santa Cruz",
        "referencia": "Casa de rejas verdes",
    },
}
r = client.post(f"{BASE}/checkout", json=payload, headers=HC)
check("POST checkout 201", r.status_code == 201, f"-> {r.status_code} {r.text[:400]}")

venta_id = None
if r.status_code == 201:
    v = r.json()["data"]
    venta_id = v["id_venta"]
    total_esperado = round(2 * 189.90 + 1 * 79.50, 2)  # 459.30
    check("  total calculado server-side", float(v["total"]) == total_esperado, f"-> {v['total']} (esperado {total_esperado})")
    check("  envío gratis (subtotal >= 300)", float(v["costo_envio"]) == 0)
    check("  codigo comprobante ATT-", v["codigo"].startswith("ATT-"))
    check("  estado PAGADO", v["estado_pago"] == "PAGADO")
    check("  items con talla/color", v["items"][0]["talla"] == "M" and v["items"][0]["color"] == "Blanco")
    check("  datos_entrega completos", v["datos_entrega"]["ciudad"] == "Santa Cruz")
    codigo = v["codigo"]

    # Stock descontado
    r = client.get("/api/v1/productos?limit=100", headers=HA)
    ps = {p["nombre"]: p for p in r.json()["data"]}
    check("  stock camisa -2", ps["Camisa Oxford Formal"]["stock_total"] == stock_camisa - 2, f"-> {ps['Camisa Oxford Formal']['stock_total']}")
    check("  stock polo -1", ps["Polo Básico Algodón"]["stock_total"] == stock_polo - 1)

    # Kardex SALIDA registrado
    r = client.get(f"/api/v1/inventario/movimientos?id_producto={p_camisa['id_producto']}&limit=5", headers=HA)
    movs = [m for m in r.json()["data"] if "checkout online" in (m["motivo"] or "")]
    check("  kardex SALIDA registrado", len(movs) >= 1 and movs[0]["tipo"] == "SALIDA")

# --- 4. Compra con envío (subtotal < 300) ---------------------------------------
payload2 = {
    "items": [{"producto_id": p_polo["id_producto"], "cantidad": 1, "talla": "M", "color": "Blanco"}],
    "metodo_pago": "EFECTIVO",
    "datos_entrega": {
        "nombre_cliente": "Cliente Demo Atencion",
        "correo": "cliente@attention.com",
        "telefono": "77123456",
        "direccion": "Calle Ballivián #45",
        "ciudad": "La Paz",
    },
}
r = client.post(f"{BASE}/checkout", json=payload2, headers=HC)
check("POST checkout 2 (EFECTIVO) 201", r.status_code == 201, f"-> {r.status_code}")
if r.status_code == 201:
    v = r.json()["data"]
    check("  envío Bs 25 (subtotal < 300)", float(v["costo_envio"]) == 25)
    check("  total = subtotal + envío", float(v["total"]) == round(79.50 + 25, 2))

# --- 5. Validaciones ------------------------------------------------------------
# 409 stock insuficiente
r = client.post(
    f"{BASE}/checkout",
    json={
        "items": [{"producto_id": p_camisa["id_producto"], "cantidad": 9999}],
        "metodo_pago": "QR",
        "datos_entrega": payload["datos_entrega"],
    },
    headers=HC,
)
check("POST stock insuficiente 409", r.status_code == 409, f"-> {r.status_code}")

# 422 producto inexistente
r = client.post(
    f"{BASE}/checkout",
    json={
        "items": [{"producto_id": 99999, "cantidad": 1}],
        "metodo_pago": "QR",
        "datos_entrega": payload["datos_entrega"],
    },
    headers=HC,
)
check("POST producto inexistente 422", r.status_code == 422, f"-> {r.status_code}")

# 422 método de pago inválido
r = client.post(
    f"{BASE}/checkout",
    json={**payload, "metodo_pago": "BITCOIN"},
    headers=HC,
)
check("POST método inválido 422", r.status_code == 422, f"-> {r.status_code}")

# 422 items vacíos
r = client.post(
    f"{BASE}/checkout",
    json={"items": [], "metodo_pago": "QR", "datos_entrega": payload["datos_entrega"]},
    headers=HC,
)
check("POST items vacíos 422", r.status_code == 422, f"-> {r.status_code}")

# 409 producto Inactivo (id 10 = Camisa Lino Verano, estado Inactivo en seed)
r = client.post(
    f"{BASE}/checkout",
    json={
        "items": [{"producto_id": 10, "cantidad": 1}],
        "metodo_pago": "QR",
        "datos_entrega": payload["datos_entrega"],
    },
    headers=HC,
)
check("POST producto Inactivo 409", r.status_code == 409, f"-> {r.status_code}")

# 401 sin token
r = client.post(f"{BASE}/checkout", json=payload)
check("POST sin token 401", r.status_code == 401, f"-> {r.status_code}")

# --- 6. GET /{id} — detalle + comprobante ----------------------------------------
r = client.get(f"{BASE}/{venta_id}", headers=HC)
check("GET venta por id 200", r.status_code == 200, f"-> {r.status_code}")
if r.status_code == 200:
    v = r.json()["data"]
    check("  detalle con items", len(v["items"]) == 2)
    check("  comprobante por codigo", v["codigo"] == codigo)

# 404 inexistente
r = client.get(f"{BASE}/99999", headers=HC)
check("GET venta inexistente 404", r.status_code == 404, f"-> {r.status_code}")

# --- 7. GET / — historial ----------------------------------------------------------
r = client.get(f"{BASE}", headers=HC)
check("GET historial (cliente) 200", r.status_code == 200, f"-> {r.status_code}")
if r.status_code == 200:
    data = r.json()
    check("  cliente solo ve sus ventas", data["total"] >= 2)
    check("  todas son del cliente", all(v["cliente_id"] for v in data["data"]))

# ASU ve todo + filtros
r = client.get(f"{BASE}?metodo_pago=QR", headers=HA)
check("GET historial (ASU, filtro QR) 200", r.status_code == 200)
if r.status_code == 200:
    check("  filtro QR", all(v["metodo_pago"] == "QR" for v in r.json()["data"]))

r = client.get(f"{BASE}?q=ATT-", headers=HA)
check("GET historial búsqueda q 200", r.status_code == 200)

# --- 8. Limpieza: restaurar stock y borrar ventas de prueba -----------------------
from sqlalchemy import text

from app.core.database import engine

with engine.connect() as conn:
    # detalles y kardex se borran por FK/CASCADE o manualmente
    conn.execute(text(
        "DELETE FROM movimientos_inventario WHERE motivo LIKE '%checkout online%'"
    ))
    conn.execute(text(
        "DELETE FROM detalle_ventas WHERE id_venta IN (SELECT id_venta FROM ventas WHERE correo = 'cliente@attention.com')"
    ))
    conn.execute(text("DELETE FROM ventas WHERE correo = 'cliente@attention.com'"))
    conn.execute(text("UPDATE productos SET stock_total = :s WHERE id_producto = :p"),
                 {"s": stock_camisa, "p": p_camisa["id_producto"]})
    conn.execute(text("UPDATE productos SET stock_total = :s WHERE id_producto = :p"),
                 {"s": stock_polo, "p": p_polo["id_producto"]})
    conn.commit()
    print("  .. limpieza OK (ventas/kardex de prueba eliminados, stock restaurado)")

# Verificación final
r = client.get("/api/v1/productos?limit=100", headers=HA)
ps = {p["nombre"]: p for p in r.json()["data"]}
check("stock final restaurado", ps["Camisa Oxford Formal"]["stock_total"] == stock_camisa and ps["Polo Básico Algodón"]["stock_total"] == stock_polo)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
