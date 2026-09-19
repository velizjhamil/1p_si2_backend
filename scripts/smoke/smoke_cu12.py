# Smoke tests CU12 — Gestion de Descuentos / Cupones (CRUD).
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu12.py
#
# Cubre:
# - Listar vacio (o lo que haya) como GS/ASU.
# - Crear un cupon con codigo: 201, devuelve el id.
# - Validacion 422: tipo invalido.
# - Validacion 422: PORCENTAJE > 100.
# - Validacion 422: fecha_fin < fecha_inicio.
# - Validacion 409: codigo duplicado.
# - Validacion 401: sin token.
# - Listar con filtro `q` y filtro `tipo`.
# - Obtener por id: 200 con datos consistentes.
# - Actualizar: cambiar nombre y desactivar.
# - Validacion 404: id inexistente.
# - Eliminar: 200 OK (no fue usado).
# - Eliminar uno que ya tiene usos_actuales > 0: 409 (restriccion de negocio).
# - Limpieza: borra los descuentos creados en este test.
import sys
from datetime import date, timedelta
from pathlib import Path

# scripts/smoke/smoke_cu12.py -> scripts/smoke -> scripts -> <backend root>
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/descuentos"
PASS = 0
FAIL = 0


def check(nombre, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] {}".format(nombre))
    else:
        FAIL += 1
        print("  [FAIL] {} {}".format(nombre, extra))


def login(correo, password="Admin123!"):
    r = client.post(
        "/api/v1/auth/login",
        json={"correo": correo, "password": password},
    )
    if r.status_code != 200:
        return None, r
    return r.json()["data"]["access_token"], r


# --- 1. Login de los roles que usaremos ---------------------------------------
token_g, r = login("gerente@attention.com")
check("login Gerente", token_g is not None, "-> {} {}".format(r.status_code, r.text[:200]))
HG = {"Authorization": "Bearer {}".format(token_g)}

token_a, r = login("admin@attention.com")
check("login Admin", token_a is not None, "-> {} {}".format(r.status_code, r.text[:200]))
HA = {"Authorization": "Bearer {}".format(token_a)}

token_c, r = login("cliente@attention.com")
check("login Cliente", token_c is not None, "-> {} {}".format(r.status_code, r.text[:200]))
HC = {"Authorization": "Bearer {}".format(token_c)}

# --- 2. Listar descuentos existentes (puede haber previos) --------------------
r = client.get(BASE + "/", headers=HA)
check("GET / 200 como Admin", r.status_code == 200, "-> {}".format(r.status_code))
if r.status_code == 200:
    body = r.json()
    check(
        "envelope con data y total",
        "data" in body and "total" in body,
        "-> keys: {}".format(list(body.keys())),
    )

# --- 3. Validacion 401: sin token ---------------------------------------------
r = client.get(BASE + "/")
check("GET sin token 401", r.status_code == 401, "-> {}".format(r.status_code))

# --- 4. Crear un cupon con codigo ---------------------------------------------
codigo_test = "TEST{}".format(date.today().strftime("%H%M%S"))
payload_cupon = {
    "codigo": codigo_test,
    "nombre": "Cupon de prueba 15%",
    "descripcion": "Smoke test CU12",
    "tipo": "PORCENTAJE",
    "valor": 15,
    "fecha_inicio": date.today().isoformat(),
    "fecha_fin": (date.today() + timedelta(days=30)).isoformat(),
    "activo": True,
    "usos_maximos": 100,
    "monto_minimo_compra": 50,
}
r = client.post(BASE + "/", json=payload_cupon, headers=HA)
check(
    "POST cupon 201",
    r.status_code == 201,
    "-> {} {}".format(r.status_code, r.text[:300]),
)
cupon_id = None
if r.status_code == 201:
    body = r.json()
    cupon_id = body["data"]["id_descuento"]
    check("  codigo en mayusculas", body["data"]["codigo"] == codigo_test.upper())
    check("  usos_actuales=0", body["data"]["usos_actuales"] == 0)
    check("  activo=True", body["data"]["activo"] is True)

# --- 5. Validacion 409: codigo duplicado --------------------------------------
r = client.post(BASE + "/", json=payload_cupon, headers=HA)
check(
    "POST codigo duplicado 409",
    r.status_code == 409,
    "-> {} {}".format(r.status_code, r.text[:200]),
)

# --- 6. Validacion 422: tipo invalido -----------------------------------------
r = client.post(
    BASE + "/",
    json={**payload_cupon, "codigo": "INVALIDO", "tipo": "BITCOIN"},
    headers=HA,
)
check(
    "POST tipo invalido 422",
    r.status_code == 422,
    "-> {}".format(r.status_code),
)

# --- 7. Validacion 422: PORCENTAJE > 100 --------------------------------------
r = client.post(
    BASE + "/",
    json={**payload_cupon, "codigo": "PORC150", "valor": 150},
    headers=HA,
)
check(
    "POST porcentaje > 100 422",
    r.status_code == 422,
    "-> {}".format(r.status_code),
)

# --- 8. Validacion 422: fecha_fin < fecha_inicio ------------------------------
r = client.post(
    BASE + "/",
    json={
        **payload_cupon,
        "codigo": "FECHAS",
        "fecha_inicio": (date.today() + timedelta(days=10)).isoformat(),
        "fecha_fin": date.today().isoformat(),
    },
    headers=HA,
)
check(
    "POST fecha_fin < fecha_inicio 422",
    r.status_code == 422,
    "-> {}".format(r.status_code),
)

