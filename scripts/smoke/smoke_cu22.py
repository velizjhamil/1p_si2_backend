# Smoke tests CU22 — Inventario (TestClient, sin necesidad del server levantado)
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu22.py
import sys
from datetime import date
from pathlib import Path

# Inserta la raiz del backend en sys.path independientemente del CWD.
# scripts/smoke/smoke_cu22.py -> scripts/smoke -> scripts -> <backend root>
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/inventario"
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


# --- 1. Login ASU --------------------------------------------------------------
r = client.post(
    "/api/v1/auth/login",
    json={"correo": "admin@attention.com", "password": "Admin123!"},
)
check("login ASU", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
H = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

# Producto de prueba: 1 = Camisa Oxford Formal
r = client.get("/api/v1/productos?limit=100", headers=H)
productos = r.json()["data"]
p1 = next(p for p in productos if p["nombre"] == "Camisa Oxford Formal")
stock_inicial = p1["stock_total"]
print(f"  .. stock inicial producto 1: {stock_inicial}")

# --- 2. GET /stock --------------------------------------------------------------
r = client.get(f"{BASE}/stock", headers=H)
check("GET /stock 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
if r.status_code == 200:
    body = r.json()
    check("  envelope con data + total", "data" in body and "total" in body)
    print(f"  .. total productos: {body['total']}, alertas criticas: {body.get('alertas')}")
    fila = next((f for f in body["data"] if f["nombre"] == "Camisa Oxford Formal"), None)
    check("  fila con nivel de alerta", fila is not None and fila["nivel"] in ("CRITICO", "BAJO", "OK"))

# Filtro solo_alertas
r = client.get(f"{BASE}/stock?solo_alertas=true&umbral_minimo=10", headers=H)
check("GET /stock solo_alertas 200", r.status_code == 200)
if r.status_code == 200:
    data = r.json()["data"]
    check(
        "  solo_alertas filtra < umbral",
        all(f["stock_total"] < 10 for f in data),
        f"-> stocks: {[f['stock_total'] for f in data]}",
    )

# Búsqueda por nombre
r = client.get(f"{BASE}/stock?q=jean", headers=H)
check("GET /stock con q 200", r.status_code == 200)
if r.status_code == 200:
    check(
        "  q filtra por nombre",
        all("jean" in f["nombre"].lower() for f in r.json()["data"]),
    )

# --- 3. POST /movimientos -------------------------------------------------------
# ENTRADA +10
r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": p1["id_producto"], "tipo": "ENTRADA", "cantidad": 10, "motivo": "Reposición smoke test"},
    headers=H,
)
check("POST ENTRADA 201", r.status_code == 201, f"-> {r.status_code} {r.text[:300]}")
if r.status_code == 201:
    m = r.json()["data"]
    check("  stock_anterior correcto", m["stock_anterior"] == stock_inicial)
    check("  stock_nuevo = +10", m["stock_nuevo"] == stock_inicial + 10)
    check("  usuario embebido", m["usuario"]["correo"] == "admin@attention.com")
    check("  producto embebido", m["producto"]["id_producto"] == p1["id_producto"])
    entrada_id = m["id_movimiento"]

# Verificar stock_actualizado en productos
r = client.get("/api/v1/productos?limit=100", headers=H)
p1_e = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check("  productos.stock_total actualizado (+10)", p1_e["stock_total"] == stock_inicial + 10, f"-> {p1_e['stock_total']}")

# SALIDA -4 (válido)
r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": p1["id_producto"], "tipo": "SALIDA", "cantidad": 4, "motivo": "Venta smoke test"},
    headers=H,
)
check("POST SALIDA 201", r.status_code == 201, f"-> {r.status_code}")
if r.status_code == 201:
    m = r.json()["data"]
    check("  stock_nuevo = -4", m["stock_nuevo"] == stock_inicial + 10 - 4)

# SALIDA excesiva -> 409
r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": p1["id_producto"], "tipo": "SALIDA", "cantidad": 99999},
    headers=H,
)
check("POST SALIDA excesiva 409", r.status_code == 409, f"-> {r.status_code}")

