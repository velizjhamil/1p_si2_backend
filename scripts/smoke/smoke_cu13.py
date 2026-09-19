# Smoke tests CU13 - Devoluciones (TestClient, sin necesidad del server levantado)
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu13.py
#
# Cubre el flujo end-to-end:
#   1. Login Cliente + Vendedor + Admin
#   2. Cliente hace checkout (genera una venta PAGADO fresca para devolver)
#   3. GET /venta/{id}/elegibles -> confirma items disponibles
#   4. POST /  (cliente solicita devolucion SOLICITADA)
#   5. GET /  (cliente ve su devolucion; admin ve todas)
#   6. GET /{id}  (cliente y admin)
#   7. PATCH /{id}/procesar con accion=APROBAR (vendedor)
#   8. PATCH /{id}/procesar con accion=COMPLETAR (admin) -> kardex ENTRADA
#   9. Validaciones de error (cantidad excedida, sin auth, rol invalido, etc.)
import sys
from pathlib import Path

# Inserta la raiz del backend en sys.path independientemente del CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/devoluciones"
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


# --- 1. Login de los 3 roles ----------------------------------------------------
token_c, r = login("cliente@attention.com", "Admin123!")
check("login Cliente", token_c is not None, f"-> {r.status_code} {r.text[:200]}")
HC = {"Authorization": f"Bearer {token_c}"}

token_v, r = login("vendedor@attention.com", "Admin123!")
check("login Vendedor", token_v is not None, f"-> {r.status_code} {r.text[:200]}")
HV = {"Authorization": f"Bearer {token_v}"}

token_a, r = login("admin@attention.com", "Admin123!")
check("login Admin", token_a is not None, f"-> {r.status_code} {r.text[:200]}")
HA = {"Authorization": f"Bearer {token_a}"}


# --- 2. Cliente hace checkout para tener una venta PAGADO fresca ---------------
# Lookup del Camisa (id=1) - precio 189.90, stock 42.
r = client.get("/api/v1/productos?limit=100", headers=HA)
productos_por_id = {p["id_producto"]: p for p in r.json()["data"]}
p_camisa = productos_por_id[1]
stock_antes = p_camisa["stock_total"]
print(f"  .. Camisa stock antes: {stock_antes}")

payload_venta = {
    "items": [
        {"producto_id": p_camisa["id_producto"], "cantidad": 2, "talla": "M", "color": "Blanco"},
    ],
    "metodo_pago": "EFECTIVO",
    "datos_entrega": {
        "nombre_cliente": "Cliente Demo Atencion",
        "correo": "cliente@attention.com",
        "telefono": "77123456",
        "direccion": "Av. Monsenor Rivero #123",
        "ciudad": "Santa Cruz",
        "referencia": "Casa de rejas verdes",
    },
}
r = client.post("/api/v1/ventas/checkout", json=payload_venta, headers=HC)
check("checkout 201 (preparar venta)", r.status_code == 201, f"-> {r.status_code} {r.text[:300]}")
venta = r.json()["data"]
venta_id = venta["id_venta"]
detalle_venta_id = venta["items"][0]["id_detalle"]
print(f"  .. Venta {venta['codigo']} creada con id={venta_id}, detalle={detalle_venta_id}")


# --- 3. GET /venta/{id}/elegibles (helper para la UI del cliente) ---------------
r = client.get(f"{BASE}/venta/{venta_id}/elegibles", headers=HC)
check("GET elegibles 200", r.status_code == 200, f"-> {r.status_code} {r.text[:300]}")
if r.status_code == 200:
    data = r.json()["data"]
    check("  en_ventana=true (recién comprada)", data["en_ventana"] is True)
    check(
        "  items con disponible_para_devolver=2",
        len(data["items"]) == 1 and data["items"][0]["disponible_para_devolver"] == 2,
    )


