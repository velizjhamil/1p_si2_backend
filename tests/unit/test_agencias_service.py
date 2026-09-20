# Pruebas unitarias AISLADAS de CU19 fase 3 (schemas + agencias_service).
# Sin base de datos: la sesion es un Mock y las agencias son objetos en
# memoria, asi que NO tocan Supabase ni ninguna BD. Los duplicados REALES
# (mayusculas/espacios, carreras) se prueban en
# tests/integration/test_agencias_service_db.py contra la BD local.
# Ejecutar desde la raiz del backend:
#   python tests/unit/test_agencias_service.py
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.main  # noqa: F401,E402  (registra todos los mappers)
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.modules.delivery import agencias_service as svc  # noqa: E402
from app.modules.delivery.models import AgenciaReparto  # noqa: E402
from app.modules.usuarios.models import Rol, Usuario  # noqa: E402
from app.schemas.agencia import (  # noqa: E402
    AgenciaCreate,
    AgenciaEstadoPayload,
    AgenciaUpdate,
)


def usuario(rol: str | None) -> Usuario:
    return Usuario(
        id_usuario=uuid.uuid4(), nombre="N", apellido="A", correo=f"{rol}@x.test",
        estado=True, rol=Rol(nombre_rol=rol) if rol else None,
    )


def payload(**extra) -> AgenciaCreate:
    datos = dict(
        razon_social="Andes Express S.R.L.", nit="1020304050",
        correo_facturacion="Facturas@Andes-Express.com", direccion_fiscal="Av. Fiscal 123",
    )
    datos.update(extra)
    return AgenciaCreate(**datos)


def agencia(**extra) -> AgenciaReparto:
    datos = dict(
        id_agencia=7, razon_social="Andes Express", nit="1020304050",
        correo_facturacion="f@andes-express.com", direccion_fiscal="Av. Fiscal 1", is_active=True,
    )
    datos.update(extra)
    return AgenciaReparto(**datos)


class Diag:
    def __init__(self, nombre):
        self.constraint_name = nombre


class OrigFake(Exception):
    def __init__(self, nombre):
        super().__init__(f"violates {nombre}")
        self.diag = Diag(nombre)


def integrity(nombre) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, OrigFake(nombre))


# ---------------------------------------------------------------------------
class NormalizacionNit(unittest.TestCase):
    def test_quita_separadores(self):
        for crudo in ("1020304050", "1.020.304-050", " 1020 3040 50 ", "1020/304-050"):
            self.assertEqual(svc.normalizar_nit(crudo), "1020304050", crudo)

    def test_validar_devuelve_normalizado(self):
        self.assertEqual(svc.validar_nit("102-030.405 0"), "1020304050")

    def test_rechaza_letras_simbolos_y_digitos_no_ascii(self):
        for malo in ("10203A4050", "1020#304", "١٢٣٤٥٦٧", "ABCDEFG"):
            with self.subTest(malo), self.assertRaises(HTTPException) as cm:
                svc.validar_nit(malo)
            self.assertEqual(cm.exception.status_code, 422)

    def test_rechaza_largo_fuera_de_rango(self):
        for malo in ("1234", "1" * 21):
            with self.subTest(malo), self.assertRaises(HTTPException) as cm:
                svc.validar_nit(malo)
            self.assertEqual(cm.exception.status_code, 422)
        svc.validar_nit("12345")
        svc.validar_nit("1" * 20)

    def test_rechaza_todo_ceros_y_vacio(self):
        for malo in ("00000000", "---", ""):
            with self.subTest(malo), self.assertRaises(HTTPException) as cm:
                svc.validar_nit(malo)
            self.assertEqual(cm.exception.status_code, 422)