# AJUSTE fija stock exacto
r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": p1["id_producto"], "tipo": "AJUSTE", "cantidad": stock_inicial, "motivo": "Restaurar valor smoke test"},
    headers=H,
)
check("POST AJUSTE 201", r.status_code == 201, f"-> {r.status_code}")
if r.status_code == 201:
    check("  AJUSTE fija stock exacto", r.json()["data"]["stock_nuevo"] == stock_inicial)

# Validaciones 422
r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": 99999, "tipo": "ENTRADA", "cantidad": 1},
    headers=H,
)
check("POST producto inexistente 422", r.status_code == 422, f"-> {r.status_code}")

r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": 1, "tipo": "ROBO", "cantidad": 1},
    headers=H,
)
check("POST tipo inválido 422", r.status_code == 422, f"-> {r.status_code}")

r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": 1, "tipo": "ENTRADA", "cantidad": 0},
    headers=H,
)
check("POST ENTRADA cantidad 0 -> 422", r.status_code == 422, f"-> {r.status_code}")

r = client.post(
    f"{BASE}/movimientos",
    json={"id_producto": 1, "tipo": "SALIDA", "cantidad": -5},
    headers=H,
)
check("POST cantidad negativa 422", r.status_code == 422, f"-> {r.status_code}")

# 401 sin token
r = client.post(f"{BASE}/movimientos", json={"id_producto": 1, "tipo": "ENTRADA", "cantidad": 1})
check("POST sin token 401", r.status_code == 401, f"-> {r.status_code}")

# --- 4. GET /movimientos (kardex) ------------------------------------------------
r = client.get(f"{BASE}/movimientos", headers=H)
check("GET /movimientos 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
if r.status_code == 200:
    total_movs = r.json()["total"]
    print(f"  .. total movimientos: {total_movs}")

# Filtro por tipo
r = client.get(f"{BASE}/movimientos?tipo=ENTRADA", headers=H)
check("GET /movimientos tipo=ENTRADA 200", r.status_code == 200)
if r.status_code == 200:
    check(
        "  filtro tipo devuelve solo ENTRADA",
        all(m["tipo"] == "ENTRADA" for m in r.json()["data"]),
    )

# Filtro por fecha exacta (hoy)
hoy = date.today().isoformat()
r = client.get(f"{BASE}/movimientos?fecha={hoy}", headers=H)
check("GET /movimientos fecha=hoy 200", r.status_code == 200)
if r.status_code == 200:
    check(
        "  filtro fecha devuelve los de hoy",
        all(m["fecha_movimiento"].startswith(hoy) for m in r.json()["data"]),
    )

# Filtro por producto
r = client.get(f"{BASE}/movimientos?id_producto={p1['id_producto']}", headers=H)
check("GET /movimientos id_producto 200", r.status_code == 200)
if r.status_code == 200:
    check(
        "  filtro producto correcto",
        all(m["producto"]["id_producto"] == p1["id_producto"] for m in r.json()["data"]),
    )

# Tipo inválido 422
r = client.get(f"{BASE}/movimientos?tipo=ROBO", headers=H)
check("GET /movimientos tipo inválido 422", r.status_code == 422, f"-> {r.status_code}")

# --- 5. CHECK DINÁMICO products.py: DELETE producto con movimientos -> 409 -------
r = client.delete(f"/api/v1/productos/{p1['id_producto']}", headers=H)
check("DELETE producto con kardex 409 (check dinámico activo)", r.status_code == 409, f"-> {r.status_code} {r.text[:200]}")

# --- 6. Limpieza: borrar movimientos del smoke test y restaurar stock -----------
from sqlalchemy import text

from app.core.database import engine

with engine.connect() as conn:
    n = conn.execute(
        text(
            "DELETE FROM movimientos_inventario WHERE motivo LIKE '%smoke test%'"
        )
    ).rowcount
    conn.execute(text("UPDATE productos SET stock_total = :s WHERE id_producto = :p"),
                 {"s": stock_inicial, "p": p1["id_producto"]})
    conn.commit()
    print(f"  .. limpieza OK ({n} movimientos de prueba eliminados, stock restaurado)")

# Verificación final: stock del producto restaurado
r = client.get("/api/v1/productos?limit=100", headers=H)
p1_fin = next(p for p in r.json()["data"] if p["nombre"] == "Camisa Oxford Formal")
check("stock final restaurado", p1_fin["stock_total"] == stock_inicial, f"-> {p1_fin['stock_total']}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
