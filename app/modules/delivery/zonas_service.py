# backend/app/modules/delivery/zonas_service.py
# CU19 - zonas de cobertura de las agencias de reparto.
#
# Extension separada de agencias_service.py (que no se modifica): reutiliza su
# autorizacion (`_exigir_admin`), la busqueda de agencia (`_buscar`, con
# FOR UPDATE), la visibilidad por rol (`obtener_agencia`: D solo ve agencias
# habilitadas) y `_nombre_restriccion`. Las tarifas (tarifas_service), la
# disponibilidad y la cotizacion NO estan aca; si reutilizan la normalizacion y
# busqueda de ciudades de este modulo.
#
# MODELO (Fase 2, sin cambios): agencia_zonas(id_agencia FK CASCADE, id_ciudad
# FK a ciudades, nombre_zona NULL) con UNIQUE funcional
# (id_agencia, id_ciudad, lower(coalesce(btrim(nombre_zona), ''))).
#
# REGLAS
# - Ciudad: por `id_ciudad` (404 si no existe) o por nombre `ciudad`, resuelto
#   por nombre NORMALIZADO (sin mayusculas, tildes ni espacios de mas). Sin
#   coincidencias -> 404; varias coincidencias (ambiguo) -> 409 y se pide el
#   id. `resolver_ciudad` devuelve None cuando no es inequivoca: asi la usara
#   la disponibilidad de agencias (una ciudad ambigua NO se considera cubierta).
#   No se toca `ventas.ciudad`.
# - Subzona (`nombre_zona`, opcional): se recorta y colapsan espacios internos;
#   vacio = sin subzona ("toda la ciudad"). Se compara sin distinguir
#   mayusculas y SIN ignorar tildes (misma semantica que la razon social y que
#   el indice unico de la DB).
# - Duplicado: misma agencia + ciudad + subzona equivalente -> 409 (servicio y
#   UNIQUE; un IntegrityError por carrera tambien termina en 409).
# - Agencia deshabilitada: no admite crear ni actualizar zonas (400). Consultar
#   y ELIMINAR si esta permitido: quitar cobertura no es una configuracion nueva.
# - Eliminacion: 409 si la zona tiene tarifas (no se hacen cascadas destructivas;
#   hay que eliminar las tarifas primero). Las FK de la DB (RESTRICT de envios
#   sobre tarifas) tambien se traducen a 409.
# - Autorizacion: ASU/GS administran; D solo consulta zonas de agencias
#   habilitadas; V/C 403. Las zonas no contienen datos de facturacion.
import re
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.delivery.agencias_service import (
    _buscar,
    _exigir_admin,
    _nombre_restriccion,
    obtener_agencia,
)
from app.modules.delivery.models import AgenciaReparto, AgenciaZona
from app.modules.empresa.models import Ciudad
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_zona import ZonaCreate, ZonaRead, ZonaUpdate

_CONSTRAINT_COBERTURA = "uq_agencia_zonas_cobertura"
_CONSTRAINT_FK_CIUDAD = "fk_agencia_zonas_id_ciudad_ciudades"
_CONSTRAINT_FK_AGENCIA = "fk_agencia_zonas_id_agencia_agencias_reparto"
_CONSTRAINT_FK_ENVIOS = "fk_envios_id_tarifa_aplicada"

_ESPACIOS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Normalizacion (funciones puras)
# ---------------------------------------------------------------------------
def normalizar_nombre_ciudad(nombre: str | None) -> str:
    """Clave de comparacion de ciudades: sin tildes, sin mayusculas y con los
    espacios colapsados. 'Potosí ' == 'potosi' == 'POTOSI'."""
    if not nombre:
        return ""
    descompuesto = unicodedata.normalize("NFD", nombre)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_tildes).strip().casefold()


def normalizar_subzona(nombre: str | None) -> str | None:
    """Subzona a guardar: recortada y con espacios internos colapsados; vacia
    -> None. Conserva mayusculas y tildes tal como se escribieron (la
    comparacion usa `clave_subzona`). 422 si no tiene letras ni numeros."""
    if nombre is None:
        return None
    limpio = _ESPACIOS.sub(" ", nombre).strip()
    if not limpio:
        return None
    if not any(c.isalnum() for c in limpio):
        raise HTTPException(status_code=422, detail="La subzona debe contener letras o numeros.")
    return limpio