class ValidacionFacturacion(unittest.TestCase):
    def test_razon_social_normaliza_espacios(self):
        self.assertEqual(svc.validar_razon_social("  Andes   Express \t S.R.L. "), "Andes Express S.R.L.")

    def test_razon_social_invalida(self):
        for mala in ("A", "   ", "-- ..", "#"):
            with self.subTest(mala), self.assertRaises(HTTPException) as cm:
                svc.validar_razon_social(mala)
            self.assertEqual(cm.exception.status_code, 422)

    def test_correo_facturacion_valido_y_normalizado(self):
        self.assertEqual(svc.validar_correo("Facturas@Andes-Express.COM"), "Facturas@andes-express.com")

    def test_correo_facturacion_invalido(self):
        for malo in ("sin-arroba", "a@", "@x.com", "a b@x.com", "a@x", "a@@x.com"):
            with self.subTest(malo), self.assertRaises(HTTPException) as cm:
                svc.validar_datos_facturacion(correo_facturacion=malo)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertIn("correo de facturacion", cm.exception.detail)

    def test_direccion_fiscal_invalida(self):
        for mala in ("abc", "  ", "-----"):
            with self.subTest(mala), self.assertRaises(HTTPException) as cm:
                svc.validar_direccion_fiscal(mala)
            self.assertEqual(cm.exception.status_code, 422)

    def test_solo_valida_lo_que_se_pasa(self):
        self.assertEqual(svc.validar_datos_facturacion(nit="123-456-7"), {"nit": "1234567"})


class SchemasForma(unittest.TestCase):
    def test_obligatorios_faltantes(self):
        for falta in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            datos = dict(razon_social="X Y", nit="12345", correo_facturacion="a@b.co", direccion_fiscal="Calle 1")
            del datos[falta]
            with self.subTest(falta), self.assertRaises(ValidationError):
                AgenciaCreate(**datos)

    def test_obligatorios_vacios_o_solo_espacios(self):
        for campo in ("razon_social", "nit", "correo_facturacion", "direccion_fiscal"):
            with self.subTest(campo), self.assertRaises(ValidationError):
                payload(**{campo: "   "})

    def test_recorta_y_opcionales_vacios_son_none(self):
        p = payload(razon_social="  Andes  ", telefono="  ", contacto_operativo=" Ana ")
        self.assertEqual(p.razon_social, "Andes")
        self.assertIsNone(p.telefono)
        self.assertEqual(p.contacto_operativo, "Ana")

    def test_largos_maximos(self):
        with self.assertRaises(ValidationError):
            payload(razon_social="x" * 151)
        with self.assertRaises(ValidationError):
            payload(nit="1" * 31)

    def test_update_parcial_y_obligatorio_vacio_rechazado(self):
        self.assertEqual(AgenciaUpdate(telefono="70000000").model_dump(exclude_none=True), {"telefono": "70000000"})
        with self.assertRaises(ValidationError):
            AgenciaUpdate(razon_social="  ")

    def test_estado_payload(self):
        self.assertFalse(AgenciaEstadoPayload(is_active=False).is_active)
        with self.assertRaises(ValidationError):
            AgenciaEstadoPayload()


