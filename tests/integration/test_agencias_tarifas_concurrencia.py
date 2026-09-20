# Integracion CU19 fase 6 - CONCURRENCIA de las tarifas sobre la BD LOCAL de
# pruebas (jamas Supabase ni Render).
#
# El solapamiento de tarifas se valida en el servicio (no hay EXCLUDE ni
# btree_gist en la DB), asi que la unica defensa contra dos altas simultaneas es
# el bloqueo FOR UPDATE de la agencia y de la zona. Estas pruebas usan DOS
# sesiones/conexiones independientes que SI confirman (commit), por eso NO
# pueden correr dentro de la transaccion con rollback de las demas suites:
# crean su propia agencia con datos unicos y la BORRAN al terminar (la FK
# CASCADE arrastra zonas y tarifas), incluso si el test falla.
#   $env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
#   python tests/integration/test_agencias_tarifas_concurrencia.py
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
from app.modules.delivery import agencias_service, tarifas_service, zonas_service  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia import AgenciaCreate  # noqa: E402
from app.schemas.agencia_tarifa import TarifaCreate, TarifaUpdate  # noqa: E402
from app.schemas.agencia_zona import ZonaCreate  # noqa: E402
from tests.support.agencias_api import bd_lista  # noqa: E402

GS = Usuario(id_usuario=uuid.uuid4(), nombre="GS", apellido="T", correo="gs@andes-express.com",
             estado=True, rol=Rol(nombre_rol="GS"))


def tarifa(**extra):
    base = dict(criterio="PESO", rango_min="0", rango_max="5", costo="10", vigente_desde="2026-01-01")
    base.update(extra)
    return TarifaCreate(**base)


