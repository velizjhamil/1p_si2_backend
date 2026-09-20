# Pruebas unitarias de CU19 fase 2 (modelos + cadena de migraciones).
# Sin base de datos: solo inspeccionan el metadata de SQLAlchemy y los scripts
# de Alembic, asi que no tocan Supabase ni ninguna BD.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_modelos.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import CheckConstraint  # noqa: E402
from sqlalchemy.orm import configure_mappers  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.modules.delivery.models import (  # noqa: E402
    CRITERIOS_TARIFA,
    AgenciaReparto,
    AgenciaTarifa,
    AgenciaZona,
    Envio,
)

BACKEND = Path(__file__).resolve().parents[2]
REV_CU19 = "d5f9b3a7c1e2"
REV_CU18 = "c4e8a2f6b9d3"
COLUMNAS_ENVIO_CU19 = (
    "id_agencia",
    "id_tarifa_aplicada",
    "costo_agencia",
    "peso_kg",
    "volumen_m3",
)


def _tabla(nombre):
    return Base.metadata.tables[nombre]


def _checks(tabla) -> dict[str, str]:
    return {
        c.name: str(c.sqltext)
        for c in tabla.constraints
        if isinstance(c, CheckConstraint)
    }


class MapeoModelos(unittest.TestCase):
    def test_mappers_configuran(self):
        configure_mappers()

    def test_tablas_registradas(self):
        for nombre in ("agencias_reparto", "agencia_zonas", "agencia_tarifas"):
            self.assertIn(nombre, Base.metadata.tables)

    def test_agencia_datos_de_facturacion_obligatorios(self):
        t = _tabla("agencias_reparto")
        for col in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            self.assertFalse(t.c[col].nullable, col)
        for col in ("contacto_operativo", "telefono", "correo", "direccion"):
            self.assertTrue(t.c[col].nullable, col)
        self.assertFalse(t.c.is_active.nullable)

    def test_agencia_es_global_sin_sucursal(self):
        self.assertNotIn("codigo_sucursal", _tabla("agencias_reparto").c)
        self.assertNotIn("sucursal_id", _tabla("agencias_reparto").c)

    def test_indices_unicos_de_agencia_y_zona(self):
        unicos = {i.name for i in _tabla("agencias_reparto").indexes if i.unique}
        self.assertIn("ix_agencias_reparto_nit", unicos)
        self.assertIn("uq_agencias_reparto_razon_social_lower", unicos)
        unicos_z = {i.name for i in _tabla("agencia_zonas").indexes if i.unique}
        self.assertIn("uq_agencia_zonas_cobertura", unicos_z)

    def test_zona_referencia_ciudad_y_nombre_zona_opcional(self):
        t = _tabla("agencia_zonas")
        self.assertFalse(t.c.id_ciudad.nullable)
        self.assertTrue(t.c.nombre_zona.nullable)
        destinos = {fk.column.table.name for fk in t.foreign_keys}
        self.assertEqual(destinos, {"agencias_reparto", "ciudades"})

    def test_tarifa_campos_y_checks(self):
        t = _tabla("agencia_tarifas")
        self.assertTrue(t.c.rango_max.nullable)  # tramo abierto
        self.assertTrue(t.c.vigente_hasta.nullable)
        self.assertFalse(t.c.vigente_desde.nullable)
        self.assertFalse(t.c.costo.nullable)
        self.assertEqual(CRITERIOS_TARIFA, ("PESO", "VOLUMEN"))
        self.assertEqual(
            set(_checks(t)),
            {
                "ck_agencia_tarifas_criterio_valido",
                "ck_agencia_tarifas_rango_min_no_negativo",
                "ck_agencia_tarifas_rango_max_mayor_que_min",
                "ck_agencia_tarifas_costo_no_negativo",
                "ck_agencia_tarifas_vigencia_valida",
            },
        )

    def test_no_hay_exclude_ni_extension(self):
        # Decision: solapamiento validado en el service, sin EXCLUDE/gist.
        for nombre in ("agencias_reparto", "agencia_zonas", "agencia_tarifas"):
            for c in _tabla(nombre).constraints:
                self.assertNotIn("Exclude", type(c).__name__)


class EnvioCompatibilidadCU18(unittest.TestCase):
    def test_columnas_nuevas_son_nullable(self):
        t = _tabla("envios")
        for col in COLUMNAS_ENVIO_CU19:
            self.assertTrue(t.c[col].nullable, col)
            self.assertIsNone(t.c[col].default, col)

    def test_fks_de_agencia_restrict(self):
        t = _tabla("envios")
        fks = {fk.parent.name: fk for fk in t.foreign_keys}
        self.assertEqual(fks["id_agencia"].column.table.name, "agencias_reparto")
        self.assertEqual(fks["id_tarifa_aplicada"].column.table.name, "agencia_tarifas")
        for nombre in ("id_agencia", "id_tarifa_aplicada"):
            self.assertIsNone(fks[nombre].ondelete, nombre)  # RESTRICT

    def test_checks_cu18_intactos_y_cu19_agregados(self):
        checks = set(_checks(_tabla("envios")))
        cu18 = {
            "ck_envios_estado_envio_valido",
            "ck_envios_intentos_no_negativos",
            "ck_envios_sucursal_requerida_al_despachar",
        }
        cu19 = {
            "ck_envios_repartidor_o_agencia",
            "ck_envios_agencia_snapshot_completo",
            "ck_envios_datos_agencia_requieren_agencia",
            "ck_envios_costo_agencia_no_negativo",
        }
        self.assertTrue(cu18 <= checks)
        self.assertTrue(cu19 <= checks)

    def test_columnas_cu18_sin_cambios(self):
        t = _tabla("envios")
        self.assertFalse(t.c.id_venta.nullable)
        self.assertFalse(t.c.estado.nullable)
        self.assertTrue(t.c.id_repartidor.nullable)
        self.assertTrue(t.c.codigo_sucursal.nullable)

    def test_envio_se_construye_como_en_cu18(self):
        e = Envio(id_venta=1, estado="PREPARANDO", intentos_fallidos=0)
        self.assertIsNone(e.id_agencia)
        self.assertIsNone(e.costo_agencia)
        self.assertIsNone(e.peso_kg)
        self.assertIsNone(e.volumen_m3)
        self.assertIsNone(e.id_tarifa_aplicada)

    def test_relaciones_de_agencia(self):
        a = AgenciaReparto(razon_social="X", nit="1", correo_facturacion="a@b.c", direccion_fiscal="d")
        z = AgenciaZona(id_ciudad=1)
        a.zonas.append(z)
        z.tarifas.append(AgenciaTarifa(criterio="PESO"))
        self.assertIs(z.agencia, a)
        self.assertIs(z.tarifas[0].zona, z)


class CadenaDeMigraciones(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        cls.script = ScriptDirectory.from_config(cfg)

    def test_una_sola_cabeza_y_es_cu19(self):
        self.assertEqual(self.script.get_heads(), [REV_CU19])

    def test_cu19_cuelga_de_cu18(self):
        self.assertEqual(self.script.get_revision(REV_CU19).down_revision, REV_CU18)

    def test_cu18_no_fue_modificada(self):
        self.assertEqual(self.script.get_revision(REV_CU18).down_revision, "b7c3d9e1f2a4")


if __name__ == "__main__":
    unittest.main()
