# Pruebas unitarias AISLADAS de CU19 fase 5 (schemas + zonas_service).
# Sin base de datos: la sesion es un Mock y las entidades son objetos en
# memoria. Los comportamientos reales (UNIQUE, FK, cascadas, JWT) se prueban en
# tests/integration/test_agencias_zonas_*.py contra la BD local.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_zonas_service.py
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.modules.delivery import zonas_service as svc  # noqa: E402
from app.modules.delivery.models import AgenciaReparto, AgenciaTarifa, AgenciaZona  # noqa: E402
from app.modules.empresa.models import Ciudad  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia_zona import ZonaCreate, ZonaUpdate  # noqa: E402


def usuario(rol):
    return Usuario(id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
                   estado=True, rol=Rol(nombre_rol=rol) if rol else None)


def agencia(activa=True):
    return AgenciaReparto(id_agencia=7, razon_social="Andes Express", nit="1020304050",
                          correo_facturacion="f@andes-express.com", direccion_fiscal="Av 1", is_active=activa)


def ciudad(i=1, nombre="La Paz", depto="La Paz"):
    return Ciudad(id=i, nombre=nombre, departamento=depto)


def zona(id_zona=3, c=None, sub=None):
    c = c or ciudad()
    return AgenciaZona(id_zona=id_zona, id_agencia=7, id_ciudad=c.id, nombre_zona=sub, ciudad=c,
                       fecha_creacion=datetime.now(timezone.utc))


class Diag:
    def __init__(self, n):
        self.constraint_name = n


class Orig(Exception):
    def __init__(self, n):
        super().__init__(n)
        self.diag = Diag(n)


def integrity(n):
    return IntegrityError("x", {}, Orig(n))


class NormalizacionCiudad(unittest.TestCase):
    def test_ignora_mayusculas_tildes_y_espacios(self):
        for v in ("Potosí", "potosi", "POTOSÍ", "  Potosi ", "PoToSí", "Potosí\t"):
            self.assertEqual(svc.normalizar_nombre_ciudad(v), "potosi", v)
        self.assertEqual(svc.normalizar_nombre_ciudad("Santa   Cruz de la  Sierra"), "santa cruz de la sierra")

    def test_vacios(self):
        for v in (None, "", "   "):
            self.assertEqual(svc.normalizar_nombre_ciudad(v), "")

    def test_no_confunde_nombres_distintos(self):
        self.assertNotEqual(svc.normalizar_nombre_ciudad("La Paz"), svc.normalizar_nombre_ciudad("El Alto"))


class NormalizacionSubzona(unittest.TestCase):
    def test_guardar_recorta_y_colapsa_pero_conserva_forma(self):
        self.assertEqual(svc.normalizar_subzona("  Zona   Sur "), "Zona Sur")
        self.assertEqual(svc.normalizar_subzona("Sopocachi"), "Sopocachi")

    def test_vacia_es_none(self):
        for v in (None, "", "   "):
            self.assertIsNone(svc.normalizar_subzona(v))

    def test_sin_letras_ni_numeros_es_422(self):
        for v in ("---", "..", "#"):
            with self.subTest(v), self.assertRaises(HTTPException) as cm:
                svc.normalizar_subzona(v)
            self.assertEqual(cm.exception.status_code, 422)

    def test_clave_de_comparacion_ignora_mayusculas_pero_no_tildes(self):
        self.assertEqual(svc.clave_subzona("ZONA SUR"), svc.clave_subzona("zona sur"))
        self.assertEqual(svc.clave_subzona(None), "")
        self.assertEqual(svc.clave_subzona("   "), "")
        self.assertNotEqual(svc.clave_subzona("Sopocachí"), svc.clave_subzona("Sopocachi"))


class Schemas(unittest.TestCase):
    def test_crear_exige_una_ciudad(self):
        with self.assertRaises(ValidationError):
            ZonaCreate()
        with self.assertRaises(ValidationError):
            ZonaCreate(nombre_zona="Centro")
        with self.assertRaises(ValidationError):
            ZonaCreate(ciudad="   ")

    def test_no_ambas_formas_de_ciudad(self):
        with self.assertRaises(ValidationError):
            ZonaCreate(id_ciudad=1, ciudad="La Paz")
        with self.assertRaises(ValidationError):
            ZonaUpdate(id_ciudad=1, ciudad="La Paz")

    def test_validos_y_recortes(self):
        self.assertEqual(ZonaCreate(id_ciudad=2).nombre_zona, None)
        z = ZonaCreate(ciudad="  La Paz ", nombre_zona="  ")
        self.assertEqual((z.ciudad, z.nombre_zona), ("La Paz", None))
        self.assertEqual(ZonaCreate(id_ciudad=2, nombre_zona=" Centro ").nombre_zona, "Centro")

    def test_id_ciudad_positivo_y_largos(self):
        for malo in (0, -1):
            with self.assertRaises(ValidationError):
                ZonaCreate(id_ciudad=malo)
        with self.assertRaises(ValidationError):
            ZonaCreate(id_ciudad=1, nombre_zona="x" * 101)
        with self.assertRaises(ValidationError):
            ZonaCreate(ciudad="x" * 101)

    def test_update_vacio_es_valido_en_el_schema(self):
        self.assertEqual(ZonaUpdate().model_dump(exclude_none=True), {})