def clave_subzona(nombre: str | None) -> str:
    """Clave de comparacion de subzonas: minusculas, SIN ignorar tildes (igual
    que lower(coalesce(btrim(nombre_zona), '')) del indice unico). None -> ''."""
    return (nombre or "").strip().lower()


# ---------------------------------------------------------------------------
# Ciudades
# ---------------------------------------------------------------------------
def buscar_ciudades_por_nombre(db: Session, nombre: str | None) -> list[Ciudad]:
    """Todas las ciudades del catalogo cuyo nombre normalizado coincide.

    El catalogo es pequeno: se compara en Python para no depender de la
    extension `unaccent` (no se agregan extensiones a la DB)."""
    clave = normalizar_nombre_ciudad(nombre)
    if not clave:
        return []
    ids = [i for i, n in db.query(Ciudad.id, Ciudad.nombre).all() if normalizar_nombre_ciudad(n) == clave]
    return [c for c in (db.get(Ciudad, i) for i in ids) if c is not None]


def resolver_ciudad(db: Session, nombre: str | None) -> Ciudad | None:
    """Ciudad por nombre normalizado, o None si no hay coincidencia o es
    AMBIGUA (mas de una): una ciudad solo se resuelve si es inequivoca.

    Es la definicion de "ciudad resuelta" de CU19 y la que documentan los tests
    de Fase 5. Los endpoints de disponibilidad, cotizacion y asignacion NO la
    llaman directamente porque deben distinguir "no existe" (404) de "ambigua"
    (409); usan `buscar_ciudades_por_nombre`, de la que esta funcion se deriva
    (disponibilidad_service.resolver_ciudad_consultada)."""
    encontradas = buscar_ciudades_por_nombre(db, nombre)
    return encontradas[0] if len(encontradas) == 1 else None


def _ciudad_del_payload(db: Session, payload: ZonaCreate | ZonaUpdate) -> Ciudad | None:
    """Ciudad indicada en el payload (None si no se indico ninguna).
    404 si no existe; 409 si el nombre es ambiguo."""
    if payload.id_ciudad is not None:
        ciudad = db.get(Ciudad, payload.id_ciudad)
        if ciudad is None:
            raise HTTPException(
                status_code=404, detail=f"No existe la ciudad con id {payload.id_ciudad}."
            )
        return ciudad
    if payload.ciudad is None:
        return None
    encontradas = buscar_ciudades_por_nombre(db, payload.ciudad)
    if not encontradas:
        raise HTTPException(
            status_code=404, detail=f"No existe la ciudad '{payload.ciudad}' en el catalogo."
        )
    if len(encontradas) > 1:
        ids = ", ".join(str(c.id) for c in encontradas)
        raise HTTPException(
            status_code=409,
            detail=(
                f"El nombre de ciudad '{payload.ciudad}' es ambiguo (ids: {ids}). "
                "Indique la ciudad con 'id_ciudad'."
            ),
        )
    return encontradas[0]


# ---------------------------------------------------------------------------
# Errores de base de datos
# ---------------------------------------------------------------------------
def traducir_integrity_error(exc: IntegrityError) -> HTTPException:
    """IntegrityError -> HTTPException. Nunca un 500."""
    nombre = _nombre_restriccion(exc)
    if _CONSTRAINT_COBERTURA in nombre:
        return HTTPException(
            status_code=409,
            detail="La agencia ya tiene una zona equivalente para esa ciudad y subzona.",
        )
    if _CONSTRAINT_FK_CIUDAD in nombre:
        return HTTPException(status_code=404, detail="La ciudad indicada no existe.")
    if _CONSTRAINT_FK_AGENCIA in nombre:
        return HTTPException(status_code=404, detail="La agencia indicada ya no existe.")
    if _CONSTRAINT_FK_ENVIOS in nombre:
        return HTTPException(
            status_code=409,
            detail="No se puede eliminar: hay envios asociados a las tarifas de esta zona.",
        )
    return HTTPException(
        status_code=422, detail="Los datos de la zona no cumplen las restricciones."
    )


