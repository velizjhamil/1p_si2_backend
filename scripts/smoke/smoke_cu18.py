# Smoke tests CU18 — Gestion de Envio (TestClient contra la DB configurada)
# Ejecutar desde la raiz del backend: python scripts/smoke/smoke_cu18.py
#
# LIMPIEZA GARANTIZADA (try/finally): el script crea usuarios temporales
# (correo smoke18_*@attention-smoke.com), ventas, envios, notificaciones y
# kardex, y al terminar los borra y restaura stock/estado del producto usado.
# Al final compara los conteos con los de antes de empezar: si difieren, FALLA.
# No toca ningun dato real (todo lo que borra pertenece a los usuarios
# temporales que el propio script creo).
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import obtener_hash_password
from app.main import app
from app.modules.usuarios.models import Rol, Usuario

client = TestClient(app)
API = "/api/v1"
PASS = 0
FAIL = 0
PASSWORD = "Smoke18!pass"
SUFIJO = uuid.uuid4().hex[:8]
DOMINIO = "attention-smoke.com"
ID_PRODUCTO = 1  # producto con stock alto; se restaura al final
TABLAS = (
    "ventas", "detalle_ventas", "movimientos_inventario",
    "notificaciones", "usuarios", "envios", "envio_historial",
)


def check(nombre, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {nombre}")
    else:
        FAIL += 1
        print(f"  [FAIL] {nombre} {extra}")


def expect(nombre, r, codigo):
    check(nombre, r.status_code == codigo, f"-> {r.status_code} {r.text[:160]}")


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def futuro(dias=1):
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


def conteos(db):
    return {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLAS}


def limpiar(db, ids: list[str]) -> None:
    """Borra TODO lo generado por los usuarios temporales (ids)."""
    if not ids:
        return
    p = {"ids": ids}
    ventas = (
        "SELECT id_venta FROM ventas WHERE id_cliente = ANY(CAST(:ids AS uuid[])) "
        "OR id_vendedor = ANY(CAST(:ids AS uuid[]))"
    )
    db.execute(text(
        f"DELETE FROM envio_historial WHERE id_envio IN "
        f"(SELECT id_envio FROM envios WHERE id_venta IN ({ventas}))"), p)
    db.execute(text(f"DELETE FROM envios WHERE id_venta IN ({ventas})"), p)
    db.execute(text("DELETE FROM notificaciones WHERE id_usuario = ANY(CAST(:ids AS uuid[]))"), p)
    db.execute(text("DELETE FROM movimientos_inventario WHERE id_usuario = ANY(CAST(:ids AS uuid[]))"), p)
    db.execute(text(f"DELETE FROM detalle_ventas WHERE id_venta IN ({ventas})"), p)
    db.execute(text(
        "DELETE FROM ventas WHERE id_cliente = ANY(CAST(:ids AS uuid[])) "
        "OR id_vendedor = ANY(CAST(:ids AS uuid[]))"), p)
    db.execute(text("DELETE FROM usuario_permiso WHERE usuario_id = ANY(CAST(:ids AS uuid[]))"), p)
    db.execute(text("DELETE FROM usuarios WHERE id_usuario = ANY(CAST(:ids AS uuid[]))"), p)
    db.commit()


db = SessionLocal()

# --- 0. Precondiciones y linea base -------------------------------------------
residuo = db.execute(text(
    "SELECT count(*) FROM usuarios WHERE correo LIKE 'smoke18\\_%@" + DOMINIO + "'"
)).scalar()
if residuo:
    print(f"ABORTADO: hay {residuo} usuarios smoke18_* de una corrida previa. "
          "Revisar manualmente antes de continuar.")
    sys.exit(2)

base = conteos(db)
prod0 = db.execute(text(
    "SELECT stock_total, estado FROM productos WHERE id_producto = :p"), {"p": ID_PRODUCTO}
).one()
print(f"Linea base: {base} | producto {ID_PRODUCTO}: stock={prod0[0]} estado={prod0[1]}")

ids_temporales: list[str] = []
tok: dict[str, str] = {}
uid: dict[str, str] = {}

try:
    # --- 1. Usuarios temporales (uno por rol) --------------------------------
    print("\n[1] Setup: usuarios temporales")
    roles = {r.nombre_rol: r for r in db.query(Rol).all()}
    for clave, rol in (("ASU", "ASU"), ("GS", "GS"), ("V", "V"), ("CA", "C"),
                       ("CB", "C"), ("D1", "D"), ("D2", "D")):
        u = Usuario(
            nombre=f"Smoke{clave}", apellido="CU18",
            correo=f"smoke18_{clave.lower()}_{SUFIJO}@{DOMINIO}",
            password=obtener_hash_password(PASSWORD), estado=True,
            id_rol=roles[rol].id_rol,
        )
        db.add(u)
        db.flush()
        ids_temporales.append(str(u.id_usuario))
        uid[clave] = str(u.id_usuario)
        u_correo = u.correo
        db.commit()
        r = client.post(f"{API}/auth/login", json={"correo": u_correo, "password": PASSWORD})
        check(f"login {clave} ({rol})", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
        tok[clave] = r.json()["data"]["access_token"]

    entrega = {
        "nombre_cliente": "Cliente Smoke", "correo": f"cli_{SUFIJO}@{DOMINIO}",
        "telefono": "70000000", "direccion": "Av. Smoke 123", "ciudad": "Santa Cruz",
        "referencia": "Casa azul",
    }

    def checkout(clave, cantidad=1, **extra):
        payload = {
            "items": [{"producto_id": ID_PRODUCTO, "cantidad": cantidad}],
            "metodo_pago": "EFECTIVO", "datos_entrega": entrega, **extra,
        }
        # El checkout online es solo del rol C; la venta de mostrador (POS) va por /ventas/pos.
        ruta = "pos" if extra.get("tipo_venta") == "POS" else "checkout"
        return client.post(f"{API}/ventas/{ruta}", json=payload, headers=H(tok[clave]))

    # --- 2. Creacion: checkout a domicilio genera envio ----------------------
    print("\n[2] Checkout -> creacion del envio")
    r = checkout("CA")
    expect("checkout online 201", r, 201)
    v1 = r.json()["data"]
    check("  venta tipo_entrega=DOMICILIO", v1["tipo_entrega"] == "DOMICILIO")
    r = client.get(f"{API}/envios/por-venta/{v1['id_venta']}", headers=H(tok["CA"]))
    expect("  cliente ve el envio de su venta (por-venta)", r, 200)
    e1 = r.json()["data"]
    E1 = e1["id_envio"]
    check("  estado inicial PREPARANDO", e1["estado"] == "PREPARANDO")
    check("  sin sucursal ni repartidor todavia", e1["codigo_sucursal"] is None and e1["repartidor_id"] is None)
    check("  datos_entrega tomados de la venta (sin duplicar)", e1["datos_entrega"]["direccion"] == "Av. Smoke 123")
    check("  items del paquete incluidos", len(e1["items"]) == 1 and e1["items"][0]["producto_id"] == ID_PRODUCTO)
    r = client.get(f"{API}/envios/{E1}/historial", headers=H(tok["GS"]))
    h = r.json()["data"]
    check("  historial inicial: None->PREPARANDO", [(x["estado_anterior"], x["estado_nuevo"]) for x in h] == [(None, "PREPARANDO")])

    r = checkout("V", tipo_venta="POS", id_cliente_override=uid["CA"])
    expect("checkout POS 201", r, 201)
    v_pos = r.json()["data"]
    check("  POS => tipo_entrega=RETIRO", v_pos["tipo_entrega"] == "RETIRO")
    expect("  POS: sin envio (404 por-venta)", client.get(f"{API}/envios/por-venta/{v_pos['id_venta']}", headers=H(tok["GS"])), 404)
    r = checkout("CA", tipo_entrega="RETIRO")
    expect("checkout online con RETIRO explicito 201", r, 201)
    v_ret = r.json()["data"]
    check("  RETIRO explicito => sin envio", client.get(f"{API}/envios/por-venta/{v_ret['id_venta']}", headers=H(tok["GS"])).status_code == 404)

    ventas_antes = db.execute(text("SELECT count(*) FROM ventas")).scalar()
    r = checkout("CA", cantidad=10**6)
    expect("checkout sin stock 409", r, 409)
    db.expire_all()
    check("  rollback: no queda venta ni envio huerfano",
          db.execute(text("SELECT count(*) FROM ventas")).scalar() == ventas_antes)

    # envios adicionales para los otros escenarios
    v2 = checkout("CA").json()["data"]
    E2 = client.get(f"{API}/envios/por-venta/{v2['id_venta']}", headers=H(tok["GS"])).json()["data"]["id_envio"]
    v3 = checkout("CB").json()["data"]
    E3 = client.get(f"{API}/envios/por-venta/{v3['id_venta']}", headers=H(tok["GS"])).json()["data"]["id_envio"]

    # --- 3. Creacion manual --------------------------------------------------
    print("\n[3] POST /envios (inicio manual)")
    expect("venta que ya tiene envio -> 409", client.post(f"{API}/envios", json={"id_venta": v1["id_venta"]}, headers=H(tok["GS"])), 409)
    expect("venta RETIRO -> 400", client.post(f"{API}/envios", json={"id_venta": v_pos["id_venta"]}, headers=H(tok["GS"])), 400)
    expect("venta inexistente -> 404", client.post(f"{API}/envios", json={"id_venta": 99999999}, headers=H(tok["GS"])), 404)
    expect("Vendedor no inicia envios -> 403", client.post(f"{API}/envios", json={"id_venta": v1["id_venta"]}, headers=H(tok["V"])), 403)
    expect("payload invalido -> 422", client.post(f"{API}/envios", json={"id_venta": 0}, headers=H(tok["GS"])), 422)

    # --- 4. Autenticacion y autorizacion ---------------------------------------
    print("\n[4] Autenticacion / roles / aislamiento")
    expect("sin token -> 401", client.get(f"{API}/envios"), 401)
    expect("token invalido -> 401", client.get(f"{API}/envios", headers=H("x.y.z")), 401)
    expect("Vendedor no accede a envios -> 403", client.get(f"{API}/envios", headers=H(tok["V"])), 403)
    expect("Cliente B no ve el envio de A -> 403", client.get(f"{API}/envios/{E1}", headers=H(tok["CB"])), 403)
    expect("Cliente B no ve historial ajeno -> 403", client.get(f"{API}/envios/{E1}/historial", headers=H(tok["CB"])), 403)
    expect("Cliente B no ve por-venta ajeno -> 403", client.get(f"{API}/envios/por-venta/{v1['id_venta']}", headers=H(tok["CB"])), 403)
    expect("Repartidor sin asignacion no ve el envio -> 403", client.get(f"{API}/envios/{E1}", headers=H(tok["D1"])), 403)
    r = client.get(f"{API}/envios?limit=100", headers=H(tok["CA"]))
    dat = r.json()["data"]
    check("Cliente A lista solo los suyos", r.status_code == 200 and dat and all(e["cliente_id"] == uid["CA"] for e in dat))
    check("  ... y no ve el de B", E3 not in [e["id_envio"] for e in dat])
    r = client.get(f"{API}/envios?limit=100", headers=H(tok["D1"]))
    check("Repartidor sin asignaciones lista 0", r.status_code == 200 and r.json()["data"] == [])
    r = client.get(f"{API}/envios?limit=100", headers=H(tok["GS"]))
    ids_gs = [e["id_envio"] for e in r.json()["data"]]
    check("GS ve todos los envios", {E1, E2, E3} <= set(ids_gs))
    expect("Cliente no confirma preparacion -> 403", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 1}, headers=H(tok["CA"])), 403)
    expect("Repartidor no confirma preparacion -> 403", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 1}, headers=H(tok["D1"])), 403)
    expect("GS consulta repartidores 200", client.get(f"{API}/envios/repartidores", headers=H(tok["GS"])), 200)
    expect("Repartidor no consulta repartidores -> 403", client.get(f"{API}/envios/repartidores", headers=H(tok["D1"])), 403)
    expect("Cliente no consulta repartidores -> 403", client.get(f"{API}/envios/repartidores", headers=H(tok["CA"])), 403)
    r = client.get(f"{API}/envios/repartidores", headers=H(tok["ASU"]))
    rep = {x["id_usuario"]: x for x in r.json()["data"]}
    check("  lista incluye a D1 y D2 (rol D activos)", uid["D1"] in rep and uid["D2"] in rep)
    check("  no incluye usuarios que no son D", uid["V"] not in rep and uid["GS"] not in rep)

    # --- 5. Consulta y filtros -------------------------------------------------
    print("\n[5] Consulta / filtros")
    expect("GET envio inexistente -> 404", client.get(f"{API}/envios/99999999", headers=H(tok["GS"])), 404)
    expect("GET historial inexistente -> 404", client.get(f"{API}/envios/99999999/historial", headers=H(tok["GS"])), 404)
    expect("por-venta sin envio -> 404", client.get(f"{API}/envios/por-venta/99999999", headers=H(tok["GS"])), 404)
    expect("estado de filtro invalido -> 400", client.get(f"{API}/envios?estado=VOLANDO", headers=H(tok["GS"])), 400)
    r = client.get(f"{API}/envios?estado=PREPARANDO&limit=100", headers=H(tok["GS"]))
    check("filtro estado=PREPARANDO", r.status_code == 200 and E1 in [e["id_envio"] for e in r.json()["data"]])
    r = client.get(f"{API}/envios?q={v1['codigo']}", headers=H(tok["GS"]))
    check("busqueda por codigo de venta", r.status_code == 200 and [e["id_envio"] for e in r.json()["data"]] == [E1])
    r = client.get(f"{API}/envios?limit=1&page=1", headers=H(tok["GS"]))
    check("paginacion (limit=1)", r.status_code == 200 and len(r.json()["data"]) == 1 and r.json()["pages"] >= 1)

    # --- 6. Transiciones invalidas desde PREPARANDO ----------------------------
    print("\n[6] Transiciones invalidas (envio en PREPARANDO)")
    expect("PREPARANDO -> ENTREGADO -> 409", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "ENTREGADO"}, headers=H(tok["GS"])), 409)
    expect("PREPARANDO -> EN_RUTA -> 409", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["GS"])), 409)
    expect("intento fallido sin estar EN_RUTA -> 409", client.patch(f"{API}/envios/{E1}/intento-fallido", json={"motivo": "Cliente no responde"}, headers=H(tok["GS"])), 409)
    expect("reprogramar sin intento fallido previo -> 409", client.patch(f"{API}/envios/{E1}/reprogramar", json={"nueva_fecha_entrega": futuro()}, headers=H(tok["GS"])), 409)
    expect("asignar sin preparacion confirmada -> 409", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": uid["D1"]}, headers=H(tok["GS"])), 409)
    expect("LISTO_ENVIO por /estado no permitido -> 422", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "LISTO_ENVIO"}, headers=H(tok["GS"])), 422)
    expect("INTENTO_FALLIDO por /estado no permitido -> 422", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "INTENTO_FALLIDO"}, headers=H(tok["GS"])), 422)
    expect("CANCELADO sin motivo -> 422", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "CANCELADO"}, headers=H(tok["GS"])), 422)

    # --- 7. Confirmar preparacion ----------------------------------------------
    print("\n[7] Confirmar preparacion")
    expect("sucursal inexistente -> 400", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 99999}, headers=H(tok["GS"])), 400)
    expect("codigo_sucursal=0 -> 422", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 0}, headers=H(tok["GS"])), 422)
    expect("sin sucursal en el payload -> 422", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={}, headers=H(tok["GS"])), 422)
    expect("envio inexistente -> 404", client.patch(f"{API}/envios/99999999/confirmar-preparacion", json={"codigo_sucursal": 1}, headers=H(tok["GS"])), 404)
    r = client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 1, "observacion": "Empacado"}, headers=H(tok["GS"]))
    expect("GS confirma preparacion 200", r, 200)
    d = r.json()["data"]
    check("  estado LISTO_ENVIO y sucursal fijada", d["estado"] == "LISTO_ENVIO" and d["codigo_sucursal"] == 1 and d["sucursal_nombre"])
    expect("  repetir confirmacion -> 409", client.patch(f"{API}/envios/{E1}/confirmar-preparacion", json={"codigo_sucursal": 1}, headers=H(tok["GS"])), 409)

    # --- 8. Asignar repartidor -------------------------------------------------
    print("\n[8] Asignar repartidor")
    expect("usuario que no es repartidor -> 400", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": uid["V"]}, headers=H(tok["GS"])), 400)
    expect("repartidor inexistente -> 400", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": str(uuid.uuid4())}, headers=H(tok["GS"])), 400)
    expect("uuid invalido -> 422", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": "no-es-uuid"}, headers=H(tok["GS"])), 422)
    expect("fecha estimada pasada -> 400", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": uid["D1"], "fecha_estimada_entrega": futuro(-1)}, headers=H(tok["GS"])), 400)
    expect("Repartidor no asigna -> 403", client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": uid["D1"]}, headers=H(tok["D1"])), 403)
    r = client.patch(f"{API}/envios/{E1}/asignar", json={"id_repartidor": uid["D1"], "fecha_estimada_entrega": futuro(1)}, headers=H(tok["GS"]))
    expect("GS asigna a D1 200", r, 200)
    d = r.json()["data"]
    check("  ASIGNADO con repartidor y fecha estimada", d["estado"] == "ASIGNADO" and d["repartidor_id"] == uid["D1"] and d["fecha_estimada_entrega"])
    check("  transiciones para GS = EN_RUTA, CANCELADO", d["transiciones_permitidas"] == ["EN_RUTA", "CANCELADO"])
    r = client.get(f"{API}/envios/{E1}", headers=H(tok["D1"]))
    check("  D1 (asignado) ve el envio; sus transiciones = EN_RUTA",
          r.status_code == 200 and r.json()["data"]["transiciones_permitidas"] == ["EN_RUTA"])
    r = client.get(f"{API}/envios?limit=100", headers=H(tok["D1"]))
    check("  D1 lista solo su envio", [e["id_envio"] for e in r.json()["data"]] == [E1])
    expect("  D2 (no asignado) no puede poner EN_RUTA -> 403", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["D2"])), 403)
    expect("  D1 no puede cancelar -> 403", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "CANCELADO", "observacion": "no quiero"}, headers=H(tok["D1"])), 403)
    r = client.get(f"{API}/envios/repartidores", headers=H(tok["GS"]))
    rep = {x["id_usuario"]: x for x in r.json()["data"]}
    check("  D1 figura con 1 envio activo", rep[uid["D1"]]["envios_activos"] == 1 and rep[uid["D2"]]["envios_activos"] == 0)

    # --- 9. EN_RUTA y ENTREGADO --------------------------------------------------
    print("\n[9] EN_RUTA -> ENTREGADO (flujo feliz)")
    r = client.patch(f"{API}/envios/{E1}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["D1"]))
    expect("D1 marca EN_RUTA 200", r, 200)
    check("  estado EN_RUTA", r.json()["data"]["estado"] == "EN_RUTA")
    r = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CA"]))
    notis = r.json()["data"]
    check("  cliente notificado 'en camino'", any(n["referencia_tipo"] == "envio" and n["referencia_id"] == str(E1) and "en camino" in n["mensaje"] for n in notis))
    check("  notificacion tipo PEDIDO", any(n["tipo"] == "PEDIDO" and n["referencia_id"] == str(E1) for n in notis))
    r = client.patch(f"{API}/envios/{E1}/estado", json={"estado": "ENTREGADO", "observacion": "Recibido por el cliente"}, headers=H(tok["D1"]))
    expect("D1 marca ENTREGADO 200", r, 200)
    d = r.json()["data"]
    check("  ENTREGADO con fecha_entrega_real", d["estado"] == "ENTREGADO" and d["fecha_entrega_real"])
    check("  flujo cerrado: sin transiciones", d["transiciones_permitidas"] == [])
    notis = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CA"])).json()["data"]
    check("  cliente notificado 'entregado'", any(n["referencia_id"] == str(E1) and "entregado" in n["mensaje"] for n in notis))
    expect("  ENTREGADO es terminal: EN_RUTA -> 409", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["GS"])), 409)
    expect("  ENTREGADO no se cancela -> 409", client.patch(f"{API}/envios/{E1}/estado", json={"estado": "CANCELADO", "observacion": "tarde"}, headers=H(tok["GS"])), 409)
    expect("  ENTREGADO no se reprograma -> 409", client.patch(f"{API}/envios/{E1}/reprogramar", json={"nueva_fecha_entrega": futuro()}, headers=H(tok["GS"])), 409)
    h = client.get(f"{API}/envios/{E1}/historial", headers=H(tok["GS"])).json()["data"]
    esperado = [(None, "PREPARANDO"), ("PREPARANDO", "LISTO_ENVIO"), ("LISTO_ENVIO", "ASIGNADO"), ("ASIGNADO", "EN_RUTA"), ("EN_RUTA", "ENTREGADO")]
    check("  historial completo y en orden", [(x["estado_anterior"], x["estado_nuevo"]) for x in h] == esperado, f"-> {[(x['estado_anterior'], x['estado_nuevo']) for x in h]}")
    check("  historial registra al responsable", h[-1]["id_usuario"] == uid["D1"] and h[1]["id_usuario"] == uid["GS"] and h[-1]["usuario_nombre"])
    check("  historial conserva observaciones", h[-1]["observacion"] == "Recibido por el cliente")
    d = client.get(f"{API}/envios/{E1}", headers=H(tok["CA"])).json()["data"]
    check("  cliente ve su envio ENTREGADO", d["estado"] == "ENTREGADO")

    # --- 10. Excepcion: intento fallido y reprogramacion ------------------------
    print("\n[10] INTENTO_FALLIDO -> REPROGRAMADO (envio 2)")
    client.patch(f"{API}/envios/{E2}/confirmar-preparacion", json={"codigo_sucursal": 2}, headers=H(tok["GS"]))
    client.patch(f"{API}/envios/{E2}/asignar", json={"id_repartidor": uid["D1"]}, headers=H(tok["GS"]))
    client.patch(f"{API}/envios/{E2}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["D1"]))
    expect("EN_RUTA no se cancela directamente -> 409", client.patch(f"{API}/envios/{E2}/estado", json={"estado": "CANCELADO", "observacion": "x"}, headers=H(tok["GS"])), 409)
    expect("motivo muy corto -> 422", client.patch(f"{API}/envios/{E2}/intento-fallido", json={"motivo": "no"}, headers=H(tok["D1"])), 422)
    expect("sin motivo -> 422", client.patch(f"{API}/envios/{E2}/intento-fallido", json={}, headers=H(tok["D1"])), 422)
    expect("Cliente no registra intento fallido -> 403", client.patch(f"{API}/envios/{E2}/intento-fallido", json={"motivo": "Cliente no responde"}, headers=H(tok["CA"])), 403)
    r = client.patch(f"{API}/envios/{E2}/intento-fallido", json={"motivo": "Direccion no encontrada", "observacion": "Se llamo 3 veces"}, headers=H(tok["D1"]))
    expect("D1 registra intento fallido 200", r, 200)
    d = r.json()["data"]
    check("  INTENTO_FALLIDO, intentos=1, motivo guardado", d["estado"] == "INTENTO_FALLIDO" and d["intentos_fallidos"] == 1 and d["motivo_fallo"] == "Direccion no encontrada")
    check("  NO es terminal: D1 puede REPROGRAMAR (no cancelar)", d["transiciones_permitidas"] == ["REPROGRAMADO"])
    r = client.get(f"{API}/envios/{E2}", headers=H(tok["GS"]))
    check("  NO es terminal: GS puede REPROGRAMAR o CANCELAR", r.json()["data"]["transiciones_permitidas"] == ["REPROGRAMADO", "CANCELADO"])
    expect("  desde INTENTO_FALLIDO no se entrega directo -> 409", client.patch(f"{API}/envios/{E2}/estado", json={"estado": "ENTREGADO"}, headers=H(tok["D1"])), 409)
    expect("  ni se vuelve a EN_RUTA sin reprogramar -> 409", client.patch(f"{API}/envios/{E2}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["D1"])), 409)
    expect("  reprogramar con fecha pasada -> 400", client.patch(f"{API}/envios/{E2}/reprogramar", json={"nueva_fecha_entrega": futuro(-1)}, headers=H(tok["D1"])), 400)
    expect("  reprogramar sin fecha -> 422", client.patch(f"{API}/envios/{E2}/reprogramar", json={}, headers=H(tok["D1"])), 422)
    expect("  Cliente no reprograma -> 403", client.patch(f"{API}/envios/{E2}/reprogramar", json={"nueva_fecha_entrega": futuro()}, headers=H(tok["CA"])), 403)
    nueva = futuro(2)
    r = client.patch(f"{API}/envios/{E2}/reprogramar", json={"nueva_fecha_entrega": nueva, "observacion": "Cliente pidio manana"}, headers=H(tok["D1"]))
    expect("  D1 reprograma 200", r, 200)
    d = r.json()["data"]
    check("  REPROGRAMADO con nueva fecha y fecha_reprogramacion", d["estado"] == "REPROGRAMADO" and d["fecha_reprogramacion"] and d["fecha_estimada_entrega"])
    check("  el intento anterior NO se pierde (motivo_fallo e intentos)", d["motivo_fallo"] == "Direccion no encontrada" and d["intentos_fallidos"] == 1)
    notis = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CA"])).json()["data"]
    check("  cliente notificado de la reprogramacion", any(n["referencia_id"] == str(E2) and "reprogramada" in n["mensaje"] for n in notis))
    expect("  continua el flujo: EN_RUTA otra vez 200", client.patch(f"{API}/envios/{E2}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["D1"])), 200)
    r = client.patch(f"{API}/envios/{E2}/intento-fallido", json={"motivo": "Cliente no responde"}, headers=H(tok["D1"]))
    check("  segundo intento fallido: intentos=2", r.status_code == 200 and r.json()["data"]["intentos_fallidos"] == 2)
    client.patch(f"{API}/envios/{E2}/reprogramar", json={"nueva_fecha_entrega": futuro(3)}, headers=H(tok["GS"]))
    client.patch(f"{API}/envios/{E2}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["GS"]))
    r = client.patch(f"{API}/envios/{E2}/estado", json={"estado": "ENTREGADO"}, headers=H(tok["GS"]))
    expect("  tras reprogramar se entrega: ENTREGADO 200", r, 200)
    h = client.get(f"{API}/envios/{E2}/historial", headers=H(tok["GS"])).json()["data"]
    seq = [x["estado_nuevo"] for x in h]
    esperado = ["PREPARANDO", "LISTO_ENVIO", "ASIGNADO", "EN_RUTA", "INTENTO_FALLIDO", "REPROGRAMADO",
                "EN_RUTA", "INTENTO_FALLIDO", "REPROGRAMADO", "EN_RUTA", "ENTREGADO"]
    check("  historial conserva TODOS los intentos y reprogramaciones", seq == esperado, f"-> {seq}")
    check("  motivos de cada intento en el historial",
          "Direccion no encontrada" in (h[4]["observacion"] or "") and "Cliente no responde" in (h[7]["observacion"] or ""))

    # --- 11. CANCELADO vs INTENTO_FALLIDO ---------------------------------------
    print("\n[11] CANCELADO (terminal) vs INTENTO_FALLIDO (envio 3)")
    client.patch(f"{API}/envios/{E3}/confirmar-preparacion", json={"codigo_sucursal": 3}, headers=H(tok["GS"]))
    r = client.patch(f"{API}/envios/{E3}/estado", json={"estado": "CANCELADO", "observacion": "El cliente desistio de la compra"}, headers=H(tok["GS"]))
    expect("GS cancela envio LISTO_ENVIO 200", r, 200)
    d = r.json()["data"]
    check("  CANCELADO sin transiciones (cierra el flujo)", d["estado"] == "CANCELADO" and d["transiciones_permitidas"] == [])
    expect("  CANCELADO no se reprograma -> 409", client.patch(f"{API}/envios/{E3}/reprogramar", json={"nueva_fecha_entrega": futuro()}, headers=H(tok["GS"])), 409)
    expect("  CANCELADO no vuelve a EN_RUTA -> 409", client.patch(f"{API}/envios/{E3}/estado", json={"estado": "EN_RUTA"}, headers=H(tok["GS"])), 409)
    expect("  CANCELADO no se asigna -> 409", client.patch(f"{API}/envios/{E3}/asignar", json={"id_repartidor": uid["D1"]}, headers=H(tok["GS"])), 409)
    notis = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CB"])).json()["data"]
    check("  cliente B notificado de la cancelacion (WARNING)", any(n["referencia_id"] == str(E3) and n["tipo"] == "WARNING" for n in notis))
    h = client.get(f"{API}/envios/{E3}/historial", headers=H(tok["GS"])).json()["data"]
    check("  historial: cancelacion con motivo", h[-1]["estado_nuevo"] == "CANCELADO" and "desistio" in h[-1]["observacion"])
    check("  contraste: INTENTO_FALLIDO si permitio continuar (envio 2 llego a ENTREGADO)",
          client.get(f"{API}/envios/{E2}", headers=H(tok["GS"])).json()["data"]["estado"] == "ENTREGADO")

    # --- 12. Sucursal ------------------------------------------------------------
    print("\n[12] Sucursal responsable")
    r = client.get(f"{API}/envios?codigo_sucursal=1&limit=100", headers=H(tok["GS"]))
    ids = [e["id_envio"] for e in r.json()["data"]]
    check("filtro por sucursal 1 incluye E1 y excluye E2/E3", E1 in ids and E2 not in ids and E3 not in ids)
    r = client.get(f"{API}/envios?codigo_sucursal=2&limit=100", headers=H(tok["GS"]))
    check("filtro por sucursal 2 incluye E2", E2 in [e["id_envio"] for e in r.json()["data"]])
    d = client.get(f"{API}/envios/{E3}", headers=H(tok["GS"])).json()["data"]
    check("cada envio conserva su sucursal (E3 -> 3)", d["codigo_sucursal"] == 3 and d["sucursal_nombre"])
    db.expire_all()
    fila = db.execute(text("SELECT codigo_sucursal FROM envios WHERE id_envio = :i"), {"i": E1}).scalar()
    check("persistido en DB: envios.codigo_sucursal", fila == 1)
    # CHECK de DB: no se puede despachar sin sucursal (defensa en profundidad)
    try:
        db.execute(text("UPDATE envios SET estado='ASIGNADO', codigo_sucursal=NULL WHERE id_envio=:i"), {"i": E1})
        db.commit()
        check("CHECK sucursal_requerida_al_despachar bloquea ASIGNADO sin sucursal", False, "-> la DB lo permitio")
    except Exception as exc:  # IntegrityError esperado
        db.rollback()
        check("CHECK sucursal_requerida_al_despachar bloquea ASIGNADO sin sucursal", "sucursal_requerida" in str(exc))

    # --- 12b. Notificaciones (CU10): una por cambio de estado, sin duplicados ------
    print("\n[12b] Notificaciones sin duplicados")
    nA = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CA"])).json()["data"]
    nB = client.get(f"{API}/notificaciones?limit=100", headers=H(tok["CB"])).json()["data"]

    def contar(lista, envio, frase):
        return sum(1 for n in lista if n["referencia_tipo"] == "envio"
                   and n["referencia_id"] == str(envio) and frase in n["mensaje"])

    check("E1: 1 'en camino' y 1 'entregado'", contar(nA, E1, "en camino") == 1 and contar(nA, E1, "entregado") == 1)
    check("E1: total exacto = 2", sum(1 for n in nA if n["referencia_id"] == str(E1)) == 2)
    check("E2: 3 'en camino' (una por cada salida a ruta)", contar(nA, E2, "en camino") == 3)
    check("E2: 2 'No pudimos completar la entrega' (una por intento fallido)", contar(nA, E2, "No pudimos completar la entrega") == 2)
    check("E2: 2 'reprogramada' (una por reprogramacion)", contar(nA, E2, "reprogramada") == 2)
    check("E2: 1 'entregado'", contar(nA, E2, "entregado") == 1)
    check("E2: total exacto = 8 (sin duplicados)", sum(1 for n in nA if n["referencia_id"] == str(E2)) == 8)
    fallidas = [n for n in nA if n["referencia_id"] == str(E2) and "No pudimos" in n["mensaje"]]
    check("intento fallido: tipo WARNING y sin exponer el motivo interno",
          fallidas and all(n["tipo"] == "WARNING" and "Direccion no encontrada" not in n["mensaje"] for n in fallidas))
    check("E3 (cliente B): exactamente 1 (cancelacion)", sum(1 for n in nB if n["referencia_id"] == str(E3)) == 1)
    check("acciones sin cambio de estado (rechazos 409/403) no notifican",
          sum(1 for n in nA if n["referencia_tipo"] == "envio") == 2 + 8)

    # --- 13. Contrato de respuesta -------------------------------------------------
    print("\n[13] Contrato de respuesta")
    d = client.get(f"{API}/envios/{E1}", headers=H(tok["GS"])).json()
    check("envelope {status,data,message}", set(["status", "data", "message"]) <= set(d))
    campos = {"id_envio", "id_venta", "codigo_venta", "estado", "transiciones_permitidas", "cliente_id",
              "cliente_nombre", "total_venta", "datos_entrega", "items", "codigo_sucursal", "sucursal_nombre",
              "repartidor_id", "repartidor_nombre", "fecha_estimada_entrega", "fecha_entrega_real",
              "motivo_fallo", "fecha_reprogramacion", "intentos_fallidos", "fecha_creacion", "fecha_actualizacion"}
    check("EnvioResponse trae todos los campos", campos <= set(d["data"]), f"-> faltan {campos - set(d['data'])}")
    check("repartidor_nombre resuelto", d["data"]["repartidor_nombre"] == "SmokeD1 CU18")

finally:
    # --- Limpieza: SIEMPRE, aunque falle una prueba ------------------------------
    print("\n[LIMPIEZA]")
    try:
        db.rollback()
        limpiar(db, ids_temporales)
        db.execute(text("UPDATE productos SET stock_total=:s, estado=:e WHERE id_producto=:p"),
                   {"s": prod0[0], "e": prod0[1], "p": ID_PRODUCTO})
        db.commit()
        final = conteos(db)
        prod1 = db.execute(text("SELECT stock_total, estado FROM productos WHERE id_producto=:p"), {"p": ID_PRODUCTO}).one()
        check("conteos identicos a la linea base", final == base, f"-> antes={base} despues={final}")
        check("stock/estado del producto restaurados", tuple(prod1) == tuple(prod0), f"-> {tuple(prod0)} vs {tuple(prod1)}")
        left = db.execute(text("SELECT count(*) FROM usuarios WHERE correo LIKE 'smoke18\\_%@" + DOMINIO + "'")).scalar()
        check("no quedan usuarios temporales", left == 0)
        print(f"  Estado final: {final}")
    finally:
        db.close()

print("\n" + "=" * 60)
print(f"RESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
