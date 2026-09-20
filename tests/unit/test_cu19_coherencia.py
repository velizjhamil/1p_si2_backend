# Pruebas de COHERENCIA de CU19 (fase 10, auditoria). Sin base de datos: leen el
# codigo fuente, el esquema OpenAPI y las constantes para fijar garantias que la
# auditoria verifico a mano, de modo que no se puedan romper sin que un test lo diga.
#   - una sola fuente de verdad para cada regla de negocio;
#   - los roles del router y del service no pueden divergir;
#   - inventario exacto de rutas (sin endpoints muertos ni duplicados);
#   - ningun archivo fuente con bytes NUL (un heredoc los colo una vez);
#   - los comentarios no prometen "fases siguientes" ya cumplidas.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_cu19_coherencia.py
import ast
import collections
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from app.api.v1.endpoints import agencias as router_agencias  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.delivery import (  # noqa: E402
    agencias_service,
    asignacion_agencia_service,
    cotizacion_service,
    disponibilidad_service,
    tarifas_service,
    zonas_service,
)
from app.modules.delivery import service as cu18  # noqa: E402

DELIVERY = ROOT / "app" / "modules" / "delivery"
CU19_MODULOS = ("agencias_service", "zonas_service", "tarifas_service", "disponibilidad_service",
                "cotizacion_service", "asignacion_agencia_service")
CU19_ENDPOINTS = ("agencias", "agencia_zonas", "agencia_tarifas")
CU19_SCHEMAS = ("agencia", "agencia_zona", "agencia_tarifa", "agencia_disponible", "agencia_cotizacion")
NUL = bytes([0])

BASE = "/api/v1/agencias-reparto"
RUTAS_ESPERADAS = {
    BASE: {"GET", "POST"},
    BASE + "/": {"GET", "POST"},
    BASE + "/disponibles": {"GET"},
    BASE + "/{id_agencia}": {"GET", "PUT", "DELETE"},
    BASE + "/{id_agencia}/cotizacion": {"GET"},
    BASE + "/{id_agencia}/estado": {"PATCH"},
    BASE + "/{id_agencia}/zonas": {"GET", "POST"},
    BASE + "/{id_agencia}/zonas/": {"GET", "POST"},
    BASE + "/{id_agencia}/zonas/{id_zona}": {"GET", "PUT", "DELETE"},
    BASE + "/{id_agencia}/zonas/{id_zona}/tarifas": {"GET", "POST"},
    BASE + "/{id_agencia}/zonas/{id_zona}/tarifas/": {"GET", "POST"},
    BASE + "/{id_agencia}/zonas/{id_zona}/tarifas/{id_tarifa}": {"GET", "PUT", "DELETE"},
}


def fuente(nombre: str) -> str:
    return (DELIVERY / f"{nombre}.py").read_text(encoding="utf-8")


