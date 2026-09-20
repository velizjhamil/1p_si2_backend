# Pruebas unitarias AISLADAS del rango de fechas por defecto de CU20 (sin BD).
#
# Cubre el bug de `_rango_default()` (reportes.py): antes usaba
# hoy.replace(day=max(1, hoy.day - 29)) y nunca salia del mes actual.
# Tambien cubre el rango por defecto de `construir_filtros` (service.py).
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_reportes_rango.py
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra mappers)
from app.api.v1.endpoints.reportes import _rango_default  # noqa: E402
from app.modules.reportes import service  # noqa: E402
from app.modules.reportes.service import ReporteFiltroError, construir_filtros  # noqa: E402


class RangoDefaultTest(unittest.TestCase):
    def test_dentro_del_mismo_mes(self):
        self.assertEqual(
            _rango_default(date(2026, 9, 30)), (date(2026, 9, 1), date(2026, 9, 30))
        )

    def test_cruza_de_mes_regresion_del_bug(self):
        # Bug original: el dia 10 devolvia el dia 1 del MISMO mes.
        desde, hasta = _rango_default(date(2026, 3, 10))
        self.assertEqual((desde, hasta), (date(2026, 2, 9), date(2026, 3, 10)))
        self.assertNotEqual(desde, date(2026, 3, 1))

    def test_cruza_de_anio(self):
        self.assertEqual(
            _rango_default(date(2026, 1, 15)), (date(2025, 12, 17), date(2026, 1, 15))
        )

    def test_anio_bisiesto(self):
        self.assertEqual(
            _rango_default(date(2028, 3, 1)), (date(2028, 2, 1), date(2028, 3, 1))
        )

    def test_siempre_30_dias_inclusive_y_termina_hoy(self):
        d = date(2025, 12, 31)
        for _ in range(800):  # recorre ~2 anios: todos los meses/anios/bisiestos
            desde, hasta = _rango_default(d)
            self.assertEqual(hasta, d)
            self.assertEqual((hasta - desde).days + 1, 30, d)
            d += timedelta(days=1)

    def test_sin_argumento_usa_hoy_en_utc(self):
        # UTC, NO date.today(): en un servidor UTC-4 a las 23:00 locales
        # date.today() y UTC difieren en un dia.
        desde, hasta = _rango_default()
        self.assertEqual(hasta, datetime.now(timezone.utc).date())
        self.assertEqual((hasta - desde).days, 29)

    def test_hoy_no_depende_de_la_zona_local(self):
        # 2026-09-20 03:00 UTC = 2026-09-19 23:00 en UTC-4: el reporte debe
        # usar el dia UTC (20), sin importar la zona del servidor.
        instante = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        with mock.patch.object(service, "datetime") as dt:
            dt.now.return_value = instante
            self.assertEqual(service.hoy_utc(), date(2026, 9, 20))
            self.assertEqual(
                service.rango_por_defecto(), (date(2026, 8, 22), date(2026, 9, 20))
            )
            dt.now.assert_called_with(timezone.utc)  # siempre aware/UTC


class ConstruirFiltrosRangoTest(unittest.TestCase):
    # Sin categoria_id no se consulta la BD: se puede pasar db=None.
    def test_solo_fecha_fin_completa_inicio_cruzando_mes(self):
        f = construir_filtros(None, fecha_fin=date(2026, 3, 10))
        self.assertEqual((f.fecha_inicio, f.fecha_fin), (date(2026, 2, 9), date(2026, 3, 10)))

    def test_solo_fecha_fin_cruzando_anio(self):
        f = construir_filtros(None, fecha_fin=date(2026, 1, 15))
        self.assertEqual(f.fecha_inicio, date(2025, 12, 17))

    def test_sin_fechas_son_30_dias(self):
        f = construir_filtros(None)
        self.assertEqual((f.fecha_fin - f.fecha_inicio).days + 1, 30)

    def test_rango_invertido_falla(self):
        with self.assertRaises(ReporteFiltroError):
            construir_filtros(None, date(2026, 10, 1), date(2026, 9, 1))

    def test_mismo_dia_es_valido(self):
        f = construir_filtros(None, date(2026, 9, 1), date(2026, 9, 1))
        self.assertEqual(f.fecha_inicio, f.fecha_fin)

    def test_canal_se_normaliza_y_valida(self):
        self.assertEqual(construir_filtros(None, canal_venta=" pos ").canal_venta, "POS")
        with self.assertRaises(ReporteFiltroError):
            construir_filtros(None, canal_venta="TIENDA")


if __name__ == "__main__":
    unittest.main()
