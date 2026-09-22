# Smoke test — Módulo de Vestidor Virtual con IA (Virtual Try-On)
# Verificación de Blindaje Anti-SSRF, Privacidad (Cero Retención Biométrica), Autenticación y Failover
import base64
import io
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.modules.inventario.models import Producto
from app.modules.probador.models import FotoUsuario

client = TestClient(app)
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

print("=== SMOKE TEST: VESTIDOR VIRTUAL CON IA (SECURITY, PRIVACY & TRY-ON) ===")

# 1. Autenticación Requerida (Anti-DoS)
r_anon = client.post("/api/v1/probador-virtual/probar-directo", json={
    "producto_id": 1,
    "imagen_usuario": "data:image/jpeg;base64,12345",
})
check("Rechazo de petición anónima (401 Unauthorized)", r_anon.status_code == 401)

# Login cliente / ASU
r_login = client.post(
    "/api/v1/auth/login",
    json={"correo": "admin@attention.com", "password": "Admin123!"},
)
check("Login de usuario exitoso (200 OK)", r_login.status_code == 200)
token = r_login.json()["data"]["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Blindaje Anti-SSRF (Producto Inexistente)
r_invalid_prod = client.post(
    "/api/v1/probador-virtual/probar-directo",
    headers=headers,
    json={
        "producto_id": 999999,
        "imagen_usuario": "data:image/jpeg;base64,1234567890",
    },
)
check("Blindaje Anti-SSRF: Producto inexistente rechazado con 404", r_invalid_prod.status_code == 404)

# 3. Obtener un producto activo del catálogo
db = SessionLocal()
try:
    producto_activo = db.query(Producto).filter(Producto.estado == "Activo").first()
    assert producto_activo is not None, "Debe existir al menos un producto activo"
    id_producto = producto_activo.id_producto
    nombre_producto = producto_activo.nombre
    fotos_antes_count = db.query(FotoUsuario).count()
finally:
    db.close()

print(f"  .. Probando con producto activo #{id_producto} ('{nombre_producto}')")

# Generar una imagen sintética mínima de prueba de usuario (100x100)
img_test = Image.new("RGB", (120, 160), color=(220, 190, 170))
buf = io.BytesIO()
img_test.save(buf, format="JPEG")
foto_test_b64 = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

# 4. Probar Vestidor Virtual Efímero (POST /probar-directo)
r_tryon = client.post(
    "/api/v1/probador-virtual/probar-directo",
    headers=headers,
    json={
        "producto_id": id_producto,
        "imagen_usuario": foto_test_b64,
        "talla_seleccionada": "M",
        "complexion": "MEDIA",
        "estatura_cm": 172,
        "peso_kg": 68,
    },
)

check("Simulación de Vestidor Virtual exitosa (200 OK)", r_tryon.status_code == 200, f"Got {r_tryon.status_code}: {r_tryon.text}")
data = r_tryon.json().get("data", {})
check("Respuesta contiene imagen generada (resultado_imagen_url)", bool(data.get("resultado_imagen_url")))
check("Respuesta contiene recomendación de ajuste", bool(data.get("ajuste_estimado")))
check("Respuesta contiene motor de inferencia", bool(data.get("motor")))
print(f"  .. Motor utilizado: {data.get('motor')}")

# 5. Privacidad Absoluta y Cero Retención Biométrica
db = SessionLocal()
try:
    fotos_despues_count = db.query(FotoUsuario).count()
finally:
    db.close()

check(
    "Cero Retención Biométrica: No se guardó ninguna foto en la base de datos",
    fotos_despues_count == fotos_antes_count,
    f"(antes: {fotos_antes_count}, después: {fotos_despues_count})"
)

print(f"\nRESULTADO FINAL: {PASS} pruebas pasadas, {FAIL} fallidas.")
if FAIL > 0:
    sys.exit(1)
