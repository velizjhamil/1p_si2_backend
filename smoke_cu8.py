# Smoke tests CU8 — Probador Virtual (TestClient, sin necesidad del server levantado)
# Ejecutar: python smoke_cu8.py
import sys

sys.path.insert(0, ".")
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/probador-virtual"
PASS = 0
FAIL = 0

# Data URL mínima válida para el test (1x1 px PNG)
FOTO_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def check(nombre, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {nombre}")
    else:
        FAIL += 1
        print(f"  [FAIL] {nombre} {extra}")


def login(correo, password="Admin123!"):
    r = client.post("/api/v1/auth/login", json={"correo": correo, "password": password})
    if r.status_code != 200:
        return None
    return r.json()["data"]["access_token"]


# --- 1. Login Cliente ----------------------------------------------------------
token_c = login("cliente@attention.com")
check("login Cliente", token_c is not None)
HC = {"Authorization": f"Bearer {token_c}"}

token_a = login("admin@attention.com")
HA = {"Authorization": f"Bearer {token_a}"}

# --- 2. POST /subir-foto --------------------------------------------------------
# Con medidas: estatura 170, peso 55 -> IMC 19.03 -> DELGADA
r = client.post(
    f"{BASE}/subir-foto",
    json={"imagen_data_url": FOTO_DATA_URL, "estatura": 170, "peso": 55},
    headers=HC,
)
check("POST subir-foto 201", r.status_code == 201, f"-> {r.status_code} {r.text[:300]}")
foto_id = None
if r.status_code == 201:
    f = r.json()["data"]
    foto_id = f["id_foto"]
    check("  complexion derivada DELGADA", f["complexion"] == "DELGADA", f"-> {f['complexion']}")
    check("  url_imagen persistida", f["url_imagen"].startswith("data:image/"))
    check("  id_foto asignado", foto_id is not None)

# Sin medidas -> NO_INDICADA
r = client.post(
    f"{BASE}/subir-foto",
    json={"imagen_data_url": FOTO_DATA_URL},
    headers=HC,
)
if r.status_code == 201:
    check("  sin medidas -> NO_INDICADA", r.json()["data"]["complexion"] == "NO_INDICADA")

# Medidas extremas -> 422 (validación Pydantic ge/le)
r = client.post(
    f"{BASE}/subir-foto",
    json={"imagen_data_url": FOTO_DATA_URL, "estatura": 99},
    headers=HC,
)
check("POST estatura fuera de rango 422", r.status_code == 422, f"-> {r.status_code}")

# Data URL inválida -> 422
r = client.post(
    f"{BASE}/subir-foto",
    json={"imagen_data_url": "no-es-una-imagen"},
    headers=HC,
)
check("POST data URL inválida 422", r.status_code == 422, f"-> {r.status_code}")

# 401 sin token
r = client.post(f"{BASE}/subir-foto", json={"imagen_data_url": FOTO_DATA_URL})
check("POST sin token 401", r.status_code == 401, f"-> {r.status_code}")

# --- 3. POST /probar — simulación ------------------------------------------------
# Producto 1 = Camisa Oxford Formal, tallas S/M/L/XL (seed), precio 189.90
# Foto DELGADA (IMC 19) -> recomendada S. Probamos M -> AJUSTADO? No: M > S -> HOLGADO.
# Probamos S -> PERFECTO.
r = client.post(
    f"{BASE}/probar",
    json={
        "foto_id": foto_id,
        "producto_id": 1,
        "talla_seleccionada": "S",
        "color_nombre": "Blanco",
        "color_hex": "#ffffff",
    },
    headers=HC,
)
check("POST probar 201", r.status_code == 201, f"-> {r.status_code} {r.text[:400]}")
sim_id = None
if r.status_code == 201:
    s = r.json()["data"]
    sim_id = s["id_simulacion"]
    check("  talla_recomendada S (DELGADA)", s["talla_recomendada"] == "S", f"-> {s['talla_recomendada']}")
    check("  ajuste PERFECTO (elegida=recomendada)", s["ajuste_estimado"] == "PERFECTO", f"-> {s['ajuste_estimado']}")
    check("  producto_nombre real", s["producto_nombre"] == "Camisa Oxford Formal")
    check("  precio real del catálogo", float(s["precio"]) == 189.90)
    check("  resultado_imagen_url = foto", s["resultado_imagen_url"].startswith("data:image/"))
    check("  categoria embebida", s["categoria"] is not None)

# Talla M contra recomendada S -> HOLGADO (una talla más)
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_id, "producto_id": 1, "talla_seleccionada": "M"},
    headers=HC,
)
if r.status_code == 201:
    check("  talla mayor -> HOLGADO", r.json()["data"]["ajuste_estimado"] == "HOLGADO")