# --- 4. POST / (cliente solicita devolucion) ------------------------------------
payload_dev = {
    "id_venta": venta_id,
    "motivo": "La camisa llegó con la talla equivocada, pedí M y llegó L.",
    "items": [
        {"detalle_venta_id": detalle_venta_id, "cantidad_devuelta": 1},
    ],
}
r = client.post(f"{BASE}/", json=payload_dev, headers=HC)
check("POST devolucion 201", r.status_code == 201, f"-> {r.status_code} {r.text[:300]}")
dev_id = None
if r.status_code == 201:
    dev = r.json()["data"]
    dev_id = dev["id_devolucion"]
    check("  estado=SOLICITADA", dev["estado"] == "SOLICITADA")
    check("  monto_total_devuelto=0 (aún no completada)", float(dev["monto_total_devuelto"]) == 0)
    check("  1 item devuelto", len(dev["items"]) == 1)
    check(
        "  subtotal = 1 * 189.90 = 189.90",
        abs(float(dev["items"][0]["subtotal"]) - 189.90) < 0.01,
    )


# --- 5. Validaciones de error en POST -------------------------------------------
# 5a. Solicitud duplicada para la misma venta (debe chocar con la abierta)
r = client.post(f"{BASE}/", json=payload_dev, headers=HC)
check("POST duplicada 409", r.status_code == 409, f"-> {r.status_code} {r.text[:200]}")

# 5b. Venta ajena: el admin no es cliente, no debe poder solicitar
r = client.post(f"{BASE}/", json=payload_dev, headers=HA)
check("POST con rol no-C 403", r.status_code == 403, f"-> {r.status_code} {r.text[:200]}")

# 5c. Cantidad excedida
payload_excedido = {
    "id_venta": venta_id,
    "motivo": "Quiero devolver todo el pedido aunque no me corresponda.",
    "items": [
        {"detalle_venta_id": detalle_venta_id, "cantidad_devuelta": 99},
    ],
}
r = client.post(f"{BASE}/", json=payload_excedido, headers=HC)
check("POST cantidad excedida 409", r.status_code == 409, f"-> {r.status_code} {r.text[:200]}")

# 5d. Sin token
r = client.post(f"{BASE}/", json=payload_dev)
check("POST sin token 401", r.status_code == 401, f"-> {r.status_code} {r.text[:200]}")


