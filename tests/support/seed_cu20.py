# Seed DETERMINISTA de la BD LOCAL de pruebas para CU20 (reportes).
#
# Solo corre contra la BD local `_test` (candados de local_db) y solo si esta
# vacia de ventas. Lo invoca tests/support/setup_test_db.py; no se usa nunca
# contra Supabase. Datos pequenos, con fechas absolutas (no dependen de "hoy")
# y pensados para razonar los totales a mano (ver tests/integration/
# test_reportes_local_datos.py, que los verifica).
#
# ---------------------------------------------------------------- CATALOGO
#  (se borra antes el catalogo demo que insertan las migraciones CU9/CU6)
#  Categorias: Camisas, Pantalones, Vestidos y Accesorios (sin productos)
#  Producto            Cat         Precio  Stock  Nivel     Notas
#  P1 Camisa Oxford    Camisas     100     50     OK        mas vendido
#  P2 Camisa Lino      Camisas      80     12     BAJO
#  P3 Pantalon Chino   Pantalones  120      3     CRITICO
#  P4 Jean Slim        Pantalones  150      0     CRITICO   AGOTADO (estado Agotado) con ventas
#  P5 Vestido Floral   Vestidos    200     20     OK        solo ventas NO pagadas -> sin ventas
#  P6 Vestido Midi     Vestidos    250      5     BAJO      limite: 5 no es < 5
#  P7 Camisa Rayas     Camisas      60      4     CRITICO   limite: 4 < 5
#  P8 Chaqueta Inactiva Pantalones 300      9     -         estado Inactivo (fuera del inventario)
#  P9 Vestido Gala     Vestidos     90     15     OK        limite: 15 no es < 15; sin ventas
#
# ------------------------------------------------------------------ VENTAS
#  (UTC)                  canal   pago      estado     lineas                  subtotal envio total
#  V1  2025-12-30 10:00   ONLINE  QR        PAGADO     P1x2, P2x1                 280     15    295
#  V2  2025-12-31 23:59:59 POS    EFECTIVO  PAGADO     P3x1                       120      0    120
#  V3  2026-01-01 00:00:00 POS    TARJETA   PAGADO     P1x1, P4x2                 400      0    400
#  V4  2026-01-01 02:00   ONLINE  TARJETA   PAGADO     P6x1                       250     15    265
#  V5  2026-01-15 12:00   ONLINE  QR        PAGADO     P1x3, P7x2                 420     15    435
#  V6  2026-02-28 18:00   POS     QR        PAGADO     P2x2, P4x1                 310      0    310
#  V7  2026-03-01 00:00   ONLINE  EFECTIVO  PAGADO     P1x1                       100     15    115
#  V8  2026-03-10 09:00   ONLINE  QR        PENDIENTE  P5x1  (no cuenta)          200     15    215
#  V9  2026-03-10 09:30   ONLINE  QR        RECHAZADO  P5x1  (no cuenta)          200     15    215
#  V10 2026-03-15 15:00   POS     EFECTIVO  PAGADO     P1x2, P3x1                 320      0    320
#  Los limites 23:59:59 / 00:00:00 / 02:00 prueban el corte de dia en UTC.
#
# ------------------------------------------------------------ DEVOLUCIONES
#  (fecha_solicitud UTC)  estado      venta(canal)  lineas
#  D0 2025-12-31 20:00    COMPLETADA  V1 (ONLINE)   P2x1 (80)
#  D1 2026-01-05 10:00    COMPLETADA  V1 (ONLINE)   P1x1 (100)
#  D2 2026-02-03 10:00    COMPLETADA  V3 (POS)      P4x1 (150) + P1x1 (100)  <- 2 lineas, 2 categorias
#  D3 2026-02-10 10:00    RECHAZADA   V5 (ONLINE)   P1x1 (100)
#  D4 2026-02-20 10:00    APROBADA    V6 (POS)      P2x1 (80)
#  D5 2026-03-02 10:00    SOLICITADA  V7 (ONLINE)   P1x1 (100)
#  D6 2026-03-12 10:00    COMPLETADA  V10 (POS)     P3x1 (120)
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # DATABASE_URL -> BD local de pruebas (o inservible), antes de importar app

import app.main  # noqa: E402,F401  (registra mappers)
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import obtener_hash_password  # noqa: E402
from app.modules.devoluciones.models import DetalleDevolucion, Devolucion  # noqa: E402
from app.modules.inventario.models import Categoria, Producto  # noqa: E402
from app.modules.usuarios import seed as seed_usuarios  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.modules.ventas.models import DetalleVenta, Venta  # noqa: E402

NS = uuid.UUID("6f1c2d3e-0000-4000-8000-00000000c020")  # namespace de UUIDs deterministas
PASSWORD_DEMO = "Test1234!"  # solo BD de pruebas


