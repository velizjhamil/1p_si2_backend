# Integracion CU19 fase 9 - CONCURRENCIA de la asignacion de agencia sobre la BD
# LOCAL de pruebas (jamas Supabase ni Render).
#
# Usa varias sesiones/conexiones INDEPENDIENTES que SI confirman (commit), por eso
# no puede correr dentro de la transaccion con rollback de las demas suites: crea
# sus propios datos (agencias, envios, ventas, usuarios) con marcas unicas y los
# BORRA siempre en tearDown, aunque el test falle.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_asignacion_agencia_concurrencia.py
#
# Invariantes verificadas bajo carrera:
#   - un envio se asigna UNA sola vez (la segunda asignacion recibe 409);
#   - agencia y repartidor nunca quedan a la vez;
#   - el snapshot guardado (tarifa/costo) corresponde a la agencia ganadora;
#   - ninguna excepcion sin traducir (nunca un 500).
import sys
import threading
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support import local_db  # noqa: E402

local_db.configure()  # ANTES de importar `app`

import app.main  # noqa: F401,E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.modules.delivery import (  # noqa: E402
    agencias_service,
    asignacion_agencia_service,
    service as cu18,
    tarifas_service,
    zonas_service,
)
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia import AgenciaCreate  # noqa: E402
from app.schemas.agencia_tarifa import TarifaCreate  # noqa: E402
from app.schemas.agencia_zona import ZonaCreate  # noqa: E402
from app.schemas.envio import AsignarEnvioPayload  # noqa: E402
from tests.support.agencias_api import bd_lista  # noqa: E402