class Permisos(unittest.TestCase):
    """D/V/C nunca llegan a la base de datos en operaciones de escritura."""

    ESCRITURAS = (
        lambda db, u: svc.crear_agencia(db, u, payload()),
        lambda db, u: svc.actualizar_agencia(db, u, 1, AgenciaUpdate(telefono="1")),
        lambda db, u: svc.cambiar_estado(db, u, 1, False),
        lambda db, u: svc.eliminar_agencia(db, u, 1),
    )

    def test_d_v_c_sin_rol_no_escriben(self):
        for rol in ("D", "V", "C", None):
            for i, op in enumerate(self.ESCRITURAS):
                with self.subTest(rol=rol, op=i):
                    db = mock.Mock()
                    with self.assertRaises(HTTPException) as cm:
                        op(db, usuario(rol))
                    self.assertEqual(cm.exception.status_code, 403)
                    self.assertEqual(db.method_calls, [])

    def test_v_y_c_no_consultan(self):
        for rol in ("V", "C", None):
            db = mock.Mock()
            for op in (
                lambda: svc.listar_agencias(db, usuario(rol)),
                lambda: svc.obtener_agencia(db, usuario(rol), 1),
            ):
                with self.subTest(rol=rol), self.assertRaises(HTTPException) as cm:
                    op()
                self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(db.method_calls, [])

    def test_gs_y_asu_pasan_la_autorizacion(self):
        for rol in ("GS", "ASU"):
            self.assertTrue(svc._es_admin(usuario(rol)))
            svc._exigir_admin(usuario(rol))
        self.assertFalse(svc._es_admin(usuario("D")))

    def test_d_obtiene_habilitada_y_no_deshabilitada(self):
        db = mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=agencia(is_active=True)) as b:
            self.assertEqual(svc.obtener_agencia(db, usuario("D"), 7).id_agencia, 7)
        with mock.patch.object(svc, "_buscar", return_value=agencia(is_active=False)):
            with self.assertRaises(HTTPException) as cm:
                svc.obtener_agencia(db, usuario("D"), 7)
            self.assertEqual(cm.exception.status_code, 404)
            # GS si ve la deshabilitada
            self.assertFalse(svc.obtener_agencia(db, usuario("GS"), 7).is_active)

    def test_serializacion_d_sin_datos_de_facturacion(self):
        a = agencia(fecha_creacion=mock.sentinel, fecha_actualizacion=mock.sentinel)
        d = svc.serializar_agencia(agencia_completa(), usuario("D"))
        for prohibido in ("nit", "correo_facturacion", "direccion_fiscal"):
            self.assertNotIn(prohibido, d)
        self.assertEqual(d["razon_social"], "Andes Express")
        c = svc.serializar_agencia(agencia_completa(), usuario("GS"))
        for campo in ("nit", "correo_facturacion", "direccion_fiscal"):
            self.assertIn(campo, c)


def agencia_completa(**extra) -> AgenciaReparto:
    from datetime import datetime, timezone

    ahora = datetime.now(timezone.utc)
    return agencia(fecha_creacion=ahora, fecha_actualizacion=ahora, **extra)