class ResolverCiudad(unittest.TestCase):
    def _db(self, filas):
        db = mock.Mock()
        db.query.return_value.all.return_value = [(c.id, c.nombre) for c in filas]
        db.get.side_effect = lambda _m, i: next((c for c in filas if c.id == i), None)
        return db

    def test_unica_por_nombre_normalizado(self):
        db = self._db([ciudad(1, "Santa Cruz de la Sierra"), ciudad(2, "La Paz"), ciudad(8, "Potosí")])
        for n in ("potosi", "POTOSÍ", " Potosi "):
            self.assertEqual(svc.resolver_ciudad(db, n).id, 8)

    def test_inexistente_es_none(self):
        self.assertIsNone(svc.resolver_ciudad(self._db([ciudad(2)]), "Sucre"))
        self.assertEqual(svc.buscar_ciudades_por_nombre(self._db([ciudad(2)]), "  "), [])

    def test_ambigua_es_none_y_buscar_lista_todas(self):
        db = self._db([ciudad(1, "Santa Cruz", "Santa Cruz"), ciudad(11, "SANTA  CRUZ", "Otro"), ciudad(2)])
        self.assertIsNone(svc.resolver_ciudad(db, "santa cruz"))
        self.assertEqual([c.id for c in svc.buscar_ciudades_por_nombre(db, "Santa Cruz")], [1, 11])
        self.assertEqual(svc.resolver_ciudad(db, "La Paz").id, 2)

    def test_del_payload_por_nombre_ambiguo_409_sin_existir_404(self):
        db = self._db([ciudad(1, "Santa Cruz"), ciudad(11, "santa cruz"), ciudad(2)])
        with self.assertRaises(HTTPException) as cm:
            svc._ciudad_del_payload(db, ZonaCreate(ciudad="SANTA CRUZ"))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("id_ciudad", cm.exception.detail)
        with self.assertRaises(HTTPException) as cm:
            svc._ciudad_del_payload(db, ZonaCreate(ciudad="Tarija"))
        self.assertEqual(cm.exception.status_code, 404)
        self.assertEqual(svc._ciudad_del_payload(db, ZonaCreate(ciudad="la paz")).id, 2)

    def test_del_payload_por_id_inexistente_404(self):
        db = self._db([ciudad(2)])
        with self.assertRaises(HTTPException) as cm:
            svc._ciudad_del_payload(db, ZonaCreate(id_ciudad=99))
        self.assertEqual(cm.exception.status_code, 404)