# --- 9. Crear una regla automatica (sin codigo) -------------------------------
payload_regla = {
    "codigo": None,
    "nombre": "Regla 20% en toda la tienda",
    "tipo": "PORCENTAJE",
    "valor": 20,
    "fecha_inicio": date.today().isoformat(),
    "activo": True,
}
r = client.post(BASE + "/", json=payload_regla, headers=HA)
check(
    "POST regla automatica 201",
    r.status_code == 201,
    "-> {} {}".format(r.status_code, r.text[:300]),
)
regla_id = None
if r.status_code == 201:
    regla_id = r.json()["data"]["id_descuento"]
    check("  codigo null en regla", r.json()["data"]["codigo"] is None)

# --- 10. Listar con filtro q -------------------------------------------------
r = client.get(BASE + "/?q=Cupon", headers=HA)
if r.status_code == 200:
    items = r.json()["data"]
    nombres = [d["nombre"] for d in items]
    check(
        "filtro q=Cupon encuentra el cupon",
        any("Cupon" in n for n in nombres),
        "-> {}".format(nombres),
    )

# --- 11. Listar con filtro tipo=MONTO_FIJO ------------------------------------
r = client.post(
    BASE + "/",
    json={
        "codigo": "MONTO50",
        "nombre": "Regla 50 Bs. de descuento",
        "tipo": "MONTO_FIJO",
        "valor": 50,
        "fecha_inicio": date.today().isoformat(),
        "activo": True,
    },
    headers=HA,
)
if r.status_code == 201:
    monto_id = r.json()["data"]["id_descuento"]
r = client.get(BASE + "/?tipo=MONTO_FIJO", headers=HA)
if r.status_code == 200:
    items = r.json()["data"]
    tipos = [d["tipo"] for d in items]
    check(
        "filtro tipo=MONTO_FIJO solo trae esos",
        all(t == "MONTO_FIJO" for t in tipos) and len(tipos) >= 1,
        "-> tipos: {}".format(tipos),
    )

# --- 12. Obtener por id -------------------------------------------------------
if cupon_id:
    r = client.get(BASE + "/{}".format(cupon_id), headers=HA)
    check(
        "GET /{id} 200",
        r.status_code == 200,
        "-> {}".format(r.status_code),
    )
    if r.status_code == 200:
        check(
            "  mismo id",
            r.json()["data"]["id_descuento"] == cupon_id,
        )

# --- 13. Actualizar ------------------------------------------------------------
if cupon_id:
    r = client.put(
        BASE + "/{}".format(cupon_id),
        json={"nombre": "Cupon actualizado", "activo": False},
        headers=HA,
    )
    check(
        "PUT actualizar 200",
        r.status_code == 200,
        "-> {} {}".format(r.status_code, r.text[:200]),
    )
    if r.status_code == 200:
        body = r.json()["data"]
        check("  nombre actualizado", body["nombre"] == "Cupon actualizado")
        check("  activo=False", body["activo"] is False)

# --- 14. 404 id inexistente ----------------------------------------------------
r = client.get(BASE + "/99999", headers=HA)
check("GET /99999 404", r.status_code == 404, "-> {}".format(r.status_code))

# --- 15. DELETE con usos_actuales > 0 -> 409 ----------------------------------
# Forzamos usos_actuales > 0 con SQL directo y luego intentamos borrar.
from sqlalchemy import text

from app.core.database import engine

if cupon_id:
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE descuentos SET usos_actuales = 1 WHERE id_descuento = :id"),
            {"id": cupon_id},
        )
        conn.commit()
    r = client.delete(BASE + "/{}".format(cupon_id), headers=HA)
    check(
        "DELETE con usos_actuales>0 409",
        r.status_code == 409,
        "-> {} {}".format(r.status_code, r.text[:200]),
    )

# --- 16. DELETE de uno sin usos -> 200 ----------------------------------------
if regla_id:
    r = client.delete(BASE + "/{}".format(regla_id), headers=HA)
    check(
        "DELETE regla sin usos 200",
        r.status_code == 200,
        "-> {} {}".format(r.status_code, r.text[:200]),
    )

# --- 17. Aislamiento: el Cliente puede ver descuentos? -----------------------
# Decidimos que GET requiere auth pero NO discrimina rol; el cliente
# puede listar (la decision de quien puede CREAR esta en el frontend).
# Lo verificamos para asegurar que el comportamiento es consistente.
r = client.get(BASE + "/", headers=HC)
check(
    "GET como Cliente 200 (decision documentada: lectura libre, escritura restringida)",
    r.status_code == 200,
    "-> {}".format(r.status_code),
)

# --- 18. Limpieza: borrar el cupon residual -----------------------------------
# El cupon con usos_actuales>0 no se borra via endpoint (es lo correcto,
# preserva historial). Lo limpiamos por SQL para no dejar ruido.
with engine.connect() as conn:
    conn.execute(
        text("DELETE FROM descuentos WHERE codigo = :c OR codigo = :c2"),
        {"c": codigo_test.upper(), "c2": "MONTO50"},
    )
    conn.commit()
print("  .. limpieza OK (descuentos de prueba eliminados)")

print("\nRESULTADO: {} PASS / {} FAIL".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