def _confirmar(db: Session) -> None:
    """Commit unico; ante IntegrityError hace rollback y lo traduce."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise traducir_integrity_error(exc) from exc


# ---------------------------------------------------------------------------
# Consultas internas
# ---------------------------------------------------------------------------
def _agencia_para_escritura(db: Session, usuario: Usuario, id_agencia: int) -> AgenciaReparto:
    """ASU/GS; agencia existente (404), bloqueada FOR UPDATE (serializa contra
    un cambio de estado) y habilitada (400)."""
    _exigir_admin(usuario)
    agencia = _buscar(db, id_agencia, bloquear=True)
    if not agencia.is_active:
        db.rollback()  # libera el bloqueo
        raise HTTPException(
            status_code=400,
            detail=(
                f"La agencia '{agencia.razon_social}' esta deshabilitada: no admite "
                "nuevas configuraciones de cobertura. Habilitela primero."
            ),
        )
    return agencia


def _buscar_zona(db: Session, id_agencia: int, id_zona: int, *, bloquear: bool = False) -> AgenciaZona:
    """404 si la zona no existe O pertenece a otra agencia."""
    query = db.query(AgenciaZona).filter(
        AgenciaZona.id_zona == id_zona, AgenciaZona.id_agencia == id_agencia
    )
    if bloquear:
        query = query.with_for_update(of=AgenciaZona)
    zona = query.first()
    if zona is None:
        raise HTTPException(
            status_code=404,
            detail=f"La agencia {id_agencia} no tiene una zona con id {id_zona}.",
        )
    return zona


def _cobertura_en_uso(
    db: Session, id_agencia: int, id_ciudad: int, subzona: str | None, excluir_id: int | None = None
) -> bool:
    """Mismo criterio que el indice unico uq_agencia_zonas_cobertura."""
    query = db.query(AgenciaZona.id_zona).filter(
        AgenciaZona.id_agencia == id_agencia,
        AgenciaZona.id_ciudad == id_ciudad,
        func.lower(func.coalesce(func.btrim(AgenciaZona.nombre_zona), "")) == clave_subzona(subzona),
    )
    if excluir_id is not None:
        query = query.filter(AgenciaZona.id_zona != excluir_id)
    return query.first() is not None


def _exigir_cobertura_libre(
    db: Session, id_agencia: int, ciudad: Ciudad, subzona: str | None, excluir_id: int | None = None
) -> None:
    if _cobertura_en_uso(db, id_agencia, ciudad.id, subzona, excluir_id):
        donde = f"{ciudad.nombre} / {subzona}" if subzona else f"{ciudad.nombre} (toda la ciudad)"
        raise HTTPException(
            status_code=409, detail=f"La agencia ya tiene una zona de cobertura en {donde}."
        )


# ---------------------------------------------------------------------------
# Consultas (ASU/GS/D)
# ---------------------------------------------------------------------------
def listar_zonas(db: Session, usuario: Usuario, id_agencia: int) -> list[AgenciaZona]:
    """Zonas de una agencia, ordenadas por ciudad y subzona. ASU/GS: de
    cualquier agencia; D: solo de agencias habilitadas (404 si no); V/C: 403."""
    agencia = obtener_agencia(db, usuario, id_agencia)
    return sorted(
        agencia.zonas,
        key=lambda z: (normalizar_nombre_ciudad(z.ciudad.nombre), clave_subzona(z.nombre_zona), z.id_zona),
    )


def obtener_zona(db: Session, usuario: Usuario, id_agencia: int, id_zona: int) -> AgenciaZona:
    obtener_agencia(db, usuario, id_agencia)  # autorizacion + visibilidad + 404 de agencia
    return _buscar_zona(db, id_agencia, id_zona)


# ---------------------------------------------------------------------------
# Operaciones (solo ASU/GS)
# ---------------------------------------------------------------------------
def crear_zona(db: Session, usuario: Usuario, id_agencia: int, payload: ZonaCreate) -> AgenciaZona:
    """Orden de validacion: rol (403) -> agencia (404) -> habilitada (400) ->
    ciudad (404/409) -> subzona (422) -> duplicado (409)."""
    agencia = _agencia_para_escritura(db, usuario, id_agencia)
    ciudad = _ciudad_del_payload(db, payload)
    subzona = normalizar_subzona(payload.nombre_zona)
    _exigir_cobertura_libre(db, agencia.id_agencia, ciudad, subzona)

    zona = AgenciaZona(id_agencia=agencia.id_agencia, id_ciudad=ciudad.id, nombre_zona=subzona)
    db.add(zona)
    _confirmar(db)  # una carrera con otra alta igual la cubre el UNIQUE
    db.refresh(zona)
    return zona


def actualizar_zona(
    db: Session, usuario: Usuario, id_agencia: int, id_zona: int, payload: ZonaUpdate
) -> AgenciaZona:
    """Cambia ciudad y/o subzona (None = no cambiar). No deja referencias
    invalidas: la ciudad nueva debe existir y la cobertura resultante no puede
    duplicar otra zona de la agencia."""
    _exigir_admin(usuario)
    enviados: dict[str, Any] = payload.model_dump(exclude_none=True)
    if not enviados:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar.")

    agencia = _agencia_para_escritura(db, usuario, id_agencia)
    zona = _buscar_zona(db, agencia.id_agencia, id_zona, bloquear=True)

    ciudad = _ciudad_del_payload(db, payload) or zona.ciudad
    subzona = normalizar_subzona(payload.nombre_zona) if payload.nombre_zona is not None else zona.nombre_zona

    if ciudad.id == zona.id_ciudad and clave_subzona(subzona) == clave_subzona(zona.nombre_zona):
        if subzona != zona.nombre_zona:  # solo cambia la forma (mayusculas/espacios): se guarda
            zona.nombre_zona = subzona
            _confirmar(db)
            db.refresh(zona)
        else:
            db.rollback()  # sin cambios: libera los bloqueos
        return zona

    _exigir_cobertura_libre(db, agencia.id_agencia, ciudad, subzona, excluir_id=zona.id_zona)
    zona.id_ciudad = ciudad.id
    zona.nombre_zona = subzona
    _confirmar(db)
    db.refresh(zona)
    return zona


def eliminar_zona(db: Session, usuario: Usuario, id_agencia: int, id_zona: int) -> str:
    """Elimina fisicamente la zona. 409 si tiene tarifas (sin cascadas
    destructivas). Permitido aunque la agencia este deshabilitada. Devuelve una
    descripcion de la zona para el mensaje."""
    _exigir_admin(usuario)
    agencia = _buscar(db, id_agencia, bloquear=True)
    zona = _buscar_zona(db, agencia.id_agencia, id_zona, bloquear=True)

    tarifas = len(zona.tarifas)
    if tarifas > 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la zona tiene {tarifas} tarifa(s) asociada(s). "
                "Elimine primero sus tarifas."
            ),
        )

    descripcion = descripcion_zona(zona)
    db.delete(zona)
    _confirmar(db)
    return descripcion


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------
def descripcion_zona(zona: AgenciaZona) -> str:
    return f"{zona.ciudad.nombre} / {zona.nombre_zona}" if zona.nombre_zona else f"{zona.ciudad.nombre}"


def serializar_zona(zona: AgenciaZona) -> dict:
    """Misma forma para ASU/GS y D (no hay datos de facturacion en una zona)."""
    return ZonaRead(
        id_zona=zona.id_zona,
        id_agencia=zona.id_agencia,
        id_ciudad=zona.id_ciudad,
        ciudad=zona.ciudad.nombre,
        departamento=zona.ciudad.departamento,
        nombre_zona=zona.nombre_zona,
        total_tarifas=len(zona.tarifas),
        fecha_creacion=zona.fecha_creacion,
    ).model_dump()