class Permisos(unittest.TestCase):
    ESCRITURAS = (
        lambda db, u: svc.crear_zona(db, u, 7, ZonaCreate(id_ciudad=1)),
        lambda db, u: svc.actualizar_zona(db, u, 7, 3, ZonaUpdate(nombre_zona="X")),
        lambda db, u: svc.eliminar_zona(db, u, 7, 3),
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
            for op in (lambda d: svc.listar_zonas(d, usuario(rol), 7), lambda d: svc.obtener_zona(d, usuario(rol), 7, 3)):
                db = mock.Mock()
                with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                    op(db)
                self.assertEqual(cm.exception.status_code, 403)
                self.assertEqual(db.method_calls, [])

    def test_d_lista_zonas_de_agencia_habilitada_y_no_de_deshabilitada(self):
        a = agencia()
        a.zonas = [zona(1, ciudad(2, "La Paz"), "Sur"), zona(2, ciudad(3, "Cochabamba"))]
        with mock.patch.object(svc, "obtener_agencia", return_value=a):
            self.assertEqual([z.id_zona for z in svc.listar_zonas(mock.Mock(), usuario("D"), 7)], [2, 1])
        # obtener_agencia (Fase 3) responde 404 a D si esta deshabilitada
        with mock.patch.object(svc, "obtener_agencia", side_effect=HTTPException(404, "x")):
            with self.assertRaises(HTTPException) as cm:
                svc.listar_zonas(mock.Mock(), usuario("D"), 7)
            self.assertEqual(cm.exception.status_code, 404)


class CrearZona(unittest.TestCase):
    def _crear(self, payload, agencia_=None, ciudad_=None, en_uso=False, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_ciudad_del_payload", return_value=ciudad_ or ciudad()), \
             mock.patch.object(svc, "_cobertura_en_uso", return_value=en_uso):
            return db, svc.crear_zona(db, usuario("GS"), 7, payload)

    def test_valida_sin_subzona(self):
        db, _ = self._crear(ZonaCreate(id_ciudad=1))
        z = db.add.call_args.args[0]
        self.assertEqual((z.id_agencia, z.id_ciudad, z.nombre_zona), (7, 1, None))
        db.commit.assert_called_once()

    def test_valida_con_subzona_normalizada(self):
        db, _ = self._crear(ZonaCreate(id_ciudad=1, nombre_zona="  Zona   Sur "))
        self.assertEqual(db.add.call_args.args[0].nombre_zona, "Zona Sur")

    def test_agencia_deshabilitada_400_sin_escribir(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._crear(ZonaCreate(id_ciudad=1), agencia_=agencia(activa=False), db=db)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("deshabilitada", cm.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_agencia_inexistente_404(self):
        db = mock.Mock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with self.assertRaises(HTTPException) as cm:
            svc.crear_zona(db, usuario("ASU"), 99, ZonaCreate(id_ciudad=1))
        self.assertEqual(cm.exception.status_code, 404)

    def test_duplicado_409(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._crear(ZonaCreate(id_ciudad=1, nombre_zona="Sur"), en_uso=True, db=db)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("La Paz / Sur", cm.exception.detail)
        db.add.assert_not_called()

    def test_duplicado_toda_la_ciudad_mensaje(self):
        with self.assertRaises(HTTPException) as cm:
            self._crear(ZonaCreate(id_ciudad=1), en_uso=True)
        self.assertIn("toda la ciudad", cm.exception.detail)

    def test_subzona_invalida_422(self):
        with self.assertRaises(HTTPException) as cm:
            self._crear(ZonaCreate(id_ciudad=1, nombre_zona="---"))
        self.assertEqual(cm.exception.status_code, 422)

    def test_carrera_integrity_error_409_con_rollback(self):
        db = mock.Mock()
        db.commit.side_effect = integrity(svc._CONSTRAINT_COBERTURA)
        with self.assertRaises(HTTPException) as cm:
            self._crear(ZonaCreate(id_ciudad=1), db=db)
        self.assertEqual(cm.exception.status_code, 409)
        db.rollback.assert_called_once()

    def test_orden_de_validacion_agencia_deshabilitada_antes_que_ciudad(self):
        with mock.patch.object(svc, "_buscar", return_value=agencia(activa=False)), \
             mock.patch.object(svc, "_ciudad_del_payload") as c:
            with self.assertRaises(HTTPException):
                svc.crear_zona(mock.Mock(), usuario("GS"), 7, ZonaCreate(id_ciudad=1))
        c.assert_not_called()


class TraducirIntegrityError(unittest.TestCase):
    def test_mapeo(self):
        for nombre, codigo in [(svc._CONSTRAINT_COBERTURA, 409), (svc._CONSTRAINT_FK_CIUDAD, 404),
                               (svc._CONSTRAINT_FK_AGENCIA, 404),
                               ("fk_envios_id_tarifa_aplicada_agencia_tarifas", 409), ("otra", 422)]:
            with self.subTest(nombre):
                self.assertEqual(svc.traducir_integrity_error(integrity(nombre)).status_code, codigo)


class ActualizarZona(unittest.TestCase):
    def _actualizar(self, z, payload, ciudad_=None, en_uso=False, agencia_=None, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=z), \
             mock.patch.object(svc, "_ciudad_del_payload", return_value=ciudad_), \
             mock.patch.object(svc, "_cobertura_en_uso", return_value=en_uso) as uso:
            return db, svc.actualizar_zona(db, usuario("GS"), 7, z.id_zona, payload), uso

    def test_cambia_subzona(self):
        z = zona(sub="Sur")
        db, r, _ = self._actualizar(z, ZonaUpdate(nombre_zona="  Norte  "))
        self.assertEqual(r.nombre_zona, "Norte")
        db.commit.assert_called_once()

    def test_cambia_ciudad_y_conserva_subzona(self):
        z = zona(c=ciudad(2, "La Paz"), sub="Centro")
        _, r, uso = self._actualizar(z, ZonaUpdate(id_ciudad=3), ciudad_=ciudad(3, "Cochabamba"))
        self.assertEqual((r.id_ciudad, r.nombre_zona), (3, "Centro"))
        uso.assert_called_once()
        self.assertEqual(uso.call_args.args[-1], z.id_zona)  # no choca consigo misma

    def test_duplicado_409_y_no_modifica(self):
        z = zona(c=ciudad(2), sub="Centro")
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(z, ZonaUpdate(nombre_zona="Sur"), en_uso=True)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(z.nombre_zona, "Centro")

    def test_sin_cambio_real_no_hace_commit_ni_valida_duplicado(self):
        z = zona(c=ciudad(2), sub="Centro")
        db, _, uso = self._actualizar(z, ZonaUpdate(nombre_zona="Centro"))
        uso.assert_not_called()
        db.commit.assert_not_called()

    def test_cambio_solo_de_forma_se_guarda_sin_duplicado(self):
        z = zona(c=ciudad(2), sub="centro")
        db, r, uso = self._actualizar(z, ZonaUpdate(nombre_zona="CENTRO"))
        self.assertEqual(r.nombre_zona, "CENTRO")
        uso.assert_not_called()
        db.commit.assert_called_once()

    def test_agencia_deshabilitada_400(self):
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(zona(), ZonaUpdate(nombre_zona="X"), agencia_=agencia(activa=False))
        self.assertEqual(cm.exception.status_code, 400)

    def test_body_vacio_400_antes_de_la_bd(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_zona(db, usuario("GS"), 7, 3, ZonaUpdate())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(db.method_calls, [])

    def test_zona_de_otra_agencia_404(self):
        db = mock.Mock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with mock.patch.object(svc, "_buscar", return_value=agencia()):
            with self.assertRaises(HTTPException) as cm:
                svc.actualizar_zona(db, usuario("GS"), 7, 3, ZonaUpdate(nombre_zona="X"))
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("no tiene una zona", cm.exception.detail)

    def test_ciudad_nueva_inexistente_404(self):
        with mock.patch.object(svc, "_buscar", return_value=agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=zona()):
            db = mock.Mock()
            db.get.return_value = None
            with self.assertRaises(HTTPException) as cm:
                svc.actualizar_zona(db, usuario("GS"), 7, 3, ZonaUpdate(id_ciudad=99))
        self.assertEqual(cm.exception.status_code, 404)

    def test_integrity_error_al_confirmar(self):
        db = mock.Mock()
        db.commit.side_effect = integrity(svc._CONSTRAINT_FK_CIUDAD)
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(zona(sub="A"), ZonaUpdate(nombre_zona="B"), db=db)
        self.assertEqual(cm.exception.status_code, 404)
        db.rollback.assert_called_once()


class EliminarZona(unittest.TestCase):
    def _eliminar(self, z, agencia_=None, db=None):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=agencia_ or agencia()), \
             mock.patch.object(svc, "_buscar_zona", return_value=z):
            return db, svc.eliminar_zona(db, usuario("GS"), 7, z.id_zona)

    def test_sin_tarifas_elimina(self):
        z = zona(sub="Sur")
        db, desc = self._eliminar(z)
        self.assertEqual(desc, "La Paz / Sur")
        db.delete.assert_called_once_with(z)
        db.commit.assert_called_once()

    def test_con_tarifas_409_sin_cascada(self):
        z = zona()
        z.tarifas = [AgenciaTarifa(criterio="PESO"), AgenciaTarifa(criterio="VOLUMEN")]
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._eliminar(z, db=db)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("2 tarifa", cm.exception.detail)
        db.delete.assert_not_called()

    def test_permitido_con_agencia_deshabilitada(self):
        db, _ = self._eliminar(zona(), agencia_=agencia(activa=False))
        db.delete.assert_called_once()

    def test_fk_de_envios_es_409(self):
        db = mock.Mock()
        db.commit.side_effect = integrity("fk_envios_id_tarifa_aplicada_agencia_tarifas")
        with self.assertRaises(HTTPException) as cm:
            self._eliminar(zona(), db=db)
        self.assertEqual(cm.exception.status_code, 409)
        db.rollback.assert_called_once()


class Serializacion(unittest.TestCase):
    def test_sin_datos_de_agencia_ni_facturacion(self):
        d = svc.serializar_zona(zona(sub="Sur"))
        self.assertEqual(set(d), {"id_zona", "id_agencia", "id_ciudad", "ciudad", "departamento",
                                  "nombre_zona", "total_tarifas", "fecha_creacion"})
        self.assertEqual((d["ciudad"], d["nombre_zona"], d["total_tarifas"]), ("La Paz", "Sur", 0))


if __name__ == "__main__":
    unittest.main()