class CrearAgencia(unittest.TestCase):
    def _crear(self, db=None, **patch_uso):
        db = db or mock.Mock()
        with mock.patch.object(svc, "_nit_en_uso", return_value=patch_uso.get("nit", False)), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=patch_uso.get("razon", False)):
            return db, svc.crear_agencia(db, usuario("GS"), payload(nit="1.020-304 050", razon_social="  Andes   Express  "))

    def test_creacion_valida_normaliza_y_habilita(self):
        db, _ = self._crear()
        db.add.assert_called_once()
        a = db.add.call_args.args[0]
        self.assertEqual(a.nit, "1020304050")
        self.assertEqual(a.razon_social, "Andes Express")
        self.assertEqual(a.correo_facturacion, "Facturas@andes-express.com")
        self.assertTrue(a.is_active)
        db.commit.assert_called_once()

    def test_admin_asu_tambien_crea(self):
        db = mock.Mock()
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            svc.crear_agencia(db, usuario("ASU"), payload())
        db.add.assert_called_once()

    def test_nit_duplicado(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._crear(db, nit=True)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("NIT", cm.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_razon_social_duplicada(self):
        db = mock.Mock()
        with self.assertRaises(HTTPException) as cm:
            self._crear(db, razon=True)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("razon social", cm.exception.detail)
        db.add.assert_not_called()

    def test_datos_invalidos_no_tocan_la_bd(self):
        for extra in (dict(nit="ABC"), dict(correo_facturacion="malo"), dict(direccion_fiscal="..."), dict(razon_social="#")):
            db = mock.Mock()
            with self.subTest(extra), self.assertRaises(HTTPException) as cm:
                svc.crear_agencia(db, usuario("GS"), payload(**extra))
            self.assertEqual(cm.exception.status_code, 422)
            db.add.assert_not_called()

    def test_correo_de_contacto_invalido(self):
        with self.assertRaises(HTTPException) as cm:
            svc.crear_agencia(mock.Mock(), usuario("GS"), payload(correo="nope"))
        self.assertEqual(cm.exception.status_code, 422)

    def test_carrera_por_nit_integrity_error_es_409_con_rollback(self):
        db = mock.Mock()
        db.commit.side_effect = integrity(svc._CONSTRAINT_NIT)
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            with self.assertRaises(HTTPException) as cm:
                svc.crear_agencia(db, usuario("GS"), payload())
        self.assertEqual(cm.exception.status_code, 409)
        db.rollback.assert_called_once()

    def test_carrera_por_razon_social_integrity_error_es_409(self):
        db = mock.Mock()
        db.commit.side_effect = integrity(svc._CONSTRAINT_RAZON)
        with mock.patch.object(svc, "_nit_en_uso", return_value=False), \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=False):
            with self.assertRaises(HTTPException) as cm:
                svc.crear_agencia(db, usuario("GS"), payload())
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("razon social", cm.exception.detail)


class TraducirIntegrityError(unittest.TestCase):
    def test_mapeo(self):
        casos = [
            (svc._CONSTRAINT_NIT, 409),
            (svc._CONSTRAINT_RAZON, 409),
            ("fk_envios_id_agencia_agencias_reparto", 409),
            ("ck_agencias_reparto_nit_no_vacio", 422),
            ("cualquier_otra", 422),
        ]
        for nombre, codigo in casos:
            with self.subTest(nombre):
                self.assertEqual(svc.traducir_integrity_error(integrity(nombre)).status_code, codigo)

    def test_sin_diag_usa_el_texto_del_error(self):
        exc = IntegrityError("x", {}, Exception('duplicate key value violates unique constraint "ix_agencias_reparto_nit"'))
        self.assertEqual(svc.traducir_integrity_error(exc).status_code, 409)


class ActualizarAgencia(unittest.TestCase):
    def _actualizar(self, a, cambios, uso_nit=False, uso_razon=False):
        db = mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "_nit_en_uso", return_value=uso_nit) as nit_uso, \
             mock.patch.object(svc, "_razon_social_en_uso", return_value=uso_razon) as raz_uso:
            resultado = svc.actualizar_agencia(db, usuario("GS"), 7, AgenciaUpdate(**cambios))
        return db, resultado, nit_uso, raz_uso

    def test_actualiza_solo_lo_enviado(self):
        a = agencia()
        db, r, _, _ = self._actualizar(a, {"telefono": "70000000", "razon_social": "Nueva  Razon"})
        self.assertEqual(r.telefono, "70000000")
        self.assertEqual(r.razon_social, "Nueva Razon")
        self.assertEqual(r.nit, "1020304050")  # intacto
        self.assertTrue(r.is_active)  # PUT no cambia habilitacion
        db.commit.assert_called_once()

    def test_normaliza_nit_nuevo(self):
        db, r, nit_uso, _ = self._actualizar(agencia(), {"nit": "999-888-777"})
        self.assertEqual(r.nit, "999888777")
        nit_uso.assert_called_once()
        self.assertEqual(nit_uso.call_args.kwargs.get("excluir_id", nit_uso.call_args.args[-1]), 7)

    def test_mismo_nit_no_cuenta_como_duplicado(self):
        _, _, nit_uso, _ = self._actualizar(agencia(), {"nit": "1020304050", "telefono": "1"})
        nit_uso.assert_not_called()

    def test_misma_razon_social_con_otras_mayusculas_no_consulta_duplicado(self):
        _, r, _, raz_uso = self._actualizar(agencia(), {"razon_social": "ANDES  express"})
        raz_uso.assert_not_called()
        self.assertEqual(r.razon_social, "ANDES express")

    def test_nit_o_razon_de_otra_agencia_es_409(self):
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(agencia(), {"nit": "55555555"}, uso_nit=True)
        self.assertEqual(cm.exception.status_code, 409)
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(agencia(), {"razon_social": "Otra SA"}, uso_razon=True)
        self.assertEqual(cm.exception.status_code, 409)

    def test_correo_facturacion_invalido_no_modifica(self):
        a = agencia()
        with self.assertRaises(HTTPException) as cm:
            self._actualizar(a, {"correo_facturacion": "malo"})
        self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(a.correo_facturacion, "f@andes-express.com")

    def test_sin_campos_es_400(self):
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_agencia(mock.Mock(), usuario("GS"), 7, AgenciaUpdate())
        self.assertEqual(cm.exception.status_code, 400)

    def test_no_existe_es_404(self):
        db = mock.Mock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with self.assertRaises(HTTPException) as cm:
            svc.actualizar_agencia(db, usuario("GS"), 99, AgenciaUpdate(telefono="1"))
        self.assertEqual(cm.exception.status_code, 404)

    def test_integrity_error_al_confirmar(self):
        a, db = agencia(), mock.Mock()
        db.commit.side_effect = integrity(svc._CONSTRAINT_NIT)
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "_nit_en_uso", return_value=False):
            with self.assertRaises(HTTPException) as cm:
                svc.actualizar_agencia(db, usuario("GS"), 7, AgenciaUpdate(nit="77777777"))
        self.assertEqual(cm.exception.status_code, 409)
        db.rollback.assert_called_once()