@unittest.skipUnless(bd_lista(), "BD local con la migracion CU19 no disponible (ver cabecera)")
class ConcurrenciaTarifas(unittest.TestCase):
    def setUp(self):
        self.ids_agencia: list[int] = []
        db = Session(engine)
        try:
            marca = uuid.uuid4().hex[:10]
            a = agencias_service.crear_agencia(db, GS, AgenciaCreate(
                razon_social=f"CONC {marca}", nit=str(int(marca, 16))[:12],
                correo_facturacion="facturas@andes-express.com", direccion_fiscal="Av. Concurrencia 1"))
            self.ids_agencia.append(a.id_agencia)
            self.ag = a.id_agencia
            self.zona = zonas_service.crear_zona(db, GS, self.ag, ZonaCreate(id_ciudad=2)).id_zona
        finally:
            db.close()

    def tearDown(self):
        with engine.begin() as c:  # limpia lo COMMITEADO (CASCADE: zonas y tarifas)
            for i in self.ids_agencia:
                c.execute(text("delete from agencias_reparto where id_agencia=:i"), {"i": i})

    # -- utilidades ------------------------------------------------------------------
    def correr_en_paralelo(self, *operaciones):
        """Ejecuta cada operacion(db) en su propio hilo y sesion, largando todas a
        la vez. Devuelve la lista de resultados ('ok', valor) o ('http', status)."""
        barrera = threading.Barrier(len(operaciones))
        resultados: list = [None] * len(operaciones)

        def trabajo(i, op):
            db = Session(engine)
            try:
                barrera.wait(timeout=10)
                resultados[i] = ("ok", op(db))
            except HTTPException as exc:
                resultados[i] = ("http", exc.status_code)
            except Exception as exc:  # cualquier otra cosa es un defecto (nunca 500)
                resultados[i] = ("error", repr(exc))
            finally:
                db.close()

        hilos = [threading.Thread(target=trabajo, args=(i, op)) for i, op in enumerate(operaciones)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=30)
            self.assertFalse(h.is_alive(), "un hilo quedo bloqueado (posible deadlock)")
        return resultados

    def crear(self, **extra):
        return lambda db: tarifas_service.crear_tarifa(db, GS, self.ag, self.zona, tarifa(**extra)).id_tarifa

    def en_bd(self, sql, **p):
        with engine.connect() as c:
            return c.execute(text(sql), p).scalar()

    def activas(self):
        return self.en_bd("select count(*) from agencia_tarifas where id_zona=:z", z=self.zona)

    # -- pruebas ------------------------------------------------------------------------
    def test_dos_altas_solapadas_simultaneas_solo_una_gana(self):
        for ronda in range(5):  # varias rondas: una carrera no se prueba con un solo intento
            with engine.begin() as c:
                c.execute(text("delete from agencia_tarifas where id_zona=:z"), {"z": self.zona})
            res = self.correr_en_paralelo(self.crear(rango_min="0", rango_max="5"),
                                          self.crear(rango_min="3", rango_max="8"))
            self.assertEqual(sorted(r[0] for r in res), ["http", "ok"], (ronda, res))
            self.assertIn(("http", 409), res, (ronda, res))
            self.assertEqual(self.activas(), 1, ronda)

    def test_altas_identicas_simultaneas_una_sola_persiste(self):
        res = self.correr_en_paralelo(*[self.crear(rango_min="0", rango_max="5") for _ in range(4)])
        self.assertEqual(sum(1 for r in res if r[0] == "ok"), 1, res)
        self.assertEqual(sum(1 for r in res if r == ("http", 409)), 3, res)
        self.assertEqual(self.activas(), 1)

    def test_altas_no_solapadas_simultaneas_ganan_todas(self):
        res = self.correr_en_paralelo(self.crear(rango_min="0", rango_max="5"), self.crear(rango_min="5", rango_max="10"),
                                      self.crear(rango_min="10", rango_max=None),
                                      self.crear(criterio="VOLUMEN", rango_min="0", rango_max="5"))
        self.assertEqual([r[0] for r in res], ["ok"] * 4, res)     # tramos contiguos y criterio distinto: sin conflicto
        self.assertEqual(self.activas(), 4)

    def test_altas_en_zonas_distintas_no_se_bloquean_como_conflicto(self):
        db = Session(engine)
        try:
            z2 = zonas_service.crear_zona(db, GS, self.ag, ZonaCreate(id_ciudad=3)).id_zona
        finally:
            db.close()
        res = self.correr_en_paralelo(self.crear(), lambda db: tarifas_service.crear_tarifa(db, GS, self.ag, z2, tarifa()).id_tarifa)
        self.assertEqual([r[0] for r in res], ["ok", "ok"], res)

    def test_alta_contra_actualizacion_simultanea_que_se_solapan(self):
        db = Session(engine)
        try:
            tarifas_service.crear_tarifa(db, GS, self.ag, self.zona, tarifa(rango_min="0", rango_max="5"))
            otra = tarifas_service.crear_tarifa(db, GS, self.ag, self.zona, tarifa(rango_min="10", rango_max="15")).id_tarifa
        finally:
            db.close()
        # la actualizacion mueve `otra` a [7, 15); la alta pide [5, 10): se cruzan -> solo una prevalece
        res = self.correr_en_paralelo(
            lambda db: tarifas_service.actualizar_tarifa(db, GS, self.ag, self.zona, otra, TarifaUpdate(rango_min="7")).id_tarifa,
            self.crear(rango_min="5", rango_max="10"),
        )
        self.assertFalse([r for r in res if r[0] == "error"], res)          # nunca una excepcion no traducida
        self.assertTrue(all(r[0] == "ok" or r == ("http", 409) for r in res), res)
        with engine.connect() as c:                    # invariante: NINGUN par de tarifas activas queda solapado
            filas = c.execute(text("select rango_min, rango_max from agencia_tarifas where id_zona=:z and criterio='PESO' order by rango_min"),
                              {"z": self.zona}).all()
        for (a_min, a_max), (b_min, b_max) in zip(filas, filas[1:]):
            self.assertTrue(a_max is not None and a_max <= b_min, f"solapadas: {filas}")

    def test_invariante_bajo_carga_ningun_par_solapado(self):
        """Muchas altas aleatorias en paralelo: al final ningun par de tarifas
        (mismo criterio) tiene rangos que se crucen."""
        import random

        rnd = random.Random(7)
        ops = []
        for _ in range(12):
            a = rnd.randint(0, 40)
            ops.append(self.crear(rango_min=str(a), rango_max=str(a + rnd.randint(1, 15))))
        res = self.correr_en_paralelo(*ops)
        self.assertFalse([r for r in res if r[0] == "error"], res)          # nunca una excepcion no traducida
        self.assertTrue(all(r[0] == "ok" or r == ("http", 409) for r in res), res)
        with engine.connect() as c:
            filas = c.execute(text("select rango_min, rango_max from agencia_tarifas where id_zona=:z order by rango_min"),
                              {"z": self.zona}).all()
        self.assertEqual(len(filas), sum(1 for r in res if r[0] == "ok"))
        for (a_min, a_max), (b_min, b_max) in zip(filas, filas[1:]):
            self.assertTrue(a_max <= b_min, f"solapadas: {filas}")


if __name__ == "__main__":
    unittest.main()
