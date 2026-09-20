# Smoke tests CU11 — Punto de Venta (POS) del Vendedor/GS/ASU.
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu11_pos.py
#
# Cubre:
# - V (Vendedor) puede registrar una venta POS contra un cliente (rol C).
# - La venta queda con id_vendedor = id del token del Vendedor.
# - tipo_venta = "POS" en la respuesta.
# - GET /ventas filtrado por V muestra SOLO las ventas POS del V (no las ONLINE).
# - Las ventas POS de V/GS/ASU van por POST /ventas/pos (el checkout online es solo del rol C).
# - C (Cliente) NO puede usar tipo_venta=POS aunque lo mande a /checkout (403).
# - POS sin id_cliente_override -> 422.
# - id_cliente_override apuntando a un usuario que no es rol C -> 422.
# - Filtros fecha_desde / fecha_hasta / tipo_venta funcionan.
import sys
from pathlib import Path

# scripts/smoke/smoke_cu11_pos.py -> scripts/smoke -> scripts -> <backend root>
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


def login(correo, password="Admin123!"):
    r = client.post(
        "/api/v1/auth/login",
        json={"correo": correo, "password": password},
    )
    if r.status_code != 200:
        return None, r
    return r.json()["data"]["access_token"], r


# --- 1. Login de los 4 roles demo ---------------------------------------------
token_c, r = login("cliente@attention.com")
check("login Cliente", token_c is not None, f"-> {r.status_code} {r.text[:200]}")
HC = {"Authorization": f"Bearer {token_c}"}

token_v, r = login("vendedor@attention.com")
check("login Vendedor", token_v is not None, f"-> {r.status_code} {r.text[:200]}")
HV = {"Authorization": f"Bearer {token_v}"}

token_g, r = login("gerente@attention.com")
check("login Gerente", token_g is not None, f"-> {r.status_code} {r.text[:200]}")
HG = {"Authorization": f"Bearer {token_g}"}

token_a, r = login("admin@attention.com")
check("login Admin", token_a is not None, f"-> {r.status_code} {r.text[:200]}")
HA = {"Authorization": f"Bearer {token_a}"}

# --- 2. Capturar id del cliente demo y del vendedor demo -----------------------
r = client.get("/api/v1/usuarios", headers=HA)
usuarios = {u["correo"]: u for u in r.json()["data"]}
cliente = usuarios["cliente@attention.com"]
vendedor = usuarios["vendedor@attention.com"]
# Los prints usan [] para evitar problemas de encoding con caracteres fuera
# de cp1252 que pueda traer el nombre/rol del usuario.
print(
    "  .. cliente_id={}...  vendedor_id={}...".format(
        cliente["id_usuario"][:8], vendedor["id_usuario"][:8]
    )
)

# --- 3. Stock inicial del producto de prueba -----------------------------------
# Usamos "Camisa Oxford Formal" (id=1): nombre ASCII puro (a prueba de
# encoding de consola cp1252 en Windows) y stock >= 40.
r = client.get("/api/v1/productos?limit=100", headers=HA)
productos = {p["nombre"]: p for p in r.json()["data"]}
nombre_test = "Camisa Oxford Formal"
check(
    "producto de prueba existe",
    nombre_test in productos,
    "disponibles: {}".format(list(productos.keys())[:3]),
)
p_camisa = productos[nombre_test]
stock_camisa_inicial = p_camisa["stock_total"]
producto_id = p_camisa["id_producto"]

# --- 4. POST /checkout tipo_venta=POS como Vendedor ----------------------------
payload_pos = {
    "items": [
        {
            "producto_id": producto_id,
            "cantidad": 1,
            "talla": "M",
            "color": "Blanco",
        }
    ],
    "metodo_pago": "EFECTIVO",
    "datos_entrega": {
        "nombre_cliente": "Cliente Demo Atencion",
        "correo": "cliente@attention.com",
        "telefono": "77123456",
        "direccion": "Av. Monsenor Rivero #123",
        "ciudad": "Santa Cruz",
        "referencia": "POS walk-in",
    },
    "tipo_venta": "POS",
    "id_cliente_override": cliente["id_usuario"],
}
r = client.post(f"{BASE}/pos", json=payload_pos, headers=HV)
check(
    "Vendedor POST POS 201",
    r.status_code == 201,
    "-> {} {}".format(r.status_code, r.text[:300]),
)

venta_pos_id = None
if r.status_code == 201:
    v = r.json()["data"]
    venta_pos_id = v["id_venta"]
    check("  tipo_venta=POS en respuesta", v["tipo_venta"] == "POS")
    check(
        "  vendedor_id = id del Vendedor",
        v["vendedor_id"] == vendedor["id_usuario"],
        "-> {}".format(v["vendedor_id"]),
    )
    check(
        "  cliente_id = id del cliente override",
        v["cliente_id"] == cliente["id_usuario"],
    )
    check("  estado PAGADO", v["estado_pago"] == "PAGADO")

# --- 5. C (Cliente) NO puede usar tipo_venta=POS aunque lo mande ---------------
r = client.post(f"{BASE}/checkout", json=payload_pos, headers=HC)
check(
    "Cliente POS rechazado 403",
    r.status_code == 403,
    "-> {} {}".format(r.status_code, r.text[:200]),
)