GS = Usuario(id_usuario=uuid.uuid4(), nombre="GS", apellido="T", correo="gs@andes-express.com",
             estado=True, rol=Rol(nombre_rol="GS"))


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ConcurrenciaAsignacion(unittest.TestCase):
    def setUp(self):
        self.agencias: list[int] = []
        self.envios: list[int] = []
        self.ventas: list[int] = []
        self.usuarios: list[uuid.UUID] = []
        self.marca = uuid.uuid4().hex[:8]
        self.n = 0
        with engine.connect() as c:
            self.suc = c.execute(text("select codigo_sucursal from sucursales limit 1")).scalar()
            self.rol_c = c.execute(text("select id_rol from roles where nombre_rol='C'")).scalar()
            self.rol_d = c.execute(text("select id_rol from roles where nombre_rol='D'")).scalar()
            rol_gs = c.execute(text("select id_rol from roles where nombre_rol='GS'")).scalar()
        # El GS que asigna debe EXISTIR en la BD: la fila de historial referencia usuarios(id_usuario).
        with engine.begin() as c:
            c.execute(text("insert into usuarios (id_usuario,nombre,correo,password,estado,id_rol,intentos_fallidos) values (:u,'GS',:c,'x',true,:r,0)"),
                      {"u": GS.id_usuario, "c": f"{self.marca}.gs@andes-express.com", "r": rol_gs})
        self.usuarios.append(GS.id_usuario)

    def tearDown(self):
        with engine.begin() as c:                       # orden: envios -> ventas -> agencias -> usuarios
            for e in self.envios:
                c.execute(text("delete from envios where id_envio=:e"), {"e": e})
            for v in self.ventas:
                c.execute(text("delete from ventas where id_venta=:v"), {"v": v})
            for a in self.agencias:
                c.execute(text("delete from agencias_reparto where id_agencia=:a"), {"a": a})   # CASCADE zonas y tarifas
            for u in self.usuarios:
                c.execute(text("delete from usuarios where id_usuario=:u"), {"u": u})

    # -- fixtures COMMITEADOS -----------------------------------------------------------
    def agencia(self, costo="10", tipo="PESO") -> tuple[int, int]:
        """Agencia habilitada con una zona en La Paz y una tarifa. Devuelve (id_agencia, id_tarifa)."""
        self.n += 1
        db = Session(engine)
        try:
            a = agencias_service.crear_agencia(db, GS, AgenciaCreate(
                razon_social=f"CONC9 {self.marca} {self.n}", nit=str(int(self.marca, 16))[:10] + f"{self.n:02d}",
                correo_facturacion="facturas@andes-express.com", direccion_fiscal="Av. Concurrencia 9"))
            self.agencias.append(a.id_agencia)
            z = zonas_service.crear_zona(db, GS, a.id_agencia, ZonaCreate(id_ciudad=2)).id_zona
            t = tarifas_service.crear_tarifa(db, GS, a.id_agencia, z, TarifaCreate(
                criterio=tipo, rango_min="0", rango_max=None, costo=costo, vigente_desde="2020-01-01")).id_tarifa
            return a.id_agencia, t
        finally:
            db.close()

    def usuario(self, rol_id) -> uuid.UUID:
        u = uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("insert into usuarios (id_usuario,nombre,correo,password,estado,id_rol,intentos_fallidos) values (:u,'T',:c,'x',true,:r,0)"),
                      {"u": u, "c": f"{self.marca}.{u}@andes-express.com", "r": rol_id})
        self.usuarios.append(u)
        return u

    def envio(self) -> int:
        self.n += 1
        cliente = self.usuario(self.rol_c)
        with engine.begin() as c:
            v = c.execute(text(
                "insert into ventas (id_cliente,total,costo_envio,metodo_pago,estado_pago,codigo,tipo_entrega,nombre_cliente,correo,telefono,direccion,ciudad) "
                "values (:u,100,25,'QR','PAGADO',:k,'DOMICILIO','C','c@andes-express.com','1','d','La Paz') returning id_venta"),
                {"u": cliente, "k": f"C9{self.marca}{self.n:03d}"}).scalar()
            self.ventas.append(v)
            e = c.execute(text("insert into envios (id_venta,estado,codigo_sucursal) values (:v,'LISTO_ENVIO',:s) returning id_envio"),
                          {"v": v, "s": self.suc}).scalar()
            self.envios.append(e)
        return e

    def estado_envio(self, e):
        with engine.connect() as c:
            return c.execute(text("select estado, id_agencia, id_tarifa_aplicada, costo_agencia, id_repartidor, "
                                  "(select count(*) from envio_historial h where h.id_envio=envios.id_envio) from envios where id_envio=:e"),
                             {"e": e}).one()

    # -- carrera ---------------------------------------------------------------------------
    def en_paralelo(self, *operaciones):
        """Cada operacion(db) en su hilo y sesion, largadas a la vez. Resultados: ('ok', v) | ('http', codigo) | ('error', repr)."""
        barrera = threading.Barrier(len(operaciones))
        resultados: list = [None] * len(operaciones)

        def trabajo(i, op):
            db = Session(engine)
            try:
                barrera.wait(timeout=10)
                resultados[i] = ("ok", op(db))
            except HTTPException as exc:
                resultados[i] = ("http", exc.status_code)
            except Exception as exc:
                resultados[i] = ("error", repr(exc))
            finally:
                db.close()

        hilos = [threading.Thread(target=trabajo, args=(i, op)) for i, op in enumerate(operaciones)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=30)
            self.assertFalse(h.is_alive(), "un hilo quedo bloqueado (posible deadlock)")
        self.assertFalse([r for r in resultados if r[0] == "error"], resultados)      # nunca una excepcion sin traducir
        return resultados

    def asignar(self, e, ag):
        return lambda db: asignacion_agencia_service.asignar_agencia(
            db, GS, e, AsignarEnvioPayload(id_agencia=ag, peso_kg="2", volumen_m3="1")).id_agencia

    # -- pruebas ------------------------------------------------------------------------------
    def test_dos_agencias_distintas_al_mismo_envio_solo_una_gana(self):
        for ronda in range(5):
            (a1, t1), (a2, t2) = self.agencia("10"), self.agencia("99")
            e = self.envio()
            res = self.en_paralelo(self.asignar(e, a1), self.asignar(e, a2))
            self.assertEqual(sorted(r[0] for r in res), ["http", "ok"], (ronda, res))
            self.assertIn(("http", 409), res, (ronda, res))
            estado, id_ag, id_t, costo, rep, n_hist = self.estado_envio(e)
            ganadora = next(r[1] for r in res if r[0] == "ok")
            esperado = {a1: (t1, 10), a2: (t2, 99)}[ganadora]
            self.assertEqual((estado, id_ag, id_t, float(costo), rep, n_hist), ("ASIGNADO", ganadora, esperado[0], esperado[1], None, 1), ronda)

    def test_la_misma_agencia_varias_veces_al_mismo_envio_una_sola_asignacion(self):
        a, t = self.agencia("12")
        e = self.envio()
        res = self.en_paralelo(*[self.asignar(e, a) for _ in range(5)])
        self.assertEqual(sum(1 for r in res if r[0] == "ok"), 1, res)
        self.assertEqual(sum(1 for r in res if r == ("http", 409)), 4, res)
        estado, id_ag, id_t, costo, rep, n_hist = self.estado_envio(e)
        self.assertEqual((estado, id_ag, id_t, float(costo), rep, n_hist), ("ASIGNADO", a, t, 12.0, None, 1))   # un solo historial

    def test_agencia_contra_repartidor_nunca_quedan_los_dos(self):
        d = self.usuario(self.rol_d)
        a, _ = self.agencia("10")
        for ronda in range(5):
            e = self.envio()
            repartidor = lambda db, e=e: cu18.asignar_repartidor(db, GS, e, AsignarEnvioPayload(id_repartidor=d)).id_repartidor
            res = self.en_paralelo(self.asignar(e, a), repartidor)
            self.assertEqual(sorted(r[0] for r in res), ["http", "ok"], (ronda, res))
            self.assertIn(("http", 409), res, (ronda, res))
            estado, id_ag, id_t, costo, rep, n_hist = self.estado_envio(e)
            self.assertEqual(estado, "ASIGNADO")
            self.assertTrue((id_ag is not None) != (rep is not None), f"agencia y repartidor a la vez o ninguno: {(id_ag, rep)}")
            if rep is not None:                                                   # gano el repartidor: sin restos de agencia
                self.assertEqual((id_ag, id_t, costo), (None, None, None))
            with engine.connect() as c:
                self.assertEqual(c.execute(text("select count(*) from envios where id_envio=:e and id_agencia is not null and id_repartidor is not null"), {"e": e}).scalar(), 0)

    def test_asignaciones_de_distintos_envios_a_la_misma_agencia_no_se_bloquean_como_conflicto(self):
        a, t = self.agencia("15")
        envios = [self.envio() for _ in range(4)]
        res = self.en_paralelo(*[self.asignar(e, a) for e in envios])
        self.assertEqual([r[0] for r in res], ["ok"] * 4, res)
        for e in envios:
            self.assertEqual(tuple(self.estado_envio(e)[:3]), ("ASIGNADO", a, t))

    def test_asignar_contra_deshabilitar_la_agencia_nunca_500_y_resultado_coherente(self):
        for ronda in range(5):
            a, _ = self.agencia("10")
            e = self.envio()
            deshabilitar = lambda db, a=a: agencias_service.cambiar_estado(db, GS, a, False).is_active
            res = self.en_paralelo(self.asignar(e, a), deshabilitar)
            asig, desh = res
            self.assertEqual(desh[0], "ok", res)                                    # deshabilitar siempre puede
            estado, id_ag, *_ = self.estado_envio(e)
            if asig[0] == "ok":
                self.assertEqual((estado, id_ag), ("ASIGNADO", a), ronda)           # se asigno antes de deshabilitar: historico valido
            else:
                self.assertEqual(asig, ("http", 400), (ronda, res))                  # vio la agencia deshabilitada
                self.assertEqual((estado, id_ag), ("LISTO_ENVIO", None), ronda)      # y no dejo nada
            with engine.connect() as c:
                self.assertFalse(c.execute(text("select is_active from agencias_reparto where id_agencia=:a"), {"a": a}).scalar())

    def test_asignar_contra_editar_la_tarifa_el_snapshot_es_coherente(self):
        for ronda in range(4):
            a, t = self.agencia("10")
            e = self.envio()
            with engine.connect() as c:
                z = c.execute(text("select id_zona from agencia_tarifas where id_tarifa=:t"), {"t": t}).scalar()
            from app.schemas.agencia_tarifa import TarifaUpdate
            editar = lambda db, a=a, z=z, t=t: tarifas_service.actualizar_tarifa(db, GS, a, z, t, TarifaUpdate(costo="77")).costo
            res = self.en_paralelo(self.asignar(e, a), editar)
            self.assertEqual(res[1][0], "ok", res)
            self.assertEqual(res[0][0], "ok", res)
            estado, id_ag, id_t, costo, *_ = self.estado_envio(e)
            self.assertEqual((estado, id_ag, id_t), ("ASIGNADO", a, t))
            self.assertIn(float(costo), (10.0, 77.0), ronda)                        # o el costo previo o el nuevo, jamas otro


if __name__ == "__main__":
    unittest.main()
