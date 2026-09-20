# Pruebas unitarias AISLADAS de CU19 fase 8 (cotizacion_service).
# Sin base de datos: la sesion es un Mock y las filas son tuplas con nombre. El
# comportamiento real (SQL, JWT, N+1, no persistencia) se prueba en
# tests/integration/test_agencias_cotizacion_*.py contra la BD local.
#
# REGLA DE SELECCION CONGELADA (documentada tambien en cotizacion_service):
#   1) mayor costo; 2) empate -> PESO antes que VOLUMEN; 3) empate -> zona de
#   ciudad completa antes que subzona; 4) si aun hay 2+ equivalentes -> 409.
#   Nunca se desempata por id_tarifa.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_cotizacion_service.py
import sys
import unittest
import uuid
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402

from app.modules.delivery import cotizacion_service as svc  # noqa: E402
from app.modules.delivery import disponibilidad_service, tarifas_service  # noqa: E402
from app.modules.empresa.models import Ciudad  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402

HOY = date(2026, 6, 15)
Fila = namedtuple("Fila", "id_zona nombre_zona id_tarifa criterio rango_min rango_max costo vigente_desde vigente_hasta is_active")
AgFila = namedtuple("AgFila", "id_agencia razon_social is_active")


def usuario(rol):
    return Usuario(id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def fila(idt=1, criterio="PESO", zona=None, rmin="0", rmax="5", costo="10", desde=date(2026, 1, 1), hasta=None,
         activa=True, id_zona=1):
    return Fila(id_zona, zona, idt, criterio, D(rmin), None if rmax is None else D(rmax), D(costo), desde, hasta, activa)


def zona_sin_tarifas(id_zona=1, nombre=None):
    return Fila(id_zona, nombre, None, None, None, None, None, None, None, None)


def cands(*filas, peso="2", vol="1"):
    return svc.candidatas(filas, D(peso), D(vol), HOY)


# ---------------------------------------------------------------------------
class Dimensiones(unittest.TestCase):
    def test_validas(self):
        for v, esperado in [("2.5", D("2.5")), ("0.001", D("0.001")), ("9999999.999", D("9999999.999")),
                            ("5.0000", D("5.0000")), (3, D(3)), (2.5, D("2.5")), (" 7 ", D(7)), (D("1.250"), D("1.250"))]:
            with self.subTest(v):
                self.assertEqual(svc.validar_dimension(v, "peso_kg"), esperado)

    def test_cero_y_negativos_422(self):
        for v in ("0", "0.000", "-1", "-0.001", 0, -5, D("-1")):
            with self.subTest(v), self.assertRaises(HTTPException) as cm:
                svc.validar_dimension(v, "volumen_m3")
            self.assertEqual(cm.exception.status_code, 422)
            self.assertIn("volumen_m3", cm.exception.detail)

    def test_nan_e_infinity_422(self):
        for v in ("NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf", float("nan"), float("inf"), D("NaN"), D("Infinity")):
            with self.subTest(v), self.assertRaises(HTTPException) as cm:
                svc.validar_dimension(v, "peso_kg")
            self.assertEqual(cm.exception.status_code, 422)

    def test_no_numericos_422(self):
        for v in ("abc", "", "  ", "1,5", "1.2.3", None, "0x10"):
            with self.subTest(v), self.assertRaises(HTTPException) as cm:
                svc.validar_dimension(v, "peso_kg")
            self.assertEqual(cm.exception.status_code, 422)

    def test_precision_de_la_columna(self):
        for v in ("1.2345", "0.0001", "10000000", "12345678.5", "1e400", "1E+8"):
            with self.subTest(v), self.assertRaises(HTTPException) as cm:
                svc.validar_dimension(v, "peso_kg")
            self.assertEqual(cm.exception.status_code, 422)
            self.assertIn("decimales", cm.exception.detail)
        svc.validar_dimension("1e-3", "peso_kg")          # 0.001 escrito en notacion cientifica: valido
        svc.validar_dimension("1E+6", "peso_kg")          # 1000000: 7 enteros

    def test_par_de_dimensiones(self):
        self.assertEqual(svc.validar_dimensiones("2", "0.5"), (D(2), D("0.5")))
        with self.assertRaises(HTTPException) as cm:
            svc.validar_dimensiones("2", "0")
        self.assertIn("volumen_m3", cm.exception.detail)


class Rangos(unittest.TestCase):
    """[rango_min, rango_max): minimo incluido, maximo excluido, NULL = sin tope."""

    def test_limites(self):
        self.assertTrue(svc.rango_contiene(D(5), D(10), D(5)))            # limite inferior INCLUIDO
        self.assertFalse(svc.rango_contiene(D(5), D(10), D("4.999")))
        self.assertTrue(svc.rango_contiene(D(5), D(10), D("9.999")))
        self.assertFalse(svc.rango_contiene(D(5), D(10), D(10)))          # limite superior EXCLUIDO
        self.assertFalse(svc.rango_contiene(D(5), D(10), D("10.001")))

    def test_rango_abierto(self):
        self.assertTrue(svc.rango_contiene(D(5), None, D(5)))
        self.assertTrue(svc.rango_contiene(D(5), None, D("9999999.999")))
        self.assertFalse(svc.rango_contiene(D(5), None, D("4.999")))

    def test_tramos_contiguos_el_valor_frontera_va_al_de_arriba(self):
        self.assertFalse(svc.rango_contiene(D(0), D(5), D(5)))
        self.assertTrue(svc.rango_contiene(D(5), D(10), D(5)))


class Candidatas(unittest.TestCase):
    def test_cada_criterio_usa_su_dimension(self):
        f_peso = fila(1, criterio="PESO", rmin="0", rmax="5")
        f_vol = fila(2, criterio="VOLUMEN", rmin="0", rmax="5")
        self.assertEqual([c.criterio for c in cands(f_peso, f_vol, peso="2", vol="9")], ["PESO"])     # volumen fuera de rango
        self.assertEqual([c.criterio for c in cands(f_peso, f_vol, peso="9", vol="2")], ["VOLUMEN"])   # peso fuera de rango
        self.assertEqual(sorted(c.criterio for c in cands(f_peso, f_vol, peso="2", vol="2")), ["PESO", "VOLUMEN"])
        self.assertEqual(cands(f_peso, f_vol, peso="9", vol="9"), [])
        # el peso NO se compara con un rango de volumen ni al reves
        self.assertEqual(cands(fila(1, criterio="VOLUMEN", rmin="5", rmax=None), peso="100", vol="1"), [])

    def test_inactiva_expirada_y_futura_no_son_candidatas(self):
        self.assertEqual(cands(fila(1, activa=False)), [])
        self.assertEqual(cands(fila(1, desde=date(2025, 1, 1), hasta=HOY - timedelta(days=1))), [])          # expirada ayer
        self.assertEqual(cands(fila(1, desde=HOY + timedelta(days=1))), [])                                  # futura desde manana
        self.assertEqual(len(cands(fila(1, desde=HOY, hasta=HOY))), 1)                                       # hoy incluido en ambos extremos
        self.assertEqual(len(cands(fila(1, desde=HOY - timedelta(days=9), hasta=None))), 1)                  # vigencia abierta

    def test_zona_sin_tarifas_no_aporta_candidatas(self):
        self.assertEqual(cands(zona_sin_tarifas()), [])

    def test_criterio_desconocido_se_ignora(self):
        self.assertEqual(cands(fila(1, criterio="OTRO")), [])


class ReglaDeSeleccion(unittest.TestCase):
    """Regla CONGELADA: mayor costo -> PESO -> ciudad completa -> 409 (sin ids)."""

    def elegir(self, *filas, peso="2", vol="1"):
        return svc.seleccionar(cands(*filas, peso=peso, vol=vol))[0]

    def test_una_sola_candidata(self):
        e = self.elegir(fila(1, criterio="PESO", costo="7"))
        self.assertEqual((e.criterio, e.costo), ("PESO", D(7)))

    def test_regla_1_mayor_costo_entre_peso_y_volumen(self):
        e = self.elegir(fila(1, criterio="PESO", costo="10"), fila(2, criterio="VOLUMEN", costo="25"))
        self.assertEqual((e.criterio, e.costo), ("VOLUMEN", D(25)))
        e = self.elegir(fila(1, criterio="PESO", costo="30"), fila(2, criterio="VOLUMEN", costo="25"))
        self.assertEqual((e.criterio, e.costo), ("PESO", D(30)))

    def test_regla_1_mayor_costo_entre_zonas(self):
        e = self.elegir(fila(1, zona=None, id_zona=1, costo="10"), fila(2, zona="Sopocachi", id_zona=2, costo="18"))
        self.assertEqual((e.nombre_zona, e.costo), ("Sopocachi", D(18)))       # gana la subzona: mayor costo

    def test_el_costo_domina_sobre_criterio_y_zona(self):
        # VOLUMEN en subzona pero MAS caro le gana a PESO en ciudad completa
        e = self.elegir(fila(1, "PESO", zona=None, id_zona=1, costo="10"),
                        fila(2, "VOLUMEN", zona="Sopocachi", id_zona=2, costo="10.01"))
        self.assertEqual((e.criterio, e.nombre_zona), ("VOLUMEN", "Sopocachi"))

    def test_regla_2_empate_de_costo_gana_peso(self):
        e = self.elegir(fila(1, "VOLUMEN", costo="15"), fila(2, "PESO", costo="15.00"))
        self.assertEqual(e.criterio, "PESO")
        e = self.elegir(fila(2, "PESO", costo="15"), fila(1, "VOLUMEN", costo="15"))       # el orden de entrada no influye
        self.assertEqual(e.criterio, "PESO")

    def test_regla_2_peso_gana_incluso_en_subzona_frente_a_volumen_de_ciudad_completa(self):
        e = self.elegir(fila(1, "VOLUMEN", zona=None, id_zona=1, costo="15"),
                        fila(2, "PESO", zona="Sopocachi", id_zona=2, costo="15"))
        self.assertEqual((e.criterio, e.nombre_zona), ("PESO", "Sopocachi"))               # criterio antes que tipo de zona

    def test_regla_3_empate_costo_y_criterio_gana_ciudad_completa(self):
        e = self.elegir(fila(1, "PESO", zona="Sopocachi", id_zona=2, costo="15"),
                        fila(2, "PESO", zona=None, id_zona=1, costo="15"))
        self.assertIsNone(e.nombre_zona)
        e = self.elegir(fila(1, "PESO", zona="  ", id_zona=1, costo="15"), fila(2, "PESO", zona="Norte", id_zona=2, costo="15"))
        self.assertEqual(e.id_zona, 1)                                                     # solo espacios = ciudad completa

    def test_regla_4_empate_final_es_409(self):
        with self.assertRaises(HTTPException) as cm:
            self.elegir(fila(1, "PESO", zona="Sopocachi", id_zona=1, costo="15"),
                        fila(2, "PESO", zona="Miraflores", id_zona=2, costo="15"))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("equivalentes", cm.exception.detail)
        self.assertIn("'Miraflores'", cm.exception.detail)
        self.assertIn("'Sopocachi'", cm.exception.detail)
        self.assertNotIn("id_tarifa", cm.exception.detail)

    def test_regla_4_tres_subzonas_empatadas_409_pero_no_si_una_es_mas_cara(self):
        tres = [fila(i, "PESO", zona=f"Z{i}", id_zona=i, costo="15") for i in (1, 2, 3)]
        with self.assertRaises(HTTPException) as cm:
            self.elegir(*tres)
        self.assertIn("3 tarifas", cm.exception.detail)
        e = self.elegir(*tres, fila(9, "PESO", zona="Z9", id_zona=9, costo="15.5"))
        self.assertEqual(e.nombre_zona, "Z9")                                              # ya no hay empate

    def test_regla_4_volumen_subzonas_empatadas_tambien_409(self):
        with self.assertRaises(HTTPException) as cm:
            self.elegir(fila(1, "VOLUMEN", zona="A", id_zona=1, costo="8"), fila(2, "VOLUMEN", zona="B", id_zona=2, costo="8"))
        self.assertEqual(cm.exception.status_code, 409)

    def test_empate_perdedor_no_dispara_409(self):
        """Dos candidatas empatadas entre si pero por DEBAJO de la mejor: no hay ambiguedad."""
        e = self.elegir(fila(1, "PESO", zona=None, id_zona=1, costo="50"),
                        fila(2, "PESO", zona="A", id_zona=2, costo="15"), fila(3, "PESO", zona="B", id_zona=3, costo="15"))
        self.assertEqual(e.costo, D(50))

    def test_no_se_desempata_por_id_tarifa(self):
        """Mismo escenario con ids permutados: la eleccion NO depende del id."""
        def escenario(ids):
            return [fila(ids[0], "PESO", zona=None, id_zona=1, costo="15"), fila(ids[1], "VOLUMEN", zona=None, id_zona=1, costo="15"),
                    fila(ids[2], "PESO", zona="Sur", id_zona=2, costo="15")]
        for ids in ((1, 2, 3), (3, 2, 1), (9, 1, 5), (2, 3, 1)):
            with self.subTest(ids=ids):
                e = self.elegir(*escenario(ids))
                self.assertEqual((e.criterio, e.nombre_zona), ("PESO", None))
        with self.assertRaises(HTTPException):                                            # y un empate real no se rompe con ids
            self.elegir(fila(1, "PESO", zona="A", id_zona=1, costo="1"), fila(99, "PESO", zona="B", id_zona=2, costo="1"))

    def test_orden_de_las_candidatas_es_total_y_determinista(self):
        filas = [fila(1, "VOLUMEN", zona=None, id_zona=1, costo="10"), fila(2, "PESO", zona="B", id_zona=2, costo="10"),
                 fila(3, "PESO", zona="A", id_zona=3, costo="20"), fila(4, "PESO", zona=None, id_zona=1, costo="10", rmin="0", rmax="9")]
        _, o1 = svc.seleccionar(cands(*filas))
        _, o2 = svc.seleccionar(cands(*reversed(filas)))
        self.assertEqual([(c.criterio, c.nombre_zona, c.costo) for c in o1], [(c.criterio, c.nombre_zona, c.costo) for c in o2])
        self.assertEqual([(c.costo, c.criterio, c.nombre_zona) for c in o1],
                         [(D(20), "PESO", "A"), (D(10), "PESO", None), (D(10), "PESO", "B"), (D(10), "VOLUMEN", None)])

    def test_sin_candidatas_es_error_de_programacion(self):
        with self.assertRaises(ValueError):
            svc.seleccionar([])


# ---------------------------------------------------------------------------
def db_mock(agencia=AgFila(4, "Andes Express", True), filas=(), ciudad=None):
    db = mock.Mock()
    db.query.return_value.filter.return_value.first.return_value = agencia
    return db


class FlujoCotizar(unittest.TestCase):
    CIUDAD = Ciudad(id=2, nombre="La Paz", departamento="La Paz")

    def cotizar(self, rol="GS", agencia=AgFila(4, "Andes Express", True), filas=(), ciudad=None, peso="2", vol="1", db=None):
        db = db or db_mock(agencia)
        with mock.patch.object(svc, "resolver_ciudad_consultada", return_value=ciudad or self.CIUDAD), \
             mock.patch.object(svc, "_zonas_y_tarifas", return_value=list(filas)):
            return db, svc.cotizar(db, usuario(rol), 4, "la paz", peso, vol, hoy=HOY)

    def test_v_c_sin_rol_403_sin_tocar_la_bd(self):
        for rol in ("V", "C", None):
            db = mock.Mock()
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                svc.cotizar(db, usuario(rol), 4, "La Paz", "2", "1")
            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(db.method_calls, [])

    def test_asu_gs_d_cotizan_con_la_misma_respuesta(self):
        filas = [fila(1, "PESO", costo="10"), fila(2, "VOLUMEN", costo="25")]
        resp = {rol: self.cotizar(rol, filas=filas)[1] for rol in ("ASU", "GS", "D")}
        self.assertEqual(resp["ASU"], resp["GS"])
        self.assertEqual(resp["GS"], resp["D"])

    def test_dimensiones_invalidas_422_antes_de_la_bd(self):
        for peso, vol in (("0", "1"), ("1", "-1"), ("NaN", "1"), ("1", "Infinity"), ("1.2345", "1")):
            db = mock.Mock()
            with self.subTest(peso=peso, vol=vol), self.assertRaises(HTTPException) as cm:
                svc.cotizar(db, usuario("GS"), 4, "La Paz", peso, vol)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual(db.method_calls, [])

    def test_agencia_inexistente_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.cotizar(agencia=None)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("agencia", cm.exception.detail)

    def test_agencia_deshabilitada_ninguno_cotiza(self):
        deshabilitada = AgFila(4, "Andes Express", False)
        for rol, codigo in (("ASU", 400), ("GS", 400), ("D", 404)):
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                self.cotizar(rol, agencia=deshabilitada, filas=[fila()])
            self.assertEqual(cm.exception.status_code, codigo)
        with self.assertRaises(HTTPException) as cm:
            self.cotizar("GS", agencia=deshabilitada)
        self.assertIn("deshabilitada", cm.exception.detail)

    def test_ciudad_vacia_inexistente_y_ambigua_vienen_de_fase_5_y_7(self):
        self.assertIs(svc.resolver_ciudad_consultada, disponibilidad_service.resolver_ciudad_consultada)
        for codigo in (404, 409, 422):
            with mock.patch.object(svc, "resolver_ciudad_consultada", side_effect=HTTPException(codigo, "x")):
                with self.subTest(codigo), self.assertRaises(HTTPException) as cm:
                    svc.cotizar(db_mock(), usuario("GS"), 4, "x", "2", "1", hoy=HOY)
                self.assertEqual(cm.exception.status_code, codigo)

    def test_sin_zonas_en_la_ciudad_404_de_cobertura(self):
        with self.assertRaises(HTTPException) as cm:
            self.cotizar(filas=[])
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("no tiene cobertura", cm.exception.detail)

    def test_cubre_pero_sin_tarifa_aplicable_404_con_otro_mensaje(self):
        mensajes = []
        for filas in ([zona_sin_tarifas()], [fila(1, "PESO", rmin="10", rmax="20")],
                      [fila(1, activa=False)], [fila(1, desde=date(2025, 1, 1), hasta=date(2025, 12, 31))],
                      [fila(1, desde=HOY + timedelta(days=5))]):
            with self.assertRaises(HTTPException) as cm:
                self.cotizar(filas=filas)
            self.assertEqual(cm.exception.status_code, 404)
            self.assertIn("no existe tarifa aplicable", cm.exception.detail)
            self.assertNotIn("no tiene cobertura", cm.exception.detail)
            mensajes.append(cm.exception.detail)
        self.assertIn("peso 2 kg", mensajes[0])
        self.assertIn("volumen 1 m3", mensajes[0])
        self.assertIn(HOY.isoformat(), mensajes[0])

    def test_subzona_cuenta_como_cobertura(self):
        _, r = self.cotizar(filas=[fila(1, "PESO", zona="Sopocachi", costo="12")])
        self.assertEqual((r["costo_agencia"], r["tarifa"]["nombre_zona"]), (D(12), "Sopocachi"))

    def test_respuesta_completa_y_sin_datos_de_facturacion(self):
        filas = [fila(11, "PESO", rmin="0", rmax="5", costo="10", desde=date(2026, 1, 1), hasta=date(2026, 12, 31)),
                 fila(12, "VOLUMEN", rmin="0.5", rmax=None, costo="25.5")]
        db, r = self.cotizar(filas=filas, peso="2.5", vol="0.8")
        self.assertEqual(set(r), {"agencia", "ciudad", "peso_kg", "volumen_m3", "fecha_referencia", "criterio",
                                  "costo_agencia", "tarifa", "candidatas"})
        self.assertEqual(r["agencia"], {"id_agencia": 4, "razon_social": "Andes Express"})
        self.assertEqual(r["ciudad"], {"id_ciudad": 2, "nombre": "La Paz", "departamento": "La Paz"})
        self.assertEqual((r["peso_kg"], r["volumen_m3"], r["fecha_referencia"]), (D("2.5"), D("0.8"), HOY))
        self.assertEqual((r["criterio"], r["costo_agencia"]), ("VOLUMEN", D("25.5")))
        self.assertEqual(r["tarifa"]["id_tarifa"], 12)
        self.assertEqual((r["tarifa"]["rango_min"], r["tarifa"]["rango_max"], r["tarifa"]["vigente_hasta"]), (D("0.5"), None, None))
        self.assertEqual([(c["criterio"], c["seleccionada"]) for c in r["candidatas"]], [("VOLUMEN", True), ("PESO", False)])
        for privado in ("nit", "correo_facturacion", "direccion_fiscal"):
            self.assertNotIn(privado, str(r))

    def test_solo_una_dimension_aplica(self):
        _, r = self.cotizar(filas=[fila(1, "PESO", costo="10"), fila(2, "VOLUMEN", rmin="5", rmax="9", costo="99")], vol="1")
        self.assertEqual((r["criterio"], r["costo_agencia"], len(r["candidatas"])), ("PESO", D(10), 1))

    def test_ambiguedad_final_409(self):
        with self.assertRaises(HTTPException) as cm:
            self.cotizar(filas=[fila(1, "PESO", zona="A", id_zona=1, costo="9"), fila(2, "PESO", zona="B", id_zona=2, costo="9")])
        self.assertEqual(cm.exception.status_code, 409)

    def test_es_solo_lectura(self):
        db, _ = self.cotizar(filas=[fila(1, "PESO", costo="10")])
        for escritura in ("add", "add_all", "delete", "commit", "flush", "merge", "execute", "bulk_save_objects"):
            getattr(db, escritura).assert_not_called()

    def test_usa_la_fecha_utc_de_hoy_de_tarifas_service(self):
        self.assertIs(svc.fecha_hoy, tarifas_service.fecha_hoy)
        self.assertIs(svc.vigente_en, tarifas_service.vigente_en)


class ConsultaEficiente(unittest.TestCase):
    def test_una_consulta_de_agencia_y_una_de_zonas_con_tarifas(self):
        """db.query se invoca: 1 agencia + 3 de resolver ciudad (Fase 5/7, mockeada
        aqui) + 1 zonas/tarifas -> con la ciudad mockeada solo quedan 2."""
        db = db_mock()
        with mock.patch.object(svc, "resolver_ciudad_consultada", return_value=Ciudad(id=2, nombre="La Paz")), \
             mock.patch.object(svc, "_zonas_y_tarifas", wraps=svc._zonas_y_tarifas):
            db.query.return_value.outerjoin.return_value.filter.return_value.order_by.return_value.all.return_value = [
                fila(i, "PESO", zona=f"Z{i}", id_zona=i, rmin="50", rmax="60") for i in range(200)] + [fila(999, "PESO", zona=None, id_zona=999)]
            r = svc.cotizar(db, usuario("GS"), 4, "La Paz", "2", "1", hoy=HOY)
        self.assertEqual(db.query.call_count, 2)                 # agencia + (zonas+tarifas): no crece con las 201 filas
        self.assertEqual(r["tarifa"]["id_tarifa"], 999)

    def test_sql_generado(self):
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.orm import Query, Session

        capturado = []

        def capturar(self_):
            capturado.append(" ".join(str(self_.statement.compile(dialect=postgresql.dialect())).split()))
            return []

        with mock.patch.object(Query, "all", capturar):
            svc._zonas_y_tarifas(Session(), 4, 2)
        (sql,) = capturado
        self.assertIn("FROM agencia_zonas LEFT OUTER JOIN agencia_tarifas ON agencia_tarifas.id_zona = agencia_zonas.id_zona AND agencia_tarifas.is_active IS true", sql)
        self.assertIn("agencia_zonas.id_agencia = %(id_agencia_1)s AND agencia_zonas.id_ciudad = %(id_ciudad_1)s", sql)
        for prohibido in ("nit", "correo_facturacion", "direccion_fiscal", "agencias_reparto", "envios", "ventas"):
            self.assertNotIn(prohibido, sql)


if __name__ == "__main__":
    unittest.main()