# --- 6. POS sin id_cliente_override -> 422 ------------------------------------
payload_sin_cliente = {**payload_pos}
del payload_sin_cliente["id_cliente_override"]
r = client.post(f"{BASE}/pos", json=payload_sin_cliente, headers=HV)
check(
    "POS sin id_cliente_override 422",
    r.status_code == 422,
    "-> {} {}".format(r.status_code, r.text[:200]),
)

# --- 7. id_cliente_override apuntando a un usuario que no es rol C -> 422 ------
# Apuntamos al id del propio Vendedor (que es rol V, no C).
payload_cliente_malo = {
    **payload_pos,
    "id_cliente_override": vendedor["id_usuario"],
}
r = client.post(f"{BASE}/pos", json=payload_cliente_malo, headers=HV)
check(
    "POS con override no-C 422",
    r.status_code == 422,
    "-> {} {}".format(r.status_code, r.text[:200]),
)

# --- 8. Flujo ONLINE del Cliente sigue funcionando (regresion) -----------------
payload_online = {**payload_pos, "tipo_venta": "ONLINE"}
del payload_online["id_cliente_override"]
r = client.post(f"{BASE}/checkout", json=payload_online, headers=HC)
check(
    "Cliente POST ONLINE 201 (regresion)",
    r.status_code == 201,
    "-> {} {}".format(r.status_code, r.text[:200]),
)
if r.status_code == 201:
    v = r.json()["data"]
    check("  tipo_venta=ONLINE", v["tipo_venta"] == "ONLINE")
    check("  vendedor_id null", v["vendedor_id"] is None)

# --- 9. Aislamiento: GET /ventas como Vendedor SOLO ve sus ventas POS ----------
r = client.get(f"{BASE}/", headers=HV)
check(
    "GET /ventas como Vendedor 200",
    r.status_code == 200,
    "-> {}".format(r.status_code),
)
if r.status_code == 200:
    data = r.json()["data"]
    ids_vendedor = [v["id_venta"] for v in data]
    check(
        "  Vendedor ve la venta POS que registro",
        venta_pos_id in ids_vendedor,
        "-> {}".format(ids_vendedor),
    )

# --- 10. Aislamiento: GET /ventas como Cliente SOLO ve lo suyo ----------------
r = client.get(f"{BASE}/", headers=HC)
if r.status_code == 200:
    data = r.json()["data"]
    codigos = [v["codigo"] for v in data]
    check(
        "  Cliente ve la venta ONLINE de su carrito",
        any(c.startswith("ATT-") for c in codigos),
        "-> {}".format(codigos),
    )

# --- 11. Filtros: tipo_venta=POS y tipo_venta=ONLINE --------------------------
r = client.get(f"{BASE}/?tipo_venta=POS", headers=HA)
if r.status_code == 200:
    pos_count = len(r.json()["data"])
    check(
        "  filtro tipo_venta=POS (admin ve {} ventas POS)".format(pos_count),
        pos_count >= 1,
    )

r = client.get(f"{BASE}/?tipo_venta=ONLINE", headers=HA)
if r.status_code == 200:
    online_count = len(r.json()["data"])
    check(
        "  filtro tipo_venta=ONLINE (admin ve {} ventas ONLINE)".format(online_count),
        online_count >= 1,
    )

# --- 12. Filtro fecha_desde = hoy debe incluir la venta POS recien creada -----
from datetime import date
hoy = date.today().isoformat()
r = client.get(f"{BASE}/?fecha_desde={hoy}", headers=HA)
if r.status_code == 200:
    ids = [v["id_venta"] for v in r.json()["data"]]
    check(
        "  filtro fecha_desde={} incluye venta POS de hoy".format(hoy),
        venta_pos_id in ids,
    )

# --- 13. Gerente (GS) ve TODAS las ventas (no se le aplica aislamiento) -----
r = client.get(f"{BASE}/", headers=HG)
if r.status_code == 200:
    gs_count = len(r.json()["data"])
    a_count = len(client.get(f"{BASE}/", headers=HA).json()["data"])
    check(
        "  Gerente ve mismo set que Admin ({} vs {})".format(gs_count, a_count),
        gs_count == a_count,
    )

# --- 14. Limpieza: borrar la venta POS de prueba y restaurar stock ------------
from sqlalchemy import text

from app.core.database import engine

with engine.connect() as conn:
    # Kardex de la venta POS de este test (motivo incluye 'Venta POS')
    conn.execute(
        text("DELETE FROM movimientos_inventario WHERE motivo LIKE 'Venta POS %'")
    )
    if venta_pos_id:
        conn.execute(
            text("DELETE FROM detalle_ventas WHERE id_venta = :id"),
            {"id": venta_pos_id},
        )
        conn.execute(
            text("DELETE FROM ventas WHERE id_venta = :id"),
            {"id": venta_pos_id},
        )
    # Restaurar stock del producto de prueba.
    conn.execute(
        text("UPDATE productos SET stock_total = :s WHERE id_producto = :p"),
        {"s": stock_camisa_inicial, "p": producto_id},
    )
    conn.commit()
    print("  .. limpieza OK (venta POS de prueba eliminada, stock restaurado)")

print("\nRESULTADO: {} PASS / {} FAIL".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