# Foto con medidas MEDIA (170cm, 75kg -> IMC 25.95) -> recomendada M; probamos S -> AJUSTADO
r = client.post(
    f"{BASE}/subir-foto",
    json={"imagen_data_url": FOTO_DATA_URL, "estatura": 170, "peso": 75},
    headers=HC,
)
foto_media_id = r.json()["data"]["id_foto"] if r.status_code == 201 else None
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_media_id, "producto_id": 1, "talla_seleccionada": "S"},
    headers=HC,
)
if r.status_code == 201:
    check("  complexion MEDIA -> recomienda M", r.json()["data"]["talla_recomendada"] == "M")
    check("  talla menor -> AJUSTADO", r.json()["data"]["ajuste_estimado"] == "AJUSTADO")

# Validaciones
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": 99999, "producto_id": 1, "talla_seleccionada": "M"},
    headers=HC,
)
check("POST foto inexistente 404", r.status_code == 404, f"-> {r.status_code}")

r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_id, "producto_id": 99999, "talla_seleccionada": "M"},
    headers=HC,
)
check("POST producto inexistente 422", r.status_code == 422, f"-> {r.status_code}")

# Talla no disponible del producto -> 422
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_id, "producto_id": 1, "talla_seleccionada": "XXL"},
    headers=HC,
)
check("POST talla no disponible 422", r.status_code == 422, f"-> {r.status_code}")

# Foto ajena: ASU intenta usar la foto del cliente -> 404
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_id, "producto_id": 1, "talla_seleccionada": "M"},
    headers=HA,
)
check("POST foto ajena 404 (aislamiento por usuario)", r.status_code == 404, f"-> {r.status_code}")

# Producto Inactivo (id 10 Camisa Lino Verano) -> 409
r = client.post(
    f"{BASE}/probar",
    json={"foto_id": foto_id, "producto_id": 10, "talla_seleccionada": "M"},
    headers=HC,
)
check("POST producto Inactivo 409", r.status_code == 409, f"-> {r.status_code}")

# --- 4. GET /historial -----------------------------------------------------------
r = client.get(f"{BASE}/historial", headers=HC)
check("GET historial 200", r.status_code == 200, f"-> {r.status_code}")
if r.status_code == 200:
    data = r.json()["data"]
    check("  historial con simulaciones", len(data) >= 3, f"-> {len(data)}")
    check("  orden reciente primero", data[0]["fecha_simulacion"] >= data[-1]["fecha_simulacion"])

# ASU no ve las simulaciones del cliente
r = client.get(f"{BASE}/historial", headers=HA)
if r.status_code == 200:
    check("  historial aislado por usuario", len(r.json()["data"]) == 0)

# --- 5. POST /lookbook + DELETE ----------------------------------------------------
r = client.post(f"{BASE}/lookbook", json={"id_simulacion": sim_id}, headers=HC)
check("POST lookbook 200", r.status_code == 200, f"-> {r.status_code}")

r = client.post(f"{BASE}/lookbook", json={"id_simulacion": 99999}, headers=HC)
check("POST lookbook inexistente 404", r.status_code == 404, f"-> {r.status_code}")

r = client.delete(f"{BASE}/lookbook/{sim_id}", headers=HC)
check("DELETE prueba 200", r.status_code == 200, f"-> {r.status_code}")

r = client.delete(f"{BASE}/lookbook/{sim_id}", headers=HC)
check("DELETE ya eliminada 404", r.status_code == 404, f"-> {r.status_code}")

# --- 6. Limpieza: borrar fotos/simulaciones de prueba -------------------------------
from sqlalchemy import text

from app.core.database import engine

with engine.connect() as conn:
    n1 = conn.execute(text("DELETE FROM simulaciones_probador")).rowcount
    n2 = conn.execute(text("DELETE FROM fotos_usuario")).rowcount
    conn.commit()
    print(f"  .. limpieza OK ({n1} simulaciones, {n2} fotos de prueba eliminadas)")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
