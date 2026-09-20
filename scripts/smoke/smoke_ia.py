# scripts/smoke/smoke_ia.py
"""Smoke test para el Asistente IA y Recomendador de Moda (Google Gemini).

Ejecutar desde la raíz del backend:
    $env:PYTHONIOENCODING="utf-8"; python scripts/smoke/smoke_ia.py
"""
import sys
from pathlib import Path

# scripts/smoke/smoke_ia.py -> scripts/smoke -> scripts -> <backend root>
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
BASE = "/api/v1/ia"
PASS = 0
FAIL = 0


def check(nombre: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {nombre}")
    else:
        FAIL += 1
        print(f"  [FAIL] {nombre} {extra}")


print("\n=== SMOKE TEST: Asistente IA y Recomendaciones con Google Gemini ===")

# --- 1. Status del servicio IA ---
r = client.get(f"{BASE}/status")
check("GET /ia/status responde 200", r.status_code == 200, f"-> {r.status_code}")
data_status = r.json().get("data", {})
check(
    "Gemini está configurado con API Key",
    data_status.get("gemini_configurado") is True,
    f"-> {data_status}",
)
check(
    "Base de datos online para IA",
    data_status.get("database_online") is True,
    f"-> {data_status}",
)

# --- 2. Validación 422 con mensaje vacío ---
r = client.post(f"{BASE}/chat", json={"mensaje": ""})
check(
    "POST /ia/chat valida mensaje vacío con 422",
    r.status_code == 422,
    f"-> {r.status_code}",
)

# --- 3. Consulta de recomendación de prendas en talla M ---
print("\n--- Probando recomendación de prendas (talla M) ---")
r = client.post(
    f"{BASE}/chat",
    json={"mensaje": "¿Qué blusas o prendas tienen disponibles en talla M?"},
)
check("POST /ia/chat responde 200", r.status_code == 200, f"-> {r.status_code} {r.text[:200]}")
body = r.json()
data = body.get("data", {})
check("Envelope status es success", body.get("status") == "success")
check(
    "Respuesta en Markdown presente",
    bool(data.get("respuesta")) and len(data.get("respuesta")) > 20,
    f"-> {data.get('respuesta')}",
)
check(
    "Productos recomendados es una lista",
    isinstance(data.get("productos_recomendados"), list),
)
check(
    "Sugerencias de preguntas presentes",
    isinstance(data.get("sugerencias"), list) and len(data.get("sugerencias")) > 0,
)
detalles = data.get("productos_detalle", [])
check(
    "Productos detalle enriquecidos desde la BD",
    isinstance(detalles, list),
    f"-> encontrados: {len(detalles)}",
)

if detalles:
    primer_prod = detalles[0]
    check(
        "Producto detalle contiene id, nombre y precio",
        "id_producto" in primer_prod
        and "nombre" in primer_prod
        and "precio_venta" in primer_prod,
        f"-> {primer_prod}",
    )
    print(f"    Ejemplo recomendado: [{primer_prod['id_producto']}] {primer_prod['nombre']} - Bs {primer_prod['precio_venta']}")

# --- 4. Consulta sobre sucursales y horarios ---
print("\n--- Probando consulta de sucursales físicas ---")
r = client.post(
    f"{BASE}/chat",
    json={"mensaje": "¿Dónde quedan sus sucursales y cuáles son sus horarios de atención?"},
)
check("POST /ia/chat (sucursales) responde 200", r.status_code == 200)
data_suc = r.json().get("data", {})
resp_suc = data_suc.get("respuesta", "").lower()
check(
    "Respuesta contiene información de atención o tiendas",
    ("sucursal" in resp_suc or "horario" in resp_suc or "atención" in resp_suc or "atencion" in resp_suc),
    f"-> {resp_suc[:150]}",
)

# --- 5. Consulta con historial previo (contexto) ---
print("\n--- Probando conversación con historial multivariable ---")
r = client.post(
    f"{BASE}/chat",
    json={
        "mensaje": "¿Y tienen cupones de descuento para comprar alguna de esas prendas?",
        "historial": [
            {
                "rol": "usuario",
                "contenido": "¿Tienen vestidos elegantes para fiesta?",
            },
            {
                "rol": "asistente",
                "contenido": "¡Sí! Te recomiendo nuestro Vestido Midi Elegante que luce increíble.",
            },
        ],
    },
)
check("POST /ia/chat con historial responde 200", r.status_code == 200)
data_hist = r.json().get("data", {})
check(
    "Respuesta con contexto de descuentos generada",
    bool(data_hist.get("respuesta")),
)

print(f"\n========================================================")
print(f"  Resultado Smoke IA: {PASS} PASS, {FAIL} FAIL")
print(f"========================================================")

if FAIL > 0:
    sys.exit(1)
else:
    sys.exit(0)