def _u(nombre: str) -> uuid.UUID:
    return uuid.uuid5(NS, nombre)


def _t(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


ROLES = {
    "ASU": "Administrador del Sistema", "GS": "Gerente de Sucursal", "V": "Vendedor",
    "C": "Cliente", "D": "Encargado de Delivery",
}
# (clave, nombre, apellido, rol)
USUARIOS = [
    ("asu", "Ana", "Admin", "ASU"), ("gs", "Gael", "Gerente", "GS"),
    ("v", "Vera", "Vendedora", "V"), ("d", "Dario", "Delivery", "D"),
    ("c1", "Carla", "Cliente", "C"), ("c2", "Camilo", "Cliente", "C"),
]
CATEGORIAS = [  # (nombre, linea)
    ("Camisas", "Hombre"), ("Pantalones", "Hombre"), ("Vestidos", "Mujer"), ("Accesorios", "Unisex"),
]
PRODUCTOS = [  # (clave, nombre, categoria, precio, stock, estado)
    ("P1", "Camisa Oxford", "Camisas", 100, 50, "Activo"),
    ("P2", "Camisa Lino", "Camisas", 80, 12, "Activo"),
    ("P3", "Pantalon Chino", "Pantalones", 120, 3, "Activo"),
    ("P4", "Jean Slim", "Pantalones", 150, 0, "Agotado"),
    ("P5", "Vestido Floral", "Vestidos", 200, 20, "Activo"),
    ("P6", "Vestido Midi", "Vestidos", 250, 5, "Activo"),
    ("P7", "Camisa Rayas", "Camisas", 60, 4, "Activo"),
    ("P8", "Chaqueta Inactiva", "Pantalones", 300, 9, "Inactivo"),
    ("P9", "Vestido Gala", "Vestidos", 90, 15, "Activo"),
]
# (clave, fecha UTC, canal, metodo, estado, envio, cliente, [(prod, cant), ...])
VENTAS = [
    ("V1", "2025-12-30T10:00:00", "ONLINE", "QR", "PAGADO", 15, "c1", [("P1", 2), ("P2", 1)]),
    ("V2", "2025-12-31T23:59:59", "POS", "EFECTIVO", "PAGADO", 0, "c2", [("P3", 1)]),
    ("V3", "2026-01-01T00:00:00", "POS", "TARJETA", "PAGADO", 0, "c1", [("P1", 1), ("P4", 2)]),
    ("V4", "2026-01-01T02:00:00", "ONLINE", "TARJETA", "PAGADO", 15, "c2", [("P6", 1)]),
    ("V5", "2026-01-15T12:00:00", "ONLINE", "QR", "PAGADO", 15, "c1", [("P1", 3), ("P7", 2)]),
    ("V6", "2026-02-28T18:00:00", "POS", "QR", "PAGADO", 0, "c2", [("P2", 2), ("P4", 1)]),
    ("V7", "2026-03-01T00:00:00", "ONLINE", "EFECTIVO", "PAGADO", 15, "c1", [("P1", 1)]),
    ("V8", "2026-03-10T09:00:00", "ONLINE", "QR", "PENDIENTE", 15, "c2", [("P5", 1)]),
    ("V9", "2026-03-10T09:30:00", "ONLINE", "QR", "RECHAZADO", 15, "c1", [("P5", 1)]),
    ("V10", "2026-03-15T15:00:00", "POS", "EFECTIVO", "PAGADO", 0, "c2", [("P1", 2), ("P3", 1)]),
]
# (clave, fecha UTC, estado, venta, motivo, [(prod, cant), ...])
DEVOLUCIONES = [
    ("D0", "2025-12-31T20:00:00", "COMPLETADA", "V1", "Talla incorrecta", [("P2", 1)]),
    ("D1", "2026-01-05T10:00:00", "COMPLETADA", "V1", "Defecto de fabrica", [("P1", 1)]),
    ("D2", "2026-02-03T10:00:00", "COMPLETADA", "V3", "No coincide con la foto", [("P4", 1), ("P1", 1)]),
    ("D3", "2026-02-10T10:00:00", "RECHAZADA", "V5", "Cambie de opinion", [("P1", 1)]),
    ("D4", "2026-02-20T10:00:00", "APROBADA", "V6", "Color distinto", [("P2", 1)]),
    ("D5", "2026-03-02T10:00:00", "SOLICITADA", "V7", "Llego tarde", [("P1", 1)]),
    ("D6", "2026-03-12T10:00:00", "COMPLETADA", "V10", "Costura rota", [("P3", 1)]),
]


def main() -> int:
    if not local_db.es_bd_de_pruebas_local():
        print("ABORTADO: la BD no es la BD local de pruebas (_test en localhost).", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        if db.query(Venta).count():
            print("ABORTADO: la BD ya tiene ventas; reconstruya con setup_test_db.py --recreate.", file=sys.stderr)
            return 3

        # --- limpieza del catalogo sembrado por las MIGRACIONES ------------------
        # Las migraciones CU9/CU6 insertan 8 categorias y 10 productos demo. Para
        # que los totales de los reportes sean deterministas se parte de un
        # catalogo vacio (solo BD de pruebas; los pivotes caen por ON DELETE CASCADE).
        db.execute(text("DELETE FROM productos"))
        db.execute(text("DELETE FROM categorias"))
        db.commit()

        # --- roles + permisos (reutiliza el seed del modulo usuarios) -----------
        roles = {n: Rol(id_rol=_u(f"rol-{n}"), nombre_rol=n) for n in ROLES}
        db.add_all(roles.values())
        db.commit()
        seed_usuarios.run()  # catalogo de permisos, descripciones y matriz rol_permiso

        # --- usuarios ---------------------------------------------------------------
        hash_ = obtener_hash_password(PASSWORD_DEMO)
        users = {}
        for clave, nombre, apellido, rol in USUARIOS:
            users[clave] = Usuario(
                id_usuario=_u(f"usuario-{clave}"), nombre=nombre, apellido=apellido,
                correo=f"{clave}@cu20.example.com", password=hash_, estado=True,
                id_rol=roles[rol].id_rol, intentos_fallidos=0,
            )
        db.add_all(users.values())

        # --- catalogo ------------------------------------------------------------------
        cats = {n: Categoria(nombre=n, linea=l, descripcion=f"Seed CU20 {n}", activo=True) for n, l in CATEGORIAS}
        db.add_all(cats.values())
        db.flush()
        prods = {}
        for clave, nombre, cat, precio, stock, estado in PRODUCTOS:
            prods[clave] = Producto(
                nombre=nombre, id_categoria=cats[cat].id_categoria, precio_venta=precio,
                stock_total=stock, estado=estado, descripcion=f"Seed CU20 {clave}",
            )
        db.add_all(prods.values())
        db.flush()

        # --- ventas ------------------------------------------------------------------------
        ventas, detalles = {}, {}  # detalles[(venta, prod)] -> DetalleVenta
        for clave, fecha, canal, metodo, estado, envio, cli, lineas in VENTAS:
            cliente = users[cli]
            subtotal = sum(prods[p].precio_venta * c for p, c in lineas)
            v = Venta(
                id_cliente=cliente.id_usuario,
                id_vendedor=users["v"].id_usuario if canal == "POS" else None,
                fecha_venta=_t(fecha), total=subtotal + envio, costo_envio=envio,
                metodo_pago=metodo, estado_pago=estado, codigo=f"ATT-T{clave[1:]:0>5}",
                tipo_entrega="RETIRO" if canal == "POS" else "DOMICILIO",
                nombre_cliente=f"{cliente.nombre} {cliente.apellido}", correo=cliente.correo,
                telefono="70000000", direccion="Av. Prueba 123", ciudad="Santa Cruz",
            )
            for p, c in lineas:
                d = DetalleVenta(
                    id_producto=prods[p].id_producto, cantidad=c,
                    precio_unitario=prods[p].precio_venta, subtotal=prods[p].precio_venta * c,
                )
                v.detalles.append(d)
                detalles[(clave, p)] = d
            ventas[clave] = v
            db.add(v)
        db.flush()

        # --- devoluciones -----------------------------------------------------------------
        for clave, fecha, estado, venta, motivo, lineas in DEVOLUCIONES:
            v = ventas[venta]
            monto = sum(prods[p].precio_venta * c for p, c in lineas)
            dev = Devolucion(
                id_venta=v.id_venta, id_cliente=v.id_cliente, id_solicitante=v.id_cliente,
                id_procesador=None if estado == "SOLICITADA" else users["gs"].id_usuario,
                fecha_solicitud=_t(fecha),
                fecha_procesado=None if estado == "SOLICITADA" else _t(fecha),
                estado=estado, motivo=motivo,
                motivo_rechazo="No aplica la politica" if estado == "RECHAZADA" else None,
                monto_total_devuelto=monto if estado == "COMPLETADA" else 0,
            )
            for p, c in lineas:
                dv = detalles[(venta, p)]
                dev.detalles.append(DetalleDevolucion(
                    id_detalle_venta=dv.id_detalle, id_producto=prods[p].id_producto,
                    cantidad_devuelta=c, precio_unitario=prods[p].precio_venta,
                    subtotal=prods[p].precio_venta * c,
                ))
            db.add(dev)

        db.commit()
        print(f"Seed CU20: {len(USUARIOS)} usuarios, {len(CATEGORIAS)} categorias, {len(PRODUCTOS)} productos, "
              f"{len(VENTAS)} ventas, {len(DEVOLUCIONES)} devoluciones.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