class CambiarEstado(unittest.TestCase):
    def test_deshabilitar_y_habilitar(self):
        a, db = agencia(is_active=True), mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a):
            self.assertFalse(svc.cambiar_estado(db, usuario("GS"), 7, False).is_active)
            self.assertTrue(svc.cambiar_estado(db, usuario("ASU"), 7, True).is_active)
        self.assertEqual(db.commit.call_count, 2)

    def test_no_toca_envios_historicos(self):
        a, db = agencia(is_active=True), mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "contar_envios") as contar:
            svc.cambiar_estado(db, usuario("GS"), 7, False)
        contar.assert_not_called()
        # nunca emite UPDATE/DELETE sobre envios: solo se mutó la agencia
        for llamada in db.method_calls:
            self.assertNotIn(llamada[0], ("execute", "delete", "bulk_update_mappings"))

    def test_idempotente_sin_commit(self):
        a, db = agencia(is_active=False), mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a):
            self.assertFalse(svc.cambiar_estado(db, usuario("GS"), 7, False).is_active)
        db.commit.assert_not_called()


class EliminarAgencia(unittest.TestCase):
    def test_sin_envios_elimina(self):
        a, db = agencia(), mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "contar_envios", return_value=0):
            self.assertEqual(svc.eliminar_agencia(db, usuario("GS"), 7), "Andes Express")
        db.delete.assert_called_once_with(a)
        db.commit.assert_called_once()

    def test_con_envios_409_sin_borrar(self):
        a, db = agencia(), mock.Mock()
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "contar_envios", return_value=3):
            with self.assertRaises(HTTPException) as cm:
                svc.eliminar_agencia(db, usuario("ASU"), 7)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("deshabilitela", cm.exception.detail)
        self.assertIn("3", cm.exception.detail)
        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_envio_asignado_en_paralelo_es_409(self):
        a, db = agencia(), mock.Mock()
        db.commit.side_effect = integrity("fk_envios_id_agencia_agencias_reparto")
        with mock.patch.object(svc, "_buscar", return_value=a), \
             mock.patch.object(svc, "contar_envios", return_value=0):
            with self.assertRaises(HTTPException) as cm:
                svc.eliminar_agencia(db, usuario("GS"), 7)
        self.assertEqual(cm.exception.status_code, 409)
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
