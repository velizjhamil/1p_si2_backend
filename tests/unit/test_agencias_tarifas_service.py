# Pruebas unitarias AISLADAS de CU19 fase 6 (schemas + tarifas_service).
# Sin base de datos: la sesion es un Mock y las entidades son objetos en
# memoria. El comportamiento real (CHECK, FK, cascadas, JWT, concurrencia) se
# prueba en tests/integration/test_agencias_tarifas_*.py contra la BD local.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_tarifas_service.py
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.modules.delivery import tarifas_service as svc  # noqa: E402
from app.modules.delivery.models import AgenciaReparto, AgenciaTarifa, AgenciaZona  # noqa: E402
from app.modules.empresa.models import Ciudad  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia_tarifa import TarifaCreate, TarifaUpdate  # noqa: E402

ENE1, ENE31, FEB1, DIC31 = date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1), date(2026, 12, 31)


def usuario(rol):
    return Usuario(id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def agencia(activa=True):
    return AgenciaReparto(id_agencia=7, razon_social="Andes Express", nit="1020304050",
                          correo_facturacion="f@andes-express.com", direccion_fiscal="Av 1", is_active=activa)


def zona():
    return AgenciaZona(id_zona=3, id_agencia=7, id_ciudad=2, ciudad=Ciudad(id=2, nombre="La Paz"))


def tarifa(id_tarifa=1, criterio="PESO", rmin="0", rmax="5", costo="10", desde=ENE1, hasta=None, activa=True):
    return AgenciaTarifa(id_tarifa=id_tarifa, id_zona=3, criterio=criterio, rango_min=D(rmin),
                         rango_max=None if rmax is None else D(rmax), costo=D(costo), vigente_desde=desde,
                         vigente_hasta=hasta, is_active=activa,
                         fecha_creacion=datetime.now(timezone.utc), fecha_actualizacion=datetime.now(timezone.utc))


def datos(criterio="PESO", rmin="0", rmax="5", costo="10", desde=ENE1, hasta=None, activa=True):
    return dict(criterio=criterio, rango_min=D(rmin), rango_max=None if rmax is None else D(rmax),
                costo=D(costo), vigente_desde=desde, vigente_hasta=hasta, is_active=activa)


def payload(**extra):
    base = dict(criterio="PESO", rango_min="0", rango_max="5", costo="10", vigente_desde="2026-01-01")
    base.update(extra)
    return TarifaCreate(**base)


class Diag:
    def __init__(self, n):
        self.constraint_name = n


class Orig(Exception):
    def __init__(self, n):
        super().__init__(n)
        self.diag = Diag(n)


def integrity(n):
    return IntegrityError("x", {}, Orig(n))


# ---------------------------------------------------------------------------
class Rangos(unittest.TestCase):
    """Rango [min, max): el maximo NO se incluye; max NULL = abierto."""

    def test_intersecciones(self):
        casos = [
            (("0", "5"), ("3", "8"), True),       # parcial
            (("0", "10"), ("2", "4"), True),      # contenido
            (("2", "4"), ("0", "10"), True),      # contenedor
            (("0", "5"), ("0", "5"), True),       # identico
            (("0", "5"), ("5", "10"), False),     # contiguos: 5 no pertenece a [0,5)
            (("5", "10"), ("0", "5"), False),
            (("0", "5"), ("6", "10"), False),     # separados
            (("0", "5"), ("4.999", "10"), True),  # 4.999 si esta en [0,5)
        ]
        for (a, b, esperado) in [(c[0], c[1], c[2]) for c in casos]:
            with self.subTest(a=a, b=b):
                self.assertIs(svc.rangos_se_intersectan(D(a[0]), D(a[1]), D(b[0]), D(b[1])), esperado)

    def test_rango_abierto(self):
        self.assertTrue(svc.rangos_se_intersectan(D(5), None, D(100), D(200)))    # [5,inf) ∩ [100,200)
        self.assertTrue(svc.rangos_se_intersectan(D(5), None, D(0), D(6)))        # [5,inf) ∩ [0,6)
        self.assertFalse(svc.rangos_se_intersectan(D(5), None, D(0), D(5)))       # contiguo: [0,5) no toca [5,inf)
        self.assertTrue(svc.rangos_se_intersectan(D(5), None, D(9), None))        # dos abiertos siempre se cruzan
        self.assertTrue(svc.rangos_se_intersectan(D(0), None, D(50), None))

    def test_validar_rango(self):
        svc.validar_rango(D(0), D(5))
        svc.validar_rango(D(0), None)
        svc.validar_rango(D("0.001"), D("0.002"))
        for rmin, rmax in ((D(-1), D(5)), (D(5), D(5)), (D(5), D(2)), (D(0), D(0)), (D("-0.001"), None)):
            with self.subTest(rmin=rmin, rmax=rmax), self.assertRaises(HTTPException) as cm:
                svc.validar_rango(rmin, rmax)
            self.assertEqual(cm.exception.status_code, 422)


class Vigencias(unittest.TestCase):
    """Vigencia [desde, hasta] con ambas fechas incluidas; hasta NULL = sin fin."""

    def test_intersecciones(self):
        casos = [
            ((ENE1, ENE31), (date(2026, 1, 15), FEB1), True),
            ((ENE1, ENE31), (ENE31, FEB1), True),        # comparten el 31-ene
            ((ENE1, ENE31), (FEB1, DIC31), False),       # dias consecutivos: no se solapan
            ((FEB1, DIC31), (ENE1, ENE31), False),
            ((ENE1, DIC31), (date(2026, 3, 1), date(2026, 3, 31)), True),
        ]
        for a, b, esperado in casos:
            with self.subTest(a=a, b=b):
                self.assertIs(svc.vigencias_se_intersectan(a[0], a[1], b[0], b[1]), esperado)

    def test_vigencia_abierta(self):
        self.assertTrue(svc.vigencias_se_intersectan(ENE1, None, date(2030, 1, 1), date(2030, 2, 1)))
        self.assertFalse(svc.vigencias_se_intersectan(FEB1, None, ENE1, ENE31))    # abierta desde feb no toca enero
        self.assertTrue(svc.vigencias_se_intersectan(ENE1, None, date(2099, 1, 1), None))

    def test_validar_vigencia(self):
        svc.validar_vigencia(ENE1, None)
        svc.validar_vigencia(ENE1, ENE1)          # un solo dia es valido
        svc.validar_vigencia(ENE1, ENE31)
        with self.assertRaises(HTTPException) as cm:
            svc.validar_vigencia(ENE31, ENE1)
        self.assertEqual(cm.exception.status_code, 422)

    def test_vigente_en(self):
        hoy = date(2026, 6, 15)
        self.assertTrue(svc.vigente_en(tarifa(desde=ENE1, hasta=None), hoy))
        self.assertTrue(svc.vigente_en(tarifa(desde=hoy, hasta=hoy), hoy))               # limites incluidos
        self.assertFalse(svc.vigente_en(tarifa(desde=ENE1, hasta=ENE31), hoy))           # expirada
        self.assertFalse(svc.vigente_en(tarifa(desde=date(2026, 7, 1), hasta=None), hoy))  # futura
        self.assertFalse(svc.vigente_en(tarifa(desde=ENE1, hasta=None, activa=False), hoy))  # inactiva


class Solapamiento(unittest.TestCase):
    def test_requiere_mismo_criterio_rango_y_vigencia(self):
        base = datos("PESO", "0", "5", desde=ENE1, hasta=ENE31)
        self.assertTrue(svc.tarifas_se_solapan(base, datos("PESO", "3", "8", desde=ENE31, hasta=None)))
        self.assertFalse(svc.tarifas_se_solapan(base, datos("VOLUMEN", "0", "5", desde=ENE1, hasta=ENE31)))  # otro criterio
        self.assertFalse(svc.tarifas_se_solapan(base, datos("PESO", "5", "9", desde=ENE1, hasta=ENE31)))     # rango contiguo
        self.assertFalse(svc.tarifas_se_solapan(base, datos("PESO", "0", "5", desde=FEB1, hasta=DIC31)))     # otra vigencia
        self.assertFalse(svc.tarifas_se_solapan(base, datos("PESO", "5", "9", desde=FEB1, hasta=DIC31)))     # ninguno

    def test_expirada_no_bloquea_futura(self):
        expirada = tarifa(1, "PESO", "0", "5", desde=date(2025, 1, 1), hasta=date(2025, 12, 31))
        futura = datos("PESO", "0", "5", desde=date(2027, 1, 1), hasta=None)
        self.assertIsNone(svc.encontrar_solapada(futura, [expirada]))

    def test_devuelve_la_primera_en_conflicto(self):
        otras = [tarifa(9, rmin="0", rmax="5"), tarifa(4, rmin="0", rmax="5"), tarifa(2, rmin="20", rmax=None)]
        self.assertEqual(svc.encontrar_solapada(datos("PESO", "1", "3"), otras).id_tarifa, 4)
        self.assertIsNone(svc.encontrar_solapada(datos("PESO", "5", "20"), otras))   # entra justo entre tramos


class SchemasForma(unittest.TestCase):
    def test_valida_minima_y_abierta(self):
        t = payload(rango_max=None)
        self.assertEqual((t.criterio, t.rango_min, t.rango_max, t.costo, t.is_active), ("PESO", D(0), None, D(10), True))
        self.assertIsNone(t.vigente_hasta)

    def test_criterio_valido_e_insensible_a_mayusculas(self):
        self.assertEqual(payload(criterio=" volumen ").criterio, "VOLUMEN")
        for malo in ("PESADO", "", "COMBINADO", "PESO+VOLUMEN", None, 5):
            with self.subTest(malo), self.assertRaises(ValidationError):
                payload(criterio=malo)

    def test_obligatorios(self):
        base = dict(criterio="PESO", rango_min="0", costo="10", vigente_desde="2026-01-01")
        for falta in base:
            datos_ = dict(base)
            del datos_[falta]
            with self.subTest(falta), self.assertRaises(ValidationError):
                TarifaCreate(**datos_)

    def test_valores_invalidos(self):
        for extra in ({"rango_min": "-1"}, {"rango_min": "-0.001"}, {"rango_max": "0"}, {"rango_max": "-5"},
                      {"costo": "-1"}, {"costo": "-0.01"}, {"rango_min": "abc"}, {"costo": "NaN"},
                      {"costo": "Infinity"}, {"rango_max": "Infinity"}, {"rango_min": "1.2345"},
                      {"costo": "10.123"}, {"costo": "100000000.00"}, {"rango_min": "10000000"},
                      {"vigente_desde": "no-es-fecha"}, {"vigente_desde": "2026-13-01"},
                      {"vigente_hasta": "ayer"}, {"is_active": "quizas"}):
            with self.subTest(extra), self.assertRaises(ValidationError):
                payload(**extra)

    def test_limites_maximos_aceptados(self):
        t = payload(rango_min="9999999.999", costo="99999999.99")
        self.assertEqual((t.rango_min, t.costo), (D("9999999.999"), D("99999999.99")))

    def test_costo_cero_es_valido(self):
        self.assertEqual(payload(costo="0").costo, D(0))

    def test_update_presencia_y_null(self):
        self.assertEqual(TarifaUpdate().model_fields_set, set())
        u = TarifaUpdate.model_validate({"rango_max": None, "vigente_hasta": None})
        self.assertEqual(u.model_fields_set, {"rango_max", "vigente_hasta"})   # null explicito = abierto
        for campo in ("criterio", "rango_min", "costo", "vigente_desde", "is_active"):
            with self.subTest(campo), self.assertRaises(ValidationError):
                TarifaUpdate.model_validate({campo: None})
        with self.assertRaises(ValidationError):
            TarifaUpdate(criterio="PESADO")
        with self.assertRaises(ValidationError):
            TarifaUpdate(costo="-1")


class Permisos(unittest.TestCase):
    ESCRITURAS = (
        lambda db, u: svc.crear_tarifa(db, u, 7, 3, payload()),
        lambda db, u: svc.actualizar_tarifa(db, u, 7, 3, 1, TarifaUpdate(costo="5")),
        lambda db, u: svc.eliminar_tarifa(db, u, 7, 3, 1),
    )

    def test_d_v_c_sin_rol_no_escriben_ni_tocan_la_bd(self):
        for rol in ("D", "V", "C", None):
            for i, op in enumerate(self.ESCRITURAS):
                with self.subTest(rol=rol, op=i):
                    db = mock.Mock()
                    with self.assertRaises(HTTPException) as cm:
                        op(db, usuario(rol))
                    self.assertEqual(cm.exception.status_code, 403)
                    self.assertEqual(db.method_calls, [])

    def test_v_c_no_consultan(self):
        for rol in ("V", "C", None):
            for op in (lambda d: svc.listar_tarifas(d, usuario(rol), 7, 3),
                       lambda d: svc.obtener_tarifa(d, usuario(rol), 7, 3, 1)):
                db = mock.Mock()
                with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                    op(db)
                self.assertEqual(cm.exception.status_code, 403)
                self.assertEqual(db.method_calls, [])

    def test_lectura_hereda_la_visibilidad_de_zonas(self):
        z = zona()
        z.tarifas = [tarifa(2, "VOLUMEN", "0", "1"), tarifa(1, "PESO", "5", None), tarifa(3, "PESO", "0", "5")]
        with mock.patch.object(svc, "obtener_zona", return_value=z):
            orden = [t.id_tarifa for t in svc.listar_tarifas(mock.Mock(), usuario("D"), 7, 3)]
        self.assertEqual(orden, [3, 1, 2])    # criterio, luego rango_min
        with mock.patch.object(svc, "obtener_zona", side_effect=HTTPException(404, "x")):
            with self.assertRaises(HTTPException) as cm:
                svc.listar_tarifas(mock.Mock(), usuario("D"), 7, 3)
            self.assertEqual(cm.exception.status_code, 404)


class CrearTarifa(unittest.TestCase):
    def _crear(self, pl, otras=(), agencia_=None, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_agencia_para_escritura", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=zona()), \
             mock.patch.object(svc, "_activas_de_la_zona", return_value=list(otras)):
            return db, svc.crear_tarifa(db, usuario("GS"), 7, 3, pl)

    def test_valida_peso_y_volumen(self):
        for criterio in ("PESO", "VOLUMEN"):
            db, _ = self._crear(payload(criterio=criterio))
            t = db.add.call_args.args[0]
            self.assertEqual((t.id_zona, t.criterio, t.costo), (3, criterio, D(10)))
            db.commit.assert_called_once()

    def test_rango_abierto_y_vigencia_abierta(self):
        db, _ = self._crear(payload(rango_min="5", rango_max=None, vigente_hasta=None))
        t = db.add.call_args.args[0]
        self.assertIsNone(t.rango_max)
        self.assertIsNone(t.vigente_hasta)

    def test_rango_o_vigencia_invalidos_422_sin_escribir(self):
        for extra in ({"rango_min": "5", "rango_max": "5"}, {"rango_min": "8", "rango_max": "3"},
                      {"vigente_desde": "2026-06-01", "vigente_hasta": "2026-05-31"}):
            db = mock.Mock()
            with self.subTest(extra), self.assertRaises(HTTPException) as cm:
                self._crear(payload(**extra), db=db)
            self.assertEqual(cm.exception.status_code, 422)
            db.add.assert_not_called()

    def test_solapamiento_409_con_detalle_y_sin_escribir(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._crear(payload(rango_min="3", rango_max="8"), otras=[tarifa(4, rmin="0", rmax="5")], db=db)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("#4", cm.exception.detail)
        self.assertIn("PESO", cm.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_adyacente_y_otra_vigencia_se_permiten(self):
        self._crear(payload(rango_min="5", rango_max="10"), otras=[tarifa(1, rmin="0", rmax="5")])
        self._crear(payload(vigente_desde="2027-01-01"),
                    otras=[tarifa(1, desde=date(2025, 1, 1), hasta=date(2025, 12, 31))])

    def test_inactiva_no_valida_solapamiento(self):
        # una tarifa inactiva no es aplicable: se puede guardar aunque se cruce
        db, _ = self._crear(payload(is_active=False), otras=[tarifa(1)])
        db.add.assert_called_once()

    def test_agencia_deshabilitada_400_antes_de_validar(self):
        with mock.patch("app.modules.delivery.zonas_service._buscar", return_value=agencia(activa=False)), \
             mock.patch.object(svc, "_buscar_zona") as bz:
            db = mock.Mock()
            with self.assertRaises(HTTPException) as cm:
                svc.crear_tarifa(db, usuario("GS"), 7, 3, payload())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("deshabilitada", cm.exception.detail)
        bz.assert_not_called()
        db.add.assert_not_called()

    def test_zona_de_otra_agencia_404(self):
        db = mock.Mock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with mock.patch.object(svc, "_agencia_para_escritura", return_value=agencia()):
            with self.assertRaises(HTTPException) as cm:
                svc.crear_tarifa(db, usuario("GS"), 7, 99, payload())
        self.assertEqual(cm.exception.status_code, 404)

    def test_check_de_la_db_por_carrera_es_422_no_500(self):
        db = mock.Mock()
        db.commit.side_effect = integrity("ck_agencia_tarifas_rango_max_mayor_que_min")
        with self.assertRaises(HTTPException) as cm:
            self._crear(payload(), db=db)
        self.assertEqual(cm.exception.status_code, 422)
        db.rollback.assert_called_once()


class TraducirIntegrityError(unittest.TestCase):
    def test_mapeo(self):
        for nombre, codigo in [
            ("ck_agencia_tarifas_criterio_valido", 422), ("ck_agencia_tarifas_rango_min_no_negativo", 422),
            ("ck_agencia_tarifas_rango_max_mayor_que_min", 422), ("ck_agencia_tarifas_costo_no_negativo", 422),
            ("ck_agencia_tarifas_vigencia_valida", 422),
            ("fk_agencia_tarifas_id_zona_agencia_zonas", 404),
            ("fk_envios_id_tarifa_aplicada_agencia_tarifas", 409), ("otra", 422),
        ]:
            with self.subTest(nombre):
                self.assertEqual(svc.traducir_integrity_error(integrity(nombre)).status_code, codigo)

    def test_sin_diag_usa_el_texto(self):
        exc = IntegrityError("x", {}, Exception('violates check constraint "ck_agencia_tarifas_costo_no_negativo"'))
        self.assertEqual(svc.traducir_integrity_error(exc).status_code, 422)


class ActualizarTarifa(unittest.TestCase):
    def _actualizar(self, t, cuerpo, otras=(), agencia_=None, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_agencia_para_escritura", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=zona()), \
             mock.patch.object(svc, "_buscar_tarifa", return_value=t), \
             mock.patch.object(svc, "_activas_de_la_zona", return_value=list(otras)) as act:
            return db, svc.actualizar_tarifa(db, usuario("GS"), 7, 3, t.id_tarifa, TarifaUpdate.model_validate(cuerpo)), act

    def test_cambia_solo_lo_enviado(self):
        t = tarifa(rmin="0", rmax="5", costo="10")
        db, r, _ = self._actualizar(t, {"costo": "12.50"})
        self.assertEqual((r.costo, r.rango_min, r.rango_max, r.criterio), (D("12.50"), D(0), D(5), "PESO"))
        db.commit.assert_called_once()

    def test_null_explicito_abre_el_rango_y_la_vigencia(self):
        t = tarifa(rmin="0", rmax="5", desde=ENE1, hasta=ENE31)
        _, r, _ = self._actualizar(t, {"rango_max": None, "vigente_hasta": None})
        self.assertIsNone(r.rango_max)
        self.assertIsNone(r.vigente_hasta)

    def test_puede_cerrar_un_tramo_abierto(self):
        t = tarifa(rmin="5", rmax=None)
        _, r, _ = self._actualizar(t, {"rango_max": "20"})
        self.assertEqual(r.rango_max, D(20))

    def test_resultado_invalido_422_y_no_modifica(self):
        t = tarifa(rmin="0", rmax="5")
        for cuerpo in ({"rango_min": "9"}, {"rango_max": "0.5", "rango_min": "1"}, {"vigente_hasta": "2025-12-31"}):
            with self.subTest(cuerpo), self.assertRaises(HTTPException) as cm:
                self._actualizar(t, cuerpo)
            self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual((t.rango_min, t.rango_max, t.vigente_hasta), (D(0), D(5), None))

    def test_solapamiento_excluye_a_si_misma_y_detecta_otras(self):
        t = tarifa(1, rmin="0", rmax="5")
        _, _, act = self._actualizar(t, {"costo": "9"})
        self.assertEqual(act.call_args.args[-1], 1)                       # excluir_id = ella misma
        vecina = tarifa(2, rmin="5", rmax="10")
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(t, {"rango_max": "7"}, otras=[vecina])
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(t.rango_max, D(5))
        self._actualizar(t, {"rango_max": "5.5"}, otras=[tarifa(2, rmin="6", rmax="10")])   # no se cruza

    def test_desactivar_no_valida_y_reactivar_si(self):
        t = tarifa(1, activa=True)
        _, r, act = self._actualizar(t, {"is_active": False}, otras=[tarifa(2)])   # identica a otra: igual se desactiva
        self.assertFalse(r.is_active)
        t2 = tarifa(1, activa=False)
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(t2, {"is_active": True}, otras=[tarifa(2)])            # reactivar choca
        self.assertEqual(cm.exception.status_code, 409)
        self.assertFalse(t2.is_active)

    def test_cambio_de_criterio_valida_contra_el_nuevo(self):
        t = tarifa(1, "PESO", rmin="0", rmax="5")
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(t, {"criterio": "VOLUMEN"}, otras=[tarifa(2, "VOLUMEN", rmin="0", rmax="5")])
        self.assertEqual(cm.exception.status_code, 409)

    def test_sin_cambio_real_no_commit(self):
        t = tarifa(rmin="0", rmax="5", costo="10")
        db, r, act = self._actualizar(t, {"costo": "10.00", "rango_max": "5"})
        db.commit.assert_not_called()
        act.assert_not_called()

    def test_body_vacio_400_antes_de_la_bd(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_tarifa(db, usuario("GS"), 7, 3, 1, TarifaUpdate())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(db.method_calls, [])

    def test_agencia_deshabilitada_400(self):
        with mock.patch("app.modules.delivery.zonas_service._buscar", return_value=agencia(activa=False)):
            with self.assertRaises(HTTPException) as cm:
                svc.actualizar_tarifa(mock.Mock(), usuario("GS"), 7, 3, 1, TarifaUpdate(costo="1"))
        self.assertEqual(cm.exception.status_code, 400)

    def test_tarifa_de_otra_zona_404(self):
        db = mock.Mock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with mock.patch.object(svc, "_agencia_para_escritura", return_value=agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=zona()):
            with self.assertRaises(HTTPException) as cm:
                svc.actualizar_tarifa(db, usuario("GS"), 7, 3, 99, TarifaUpdate(costo="1"))
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("no tiene una tarifa", cm.exception.detail)


class EliminarTarifa(unittest.TestCase):
    def _eliminar(self, t, agencia_=None, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=zona()), \
             mock.patch.object(svc, "_buscar_tarifa", return_value=t):
            return db, svc.eliminar_tarifa(db, usuario("GS"), 7, 3, t.id_tarifa)

    def test_elimina(self):
        t = tarifa(5, "VOLUMEN", "0", None)
        db, desc = self._eliminar(t)
        self.assertIn("#5", desc)
        self.assertIn("en adelante", desc)
        db.delete.assert_called_once_with(t)
        db.commit.assert_called_once()

    def test_permitido_con_agencia_deshabilitada(self):
        db, _ = self._eliminar(tarifa(), agencia_=agencia(activa=False))
        db.delete.assert_called_once()

    def test_fk_de_envios_409(self):
        db = mock.Mock()
        db.commit.side_effect = integrity("fk_envios_id_tarifa_aplicada_agencia_tarifas")
        with self.assertRaises(HTTPException) as cm:
            self._eliminar(tarifa(), db=db)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("desactivela", cm.exception.detail)
        db.rollback.assert_called_once()


class Serializacion(unittest.TestCase):
    def test_forma_y_vigente(self):
        d = svc.serializar_tarifa(tarifa(rmax=None, desde=ENE1, hasta=None), 7, hoy=date(2026, 6, 1))
        self.assertEqual(set(d), {"id_tarifa", "id_zona", "id_agencia", "criterio", "rango_min", "rango_max", "costo",
                                  "vigente_desde", "vigente_hasta", "is_active", "vigente", "fecha_creacion",
                                  "fecha_actualizacion"})
        self.assertEqual((d["id_agencia"], d["rango_max"], d["vigente"]), (7, None, True))
        self.assertFalse(svc.serializar_tarifa(tarifa(desde=ENE1, hasta=ENE31), 7, hoy=date(2026, 6, 1))["vigente"])
        for privado in ("nit", "correo_facturacion", "direccion_fiscal"):
            self.assertNotIn(privado, d)

    def test_hoy_es_utc(self):
        self.assertLessEqual(abs((svc.fecha_hoy() - datetime.now(timezone.utc).date()).days), 0)
        self.assertIsInstance(svc.fecha_hoy() + timedelta(days=1), date)


if __name__ == "__main__":
    unittest.main()
