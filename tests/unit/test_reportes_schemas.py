# Pruebas unitarias AISLADAS de los DTOs de reportes CU20 (sin BD).
# Comprueban que los dataclasses del service se mapean a los schemas Pydantic,
# que los importes salen como NUMERO JSON y el manejo de "sin datos".
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_reportes_schemas.py
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402
from app.modules.reportes import service as s  # noqa: E402
from app.schemas.reporte import (  # noqa: E402
    MENSAJE_SIN_DATOS,
    DevolucionesReporteResponse,
    InventarioSituacionResponse,
    ProductosMasVendidosResponse,
    VentasPeriodoResponse,
)

F = s.FiltrosReporte(date(2026, 9, 1), date(2026, 9, 2), None, "POS")


class DtoTest(unittest.TestCase):
    def test_ventas_con_datos(self):
        r = s.ReporteVentas(
            filtros=F, sin_datos=False, num_ventas=2, unidades_vendidas=3,
            ingresos_productos=Decimal("300.50"), ticket_promedio=Decimal("150.25"),
            total_facturado=Decimal("450.50"), total_envios=Decimal("150.00"),
            por_fecha=[s.PuntoDia(date(2026, 9, 1), Decimal("300.50"), 3, 2),
                       s.PuntoDia(date(2026, 9, 2), Decimal("0"), 0, 0)],
            por_categoria=[s.FilaCategoria(1, "Camisas", Decimal("300.50"), 3)],
            por_canal=[s.FilaCanal("POS", Decimal("300.50"), 2)],
            por_metodo_pago=[s.FilaMetodoPago("QR", Decimal("300.50"), 2)],
        )
        d = VentasPeriodoResponse.model_validate(r).model_dump(mode="json")
        self.assertFalse(d["sin_datos"])
        self.assertIsNone(d["mensaje"])
        self.assertEqual(d["filtros"]["canal_venta"], "POS")
        self.assertEqual(d["ingresos_productos"], 300.5)  # numero, no string
        self.assertIsInstance(d["total_facturado"], float)
        self.assertEqual(d["por_fecha"][0]["fecha"], "2026-09-01")
        self.assertEqual(len(d["por_fecha"]), 2)

    def test_ventas_sin_datos_da_mensaje_y_ceros(self):
        r = s.ReporteVentas(
            filtros=F, sin_datos=True, num_ventas=0, unidades_vendidas=0,
            ingresos_productos=Decimal("0"), ticket_promedio=Decimal("0"),
            total_facturado=Decimal("0"), total_envios=Decimal("0"),
        )
        d = VentasPeriodoResponse.model_validate(r).model_dump(mode="json")
        self.assertTrue(d["sin_datos"])
        self.assertEqual(d["mensaje"], MENSAJE_SIN_DATOS)
        self.assertEqual(d["por_fecha"], [])
        self.assertEqual(d["ingresos_productos"], 0.0)

    def test_ventas_con_filtro_categoria_omite_facturado(self):
        r = s.ReporteVentas(
            filtros=s.FiltrosReporte(date(2026, 9, 1), date(2026, 9, 2), 1, None),
            sin_datos=True, num_ventas=0, unidades_vendidas=0,
            ingresos_productos=Decimal("0"), ticket_promedio=Decimal("0"),
            total_facturado=None, total_envios=None,
        )
        d = VentasPeriodoResponse.model_validate(r).model_dump(mode="json")
        self.assertIsNone(d["total_facturado"])

    def test_top_productos(self):
        r = s.ReporteTopProductos(
            filtros=F, sin_datos=False, top=5, total_productos_vendidos=1,
            items=[s.ProductoMasVendido(1, 7, "Camisa", 2, "Camisas", 13, Decimal("2468.70"), 9)],
        )
        d = ProductosMasVendidosResponse.model_validate(r).model_dump(mode="json")
        self.assertEqual(d["items"][0]["posicion"], 1)
        self.assertEqual(d["items"][0]["total_generado"], 2468.7)

    def test_inventario_rotacion_segura_sin_stock(self):
        def item(stock, vend, rot):
            return s.ProductoInventario(1, "P", 1, "C", "Activo", stock, Decimal("10"),
                                        Decimal(10 * stock), "OK", vend, rot)
        r = s.ReporteInventario(
            filtros=F, sin_datos=False, total_productos=2, stock_total_unidades=4,
            valor_inventario=Decimal("40"), agotados=1,
            por_nivel={"CRITICO": 2, "BAJO": 0, "OK": 0}, total_filas=2, limite=200,
            items=[item(4, 2, Decimal("0.50")), item(0, 3, None)],
        )
        d = InventarioSituacionResponse.model_validate(r).model_dump(mode="json")
        con, sin = d["items"]
        self.assertEqual((con["rotacion"], con["rotacion_disponible"]), (0.5, True))
        self.assertEqual((sin["rotacion"], sin["rotacion_disponible"]), (None, False))
        self.assertEqual(set(d["por_nivel"]), {"CRITICO", "BAJO", "OK"})

    def test_devoluciones(self):
        r = s.ReporteDevoluciones(
            filtros=F, estado="COMPLETADA", sin_datos=False, num_devoluciones=1,
            unidades_devueltas=1, importe_total=Decimal("189.90"),
            importe_completado=Decimal("189.90"),
            por_estado=[s.DevolucionesPorEstado("COMPLETADA", 1, 1, Decimal("189.90"))],
            detalle=[s.DevolucionDetalle(1, datetime(2026, 9, 1, tzinfo=timezone.utc),
                                         "COMPLETADA", "ATT-1", "Defecto", 1, Decimal("189.90"))],
        )
        d = DevolucionesReporteResponse.model_validate(r).model_dump(mode="json")
        self.assertEqual(d["estado"], "COMPLETADA")
        self.assertEqual(d["importe_total"], 189.9)
        self.assertEqual(d["detalle"][0]["codigo_venta"], "ATT-1")

    def test_devoluciones_estado_invalido_no_pasa_el_dto(self):
        r = s.ReporteDevoluciones(
            filtros=F, estado=None, sin_datos=True, num_devoluciones=0,
            unidades_devueltas=0, importe_total=Decimal("0"), importe_completado=Decimal("0"),
            por_estado=[s.DevolucionesPorEstado("INVENTADO", 1, 1, Decimal("1"))],
        )
        with self.assertRaises(Exception):
            DevolucionesReporteResponse.model_validate(r)


if __name__ == "__main__":
    unittest.main()