# --- 6. GET / (listado) --------------------------------------------------------
r = client.get(f"{BASE}/", headers=HC)
check("GET listado Cliente 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
if r.status_code == 200:
    data = r.json()["data"]
    ids_cliente = [d["id_devolucion"] for d in data]
    check(
        "  cliente ve su devolucion",
        dev_id is not None and dev_id in ids_cliente,
    )

r = client.get(f"{BASE}/", headers=HA)
check("GET listado Admin 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")


# --- 7. GET /{id} (detalle con aislamiento por rol) ----------------------------
r = client.get(f"{BASE}/{dev_id}", headers=HC)
check("GET detalle Cliente 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")

r = client.get(f"{BASE}/{dev_id}", headers=HA)
check("GET detalle Admin 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")


# --- 8. PATCH /{id}/procesar - APROBAR (vendedor) ------------------------------
if dev_id is not None:
    r = client.patch(
        f"{BASE}/{dev_id}/procesar",
        json={"accion": "APROBAR"},
        headers=HV,
    )
    check("PATCH APROBAR 200", r.status_code == 200, f"-> {r.status_code} {r.text[:300]}")
    if r.status_code == 200:
        check("  estado=APROBADA", r.json()["data"]["estado"] == "APROBADA")

    # 8a. Re-aprobar debe ser 409 (transicion invalida)
    r = client.patch(
        f"{BASE}/{dev_id}/procesar",
        json={"accion": "APROBAR"},
        headers=HV,
    )
    check("PATCH APROBAR idempotente 409", r.status_code == 409, f"-> {r.status_code} {r.text[:200]}")

    # 8b. Cliente no puede procesar -> 403
    r = client.patch(
        f"{BASE}/{dev_id}/procesar",
        json={"accion": "COMPLETAR"},
        headers=HC,
    )
    check("PATCH con rol C 403", r.status_code == 403, f"-> {r.status_code} {r.status_code}")


# --- 9. PATCH /{id}/procesar - COMPLETAR (admin) -> kardex ENTRADA -------------
if dev_id is not None:
    # Lookup del stock actual del producto ANTES de completar
    r = client.get("/api/v1/inventario/stock", headers=HA)
    stock_items = r.json()["data"]
    stock_pre = next(
        (s["stock_total"] for s in stock_items if s["id_producto"] == p_camisa["id_producto"]),
        None,
    )

    r = client.patch(
        f"{BASE}/{dev_id}/procesar",
        json={"accion": "COMPLETAR"},
        headers=HA,
    )
    check("PATCH COMPLETAR 200", r.status_code == 200, f"-> {r.status_code} {r.text[:300]}")
    if r.status_code == 200:
        d = r.json()["data"]
        check("  estado=COMPLETADA", d["estado"] == "COMPLETADA")
        check(
            "  monto_total_devuelto = 189.90 (1 unidad * 189.90)",
            abs(float(d["monto_total_devuelto"]) - 189.90) < 0.01,
        )

    # 9a. Verificar que el stock subio en 1 (kardex ENTRADA). `stock_pre` es
    # el valor DESPUES del checkout (el checkout desconto las 2 unidades);
    # la devolucion de 1 unidad debe subirlo en 1.
    r = client.get("/api/v1/inventario/stock", headers=HA)
    stock_items_post = r.json()["data"]
    stock_post = next(
        (s["stock_total"] for s in stock_items_post if s["id_producto"] == p_camisa["id_producto"]),
        None,
    )
    check(
        f"  stock subio 1 por la devolucion (pre={stock_pre} post={stock_post})",
        stock_post is not None and stock_pre is not None and stock_post == stock_pre + 1,
        f"-> pre={stock_pre} post={stock_post}",
    )

    # 9b. Verificar que existe el movimiento ENTRADA en el kardex
    r = client.get(
        "/api/v1/inventario/movimientos",
        params={"id_producto": p_camisa["id_producto"], "tipo": "ENTRADA", "limit": 50},
        headers=HA,
    )
    if r.status_code == 200:
        movs = r.json()["data"]
        ultima_entrada = next(
            (m for m in movs if f"Devolucion #{dev_id}" in (m.get("motivo") or "")),
            None,
        )
        check("  kardex tiene ENTRADA de la devolucion", ultima_entrada is not None)
        if ultima_entrada:
            check(
                "  cantidad ENTRADA = 1",
                ultima_entrada["cantidad"] == 1,
            )


# --- 10. POST / RECHAZAR (ciclo alterno) ----------------------------------------
# Creamos otra venta fresca del Cliente para tener otra devolucion que rechazar.
r = client.post("/api/v1/ventas/checkout", json=payload_venta, headers=HC)
check("checkout 2 (para RECHAZAR) 201", r.status_code == 201, f"-> {r.status_code} {r.text[:200]}")
venta2 = r.json()["data"]
detalle2 = venta2["items"][0]["id_detalle"]

r = client.post(
    f"{BASE}/",
    json={
        "id_venta": venta2["id_venta"],
        "motivo": "Cambié de opinión sobre el modelo.",
        "items": [{"detalle_venta_id": detalle2, "cantidad_devuelta": 1}],
    },
    headers=HC,
)
check("POST devolucion 2 201", r.status_code == 201, f"-> {r.status_code} {r.text[:200]}")
dev2 = r.json()["data"]["id_devolucion"]

# RECHAZAR sin motivo -> 422 (validacion Pydantic)
r = client.patch(
    f"{BASE}/{dev2}/procesar",
    json={"accion": "RECHAZAR"},
    headers=HV,
)
check("PATCH RECHAZAR sin motivo 422", r.status_code == 422, f"-> {r.status_code} {r.text[:200]}")

# RECHAZAR con motivo -> 200
r = client.patch(
    f"{BASE}/{dev2}/procesar",
    json={"accion": "RECHAZAR", "motivo_rechazo": "Supera el alcance de la politica."},
    headers=HV,
)
check("PATCH RECHAZAR 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
if r.status_code == 200:
    check("  estado=RECHAZADA", r.json()["data"]["estado"] == "RECHAZADA")
    check(
        "  motivo_rechazo persistido",
        r.json()["data"]["motivo_rechazo"] == "Supera el alcance de la politica.",
    )


# --- 11. Resumen ---------------------------------------------------------------
print()
total_checks = PASS + FAIL
print(f"  Total checks: {total_checks}  |  PASS: {PASS}  |  FAIL: {FAIL}")
if FAIL > 0:
    print("  [SMOKE] FAIL")
    sys.exit(1)
print("  [SMOKE] PASS")