def definiciones(nombre: str) -> set[str]:
    return {n.name for n in ast.parse(fuente(nombre)).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


class SinBytesNulEnElCodigo(unittest.TestCase):
    def test_ningun_fuente_contiene_un_byte_nul(self):
        malos = [str(p.relative_to(ROOT)) for base in ("app", "tests") for p in (ROOT / base).rglob("*.py") if NUL in p.read_bytes()]
        self.assertEqual(malos, [])


class InventarioDeRutas(unittest.TestCase):
    def test_rutas_de_cu19_exactas(self):
        paths = app.openapi()["paths"]
        actuales = {p: {m.upper() for m in ops} for p, ops in paths.items() if p.startswith(BASE)}
        self.assertEqual(actuales, RUTAS_ESPERADAS)

    def test_la_asignacion_es_el_patch_existente_de_cu18(self):
        paths = app.openapi()["paths"]
        asignar = [p for p in paths if p.endswith("/asignar")]
        self.assertEqual(asignar, ["/api/v1/envios/{id_envio}/asignar"])
        self.assertEqual({m.upper() for m in paths[asignar[0]]}, {"PATCH"})

    def test_sin_operation_id_duplicados(self):
        ids = collections.Counter(op.get("operationId") for p in app.openapi()["paths"].values() for op in p.values())
        self.assertEqual({k: v for k, v in ids.items() if v > 1}, {})

    def test_cada_ruta_declarada_en_los_routers_esta_registrada(self):
        """Ninguna funcion decorada como ruta queda fuera del inventario (endpoint muerto)."""
        declaradas = 0
        for modulo in CU19_ENDPOINTS:
            arbol = ast.parse((ROOT / "app" / "api" / "v1" / "endpoints" / f"{modulo}.py").read_text(encoding="utf-8"))
            for fn in [n for n in arbol.body if isinstance(n, ast.FunctionDef)]:
                metodos = [d.func.attr for d in fn.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("get", "post", "put", "patch", "delete")]
                if metodos:
                    declaradas += len(metodos)
                    cuerpo = ast.unparse(fn)
                    self.assertIn("_envelope(", cuerpo, f"{modulo}.{fn.name} no responde con el envelope estandar")
        self.assertEqual(declaradas, sum(len(m) for p, m in RUTAS_ESPERADAS.items()))


class RolesRouterYService(unittest.TestCase):
    """Los routers hardcodean los roles en require_roles; no pueden divergir de las constantes del service."""

    def roles(self, dependencia):
        return inspect.getclosurevars(dependencia).nonlocals["roles"]

    def test_escritura_es_asu_gs(self):
        self.assertEqual(tuple(self.roles(router_agencias._solo_admin)), tuple(cu18.ROLES_ADMIN_ENVIO))

    def test_lectura_es_asu_gs_d(self):
        self.assertEqual(tuple(self.roles(router_agencias._lectura)), (*cu18.ROLES_ADMIN_ENVIO, cu18.ROL_REPARTIDOR))

    def test_zonas_y_tarifas_reutilizan_las_mismas_dependencias(self):
        from app.api.v1.endpoints import agencia_tarifas, agencia_zonas

        for modulo in (agencia_zonas, agencia_tarifas):
            self.assertIs(modulo._solo_admin, router_agencias._solo_admin)
            self.assertIs(modulo._lectura, router_agencias._lectura)
            self.assertIs(modulo._envelope, router_agencias._envelope)

    def test_el_service_usa_las_mismas_constantes_de_cu18(self):
        self.assertIs(agencias_service.ROLES_ADMIN_ENVIO, cu18.ROLES_ADMIN_ENVIO)
        self.assertIs(agencias_service.ROL_REPARTIDOR, cu18.ROL_REPARTIDOR)


class UnaSolaFuenteDeVerdad(unittest.TestCase):
    def solo_en(self, nombre: str, esperado: str):
        donde = [m for m in CU19_MODULOS if nombre in definiciones(m)]
        self.assertEqual(donde, [esperado], f"'{nombre}' debe definirse solo en {esperado}: {donde}")

    def test_seleccion_de_tarifa_solo_en_cotizacion(self):
        for nombre in ("seleccionar", "candidatas", "Candidata", "rango_contiene"):
            self.solo_en(nombre, "cotizacion_service")

    def test_solapamiento_y_vigencia_solo_en_tarifas(self):
        for nombre in ("rangos_se_intersectan", "vigencias_se_intersectan", "tarifas_se_solapan", "vigente_en", "fecha_hoy", "encontrar_solapada"):
            self.solo_en(nombre, "tarifas_service")

    def test_normalizacion_de_ciudad_solo_en_zonas(self):
        for nombre in ("normalizar_nombre_ciudad", "buscar_ciudades_por_nombre", "normalizar_subzona"):
            self.solo_en(nombre, "zonas_service")
        self.assertEqual([m for m in CU19_MODULOS if "import unicodedata" in fuente(m)], ["zonas_service"])

    def test_validacion_de_facturacion_solo_en_agencias(self):
        for nombre in ("normalizar_nit", "validar_nit", "validar_razon_social", "validar_correo", "validar_datos_facturacion"):
            self.solo_en(nombre, "agencias_service")

    def test_cotizacion_y_asignacion_reutilizan_en_vez_de_copiar(self):
        self.assertIs(cotizacion_service.fecha_hoy, tarifas_service.fecha_hoy)
        self.assertIs(cotizacion_service.vigente_en, tarifas_service.vigente_en)
        self.assertIs(cotizacion_service.resolver_ciudad_consultada, disponibilidad_service.resolver_ciudad_consultada)
        self.assertIs(disponibilidad_service.buscar_ciudades_por_nombre, zonas_service.buscar_ciudades_por_nombre)
        self.assertIs(asignacion_agencia_service.cotizar, cotizacion_service.cotizar)
        self.assertIs(asignacion_agencia_service.validar_dimensiones, cotizacion_service.validar_dimensiones)

    def test_una_agencia_deshabilitada_se_decide_en_un_solo_lugar_por_flujo(self):
        # zonas/tarifas comparten _agencia_para_escritura; cotizacion/asignacion, _agencia_cotizable (via cotizar)
        self.assertIs(tarifas_service._agencia_para_escritura, zonas_service._agencia_para_escritura)
        self.assertNotIn("is_active", inspect.getsource(asignacion_agencia_service.asignar_agencia))

    def test_resolver_ciudad_se_deriva_de_la_busqueda_unica(self):
        self.assertIn("buscar_ciudades_por_nombre", inspect.getsource(zonas_service.resolver_ciudad))

    def test_nombres_de_schemas_de_cu19_no_se_repiten_en_otros_modulos(self):
        cuentas = collections.Counter()
        for p in (ROOT / "app" / "schemas").glob("*.py"):
            for n in ast.parse(p.read_text(encoding="utf-8")).body:
                if isinstance(n, ast.ClassDef):
                    cuentas[n.name] += 1
        cu19 = {n.name for s in CU19_SCHEMAS for n in ast.parse((ROOT / "app" / "schemas" / f"{s}.py").read_text(encoding="utf-8")).body if isinstance(n, ast.ClassDef)}
        self.assertEqual({n: c for n, c in cuentas.items() if n in cu19 and c > 1}, {})


class EntradasConNul(unittest.TestCase):
    """Correccion de la auditoria: un NUL en texto libre es 422 y no un 500 de PostgreSQL."""

    def test_schemas_de_agencia_y_zona(self):
        from pydantic import ValidationError

        from app.schemas.agencia import AgenciaCreate, AgenciaUpdate
        from app.schemas.agencia_zona import ZonaCreate, ZonaUpdate

        base = dict(razon_social="Andes Express", nit="1020304050", correo_facturacion="f@andes-express.com", direccion_fiscal="Av Fiscal 1")
        for campo in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal", "contacto_operativo", "telefono", "correo", "direccion"):
            for valor in ("ab" + chr(0) + "cd", chr(0) + "ab", "ab" + chr(0), chr(0)):
                with self.subTest(campo=campo, valor=valor), self.assertRaises(ValidationError):
                    AgenciaCreate(**{**base, campo: valor})
                with self.subTest(update=campo, valor=valor), self.assertRaises(ValidationError):
                    AgenciaUpdate(**{campo: valor})
        for valor in ("a" + chr(0) + "b", chr(0)):
            with self.assertRaises(ValidationError):
                ZonaCreate(id_ciudad=1, nombre_zona=valor)
            with self.assertRaises(ValidationError):
                ZonaCreate(ciudad=valor)
            with self.assertRaises(ValidationError):
                ZonaUpdate(nombre_zona=valor)
        self.assertEqual(ZonaCreate(id_ciudad=1, nombre_zona=" Sur ").nombre_zona, "Sur")     # lo valido no cambia

    def test_observacion_de_los_payloads_de_envio(self):
        from pydantic import ValidationError

        from app.schemas.envio import (
            AsignarEnvioPayload,
            CambiarEstadoPayload,
            ConfirmarPreparacionPayload,
            EnvioCreatePayload,
            IntentoFallidoPayload,
            ReprogramarEnvioPayload,
        )
        from datetime import datetime, timezone

        sucio = "a" + chr(0) + "b"
        casos = [
            lambda: AsignarEnvioPayload(id_agencia=1, peso_kg="1", volumen_m3="1", observacion=sucio),
            lambda: AsignarEnvioPayload(id_repartidor="00000000-0000-0000-0000-000000000001", observacion=sucio),
            lambda: CambiarEstadoPayload(estado="EN_RUTA", observacion=sucio),
            lambda: ConfirmarPreparacionPayload(codigo_sucursal=1, observacion=sucio),
            lambda: EnvioCreatePayload(id_venta=1, observacion=sucio),
            lambda: IntentoFallidoPayload(motivo="cliente ausente", observacion=sucio),
            lambda: ReprogramarEnvioPayload(nueva_fecha_entrega=datetime.now(timezone.utc), observacion=sucio),
        ]
        for i, crear in enumerate(casos):
            with self.subTest(caso=i), self.assertRaises(ValidationError):
                crear()
        self.assertEqual(CambiarEstadoPayload(estado="EN_RUTA", observacion="  ok  ").observacion, "ok")

    def test_filtro_q_del_listado_422_despues_de_la_autorizacion(self):
        from unittest import mock

        from fastapi import HTTPException

        from app.modules.usuarios.models import Rol, Usuario

        def usuario(rol):
            return Usuario(nombre="N", apellido="A", correo="x@andes-express.com", estado=True, rol=Rol(nombre_rol=rol))

        for rol in ("ASU", "GS", "D"):
            db = mock.Mock()
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                agencias_service.listar_agencias(db, usuario(rol), q="a" + chr(0) + "b")
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual(db.method_calls, [])                       # nunca llega a la BD
        for rol in ("V", "C"):
            with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                agencias_service.listar_agencias(mock.Mock(), usuario(rol), q="a" + chr(0) + "b")
            self.assertEqual(cm.exception.status_code, 403)             # el 403 va primero

    def test_page_tiene_tope_tecnico(self):
        spec = app.openapi()["paths"][BASE]["get"]["parameters"]
        page = next(p for p in spec if p["name"] == "page")
        self.assertEqual((page["schema"]["minimum"], page["schema"]["maximum"]), (1, 100000))


class ComentariosAlDia(unittest.TestCase):
    OBSOLETOS = ("fases siguientes", "fase siguiente", "llegan en fases", "llega en fases", "llegara en", "sera implementad")

    def test_ningun_comentario_promete_lo_ya_implementado(self):
        malos = []
        archivos = [DELIVERY / f"{m}.py" for m in CU19_MODULOS]
        archivos += [ROOT / "app" / "api" / "v1" / "endpoints" / f"{m}.py" for m in CU19_ENDPOINTS]
        archivos += [ROOT / "app" / "schemas" / f"{s}.py" for s in CU19_SCHEMAS]
        for p in archivos:
            texto = p.read_text(encoding="utf-8").lower()
            malos += [f"{p.name}: '{frase}'" for frase in self.OBSOLETOS if frase in texto]
        self.assertEqual(malos, [])


if __name__ == "__main__":
    unittest.main()
