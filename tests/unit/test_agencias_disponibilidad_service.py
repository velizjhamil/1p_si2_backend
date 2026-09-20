# Pruebas unitarias AISLADAS de CU19 fase 7 (disponibilidad_service).
# Sin base de datos: la sesion es un Mock (o una Session sin conexion solo para
# compilar SQL). El comportamiento real (cobertura, orden, JWT, N+1) se prueba en
# tests/integration/test_agencias_disponibles_*.py contra la BD local.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_disponibilidad_service.py
import sys
import unittest
import uuid
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.orm import Query, Session  # noqa: E402

from app.modules.delivery import disponibilidad_service as svc  # noqa: E402
from app.modules.delivery import zonas_service  # noqa: E402
from app.modules.empresa.models import Ciudad  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402

Fila = namedtuple("Fila", "id_agencia razon_social contacto_operativo telefono is_active zonas_en_ciudad cobertura_completa")


def usuario(rol):
    return Usuario(id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def ciudad(i=2, nombre="La Paz", depto="La Paz"):
    return Ciudad(id=i, nombre=nombre, departamento=depto)


def db_con(ciudades):
    """Mock de sesion con el catalogo `ciudades` (lo que consulta zonas_service)."""
    db = mock.Mock()
    db.query.return_value.all.return_value = [(c.id, c.nombre) for c in ciudades]
    db.get.side_effect = lambda _m, i: next((c for c in ciudades if c.id == i), None)
    return db


class ReutilizaLaResolucionDeFase5(unittest.TestCase):
    def test_usa_las_mismas_funciones_sin_duplicarlas(self):
        self.assertIs(svc.normalizar_nombre_ciudad, zonas_service.normalizar_nombre_ciudad)
        self.assertIs(svc.buscar_ciudades_por_nombre, zonas_service.buscar_ciudades_por_nombre)


class ResolverCiudadConsultada(unittest.TestCase):
    CATALOGO = [ciudad(1, "Santa Cruz de la Sierra", "Santa Cruz"), ciudad(2), ciudad(8, "Potosí", "Potosí")]

    def test_unica_normalizada(self):
        db = db_con(self.CATALOGO)
        for nombre in ("La Paz", "la paz", "LA PAZ", "  La    Paz  ", "la\tpaz"):
            self.assertEqual(svc.resolver_ciudad_consultada(db, nombre).id, 2, nombre)
        for nombre in ("potosi", "POTOSÍ", " Potosi "):
            self.assertEqual(svc.resolver_ciudad_consultada(db, nombre).id, 8, nombre)

    def test_inexistente_404(self):
        with self.assertRaises(HTTPException) as cm:
            svc.resolver_ciudad_consultada(db_con(self.CATALOGO), "Atlantida")
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("Atlantida", cm.exception.detail)

    def test_ambigua_409_sin_devolver_ciudad(self):
        db = db_con([*self.CATALOGO, ciudad(11, "LA  PAZ", "Otro")])
        with self.assertRaises(HTTPException) as cm:
            svc.resolver_ciudad_consultada(db, "la paz")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("ambiguo", cm.exception.detail)
        self.assertEqual(svc.resolver_ciudad_consultada(db, "Potosi").id, 8)   # las no ambiguas siguen resolviendo

    def test_vacio_422_sin_tocar_la_bd(self):
        for vacio in (None, "", "   ", "\t"):
            db = mock.Mock()
            with self.subTest(vacio), self.assertRaises(HTTPException) as cm:
                svc.resolver_ciudad_consultada(db, vacio)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual(db.method_calls, [])

    def test_no_aproxima_por_parecido(self):
        db = db_con(self.CATALOGO)
        for parecido in ("Santa Cruz", "Paz", "La Pa", "Potos"):
            with self.subTest(parecido), self.assertRaises(HTTPException) as cm:
                svc.resolver_ciudad_consultada(db, parecido)
            self.assertEqual(cm.exception.status_code, 404)


class Permisos(unittest.TestCase):
    def test_v_c_sin_rol_403_antes_de_tocar_la_bd(self):
        for rol in ("V", "C", None):
            db = mock.Mock()
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                svc.agencias_disponibles(db, usuario(rol), "La Paz")
            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(db.method_calls, [])

    def test_asu_gs_d_consultan(self):
        for rol in ("ASU", "GS", "D"):
            db = db_con([ciudad()])
            db.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = []
            with self.subTest(rol=rol):
                c, filas = svc.agencias_disponibles(db, usuario(rol), "la paz")
                self.assertEqual((c.id, filas), (2, []))

    def test_403_antes_que_404_y_422(self):
        with self.assertRaises(HTTPException) as cm:
            svc.agencias_disponibles(mock.Mock(), usuario("V"), "   ")
        self.assertEqual(cm.exception.status_code, 403)


class ConsultaSinNMasUno(unittest.TestCase):
    def test_solo_dos_consultas_de_agencias_sin_importar_cuantas_hay(self):
        """La ciudad usa el catalogo (1 query en Fase 5); las agencias salen de
        UNA consulta agregada + su subconsulta: db.query se invoca 3 veces en
        total (catalogo, agregado de zonas, agencias) y `.all()` de agencias una vez."""
        db = db_con([ciudad()])
        query = db.query.return_value
        query.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            Fila(i, f"Ag {i}", None, None, True, 1, True) for i in range(50)]
        _, filas = svc.agencias_disponibles(db, usuario("GS"), "La Paz")
        self.assertEqual(len(filas), 50)
        self.assertEqual(db.query.call_count, 3)          # no depende de las 50 filas
        self.assertEqual(db.get.call_count, 1)            # solo la ciudad resuelta

    def test_sql_generado(self):
        """SQL real: join contra el agregado de zonas, solo habilitadas, orden
        determinista y sin DISTINCT (una fila por agencia por construccion)."""
        capturado = []
        real_all = Query.all

        def capturar(self_):
            capturado.append(str(self_.statement.compile(dialect=postgresql.dialect())))
            return []

        db = Session()
        with mock.patch.object(svc, "resolver_ciudad_consultada", return_value=ciudad()), \
             mock.patch.object(Query, "all", capturar):
            svc.agencias_disponibles(db, usuario("GS"), "La Paz")
        self.assertEqual(len(capturado), 1)                # una sola consulta de agencias
        sql = " ".join(capturado[0].split())
        self.assertIn("JOIN (SELECT agencia_zonas.id_agencia", sql)
        self.assertIn("GROUP BY agencia_zonas.id_agencia", sql)
        self.assertIn("WHERE agencia_zonas.id_ciudad = %(id_ciudad_1)s", sql)
        self.assertIn("agencias_reparto.is_active IS true", sql)
        self.assertIn("ORDER BY lower(agencias_reparto.razon_social), agencias_reparto.id_agencia", sql)
        self.assertNotIn("DISTINCT", sql)
        for prohibido in ("nit", "correo_facturacion", "direccion_fiscal", "agencia_tarifas", "costo"):
            self.assertNotIn(prohibido, sql)               # ni facturacion ni tarifas
        self.assertIs(Query.all, real_all)


class Serializacion(unittest.TestCase):
    def test_forma_sin_datos_de_facturacion_ni_tarifas(self):
        d = svc.serializar_disponible(Fila(4, "Andes Express", "Ana", "7000", True, 3, False))
        self.assertEqual(set(d), {"id_agencia", "razon_social", "contacto_operativo", "telefono", "is_active",
                                  "cubre_ciudad", "cobertura_completa", "zonas_en_ciudad"})
        self.assertEqual((d["cubre_ciudad"], d["cobertura_completa"], d["zonas_en_ciudad"]), (True, False, 3))
        for privado in ("nit", "correo_facturacion", "direccion_fiscal", "correo", "direccion",
                        "costo", "tarifa", "rango_min", "rango_max"):
            self.assertNotIn(privado, d)

    def test_cobertura_completa_es_booleana(self):
        self.assertIs(svc.serializar_disponible(Fila(1, "A", None, None, True, 1, 1))["cobertura_completa"], True)

    def test_ciudad(self):
        self.assertEqual(svc.serializar_ciudad(ciudad(2, "La Paz", "La Paz")),
                         {"id_ciudad": 2, "nombre": "La Paz", "departamento": "La Paz"})


if __name__ == "__main__":
    unittest.main()
