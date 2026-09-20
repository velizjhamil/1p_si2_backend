# backend/app/modules/delivery/disponibilidad_service.py
# CU19 - consulta de agencias DISPONIBLES para una ciudad.
#
# Solo DISPONIBILIDAD: no asigna ninguna agencia a un envio, no toca CU18 y NO
# calcula tarifas ni costos (una agencia sin tarifas tambien aparece).
#
# CIUDAD (misma regla de Fase 5, sin duplicarla): el nombre se normaliza con
# `zonas_service.normalizar_nombre_ciudad` (sin mayusculas, tildes ni espacios de
# mas) y se busca con `buscar_ciudades_por_nombre`, la funcion sobre la que se
# construye `resolver_ciudad` (una ciudad resuelta = exactamente una
# coincidencia). Se usa `buscar_...` y no `resolver_ciudad` porque esta devuelve
# None tanto si no existe como si es ambigua, y aqui hay que distinguir:
#   0 coincidencias  -> 404
#   1 coincidencia   -> se consulta
#   2+ (ambigua)     -> 409, SIN devolver agencias por aproximacion
# `ventas.ciudad` no se toca: el texto llega como parametro de la consulta.
#
# COBERTURA: una agencia esta disponible para la ciudad si
#   - esta habilitada (is_active), y
#   - tiene AL MENOS UNA fila en agencia_zonas con esa id_ciudad.
# La FK garantiza que la zona pertenece a la agencia y una zona eliminada ya no
# existe (baja fisica). Que la zona tenga subzona no importa: una agencia que
# solo cubre subzonas tambien cubre la ciudad. Que tenga o no tarifas tampoco.
# Una agencia con varias zonas en la ciudad aparece UNA sola vez.
#
# ORDEN (determinista, el mismo del listado de agencias): lower(razon_social) y
# luego id_agencia. No hay ranking.
#
# EFICIENCIA: una sola consulta agregada (zonas agrupadas por agencia + join),
# sin cargar entidades ni relaciones: el numero de sentencias no crece con la
# cantidad de agencias.
#
# AUTORIZACION: ASU/GS/D consultan; V/C 403. La vista es la de `AgenciaResumen`
# (sin NIT ni facturacion) para todos los roles.
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.delivery.agencias_service import _exigir_lectura
from app.modules.delivery.models import AgenciaReparto, AgenciaZona
from app.modules.delivery.zonas_service import (
    buscar_ciudades_por_nombre,
    normalizar_nombre_ciudad,
)
from app.modules.empresa.models import Ciudad
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_disponible import AgenciaDisponible, CiudadConsultada


def resolver_ciudad_consultada(db: Session, nombre: str | None) -> Ciudad:
    """Ciudad unica a la que corresponde el nombre. 422 si viene vacio, 404 si
    no existe, 409 si es ambigua (nunca se adivina)."""
    if not normalizar_nombre_ciudad(nombre):
        raise HTTPException(status_code=422, detail="Indique el nombre de la ciudad.")
    encontradas = buscar_ciudades_por_nombre(db, nombre)
    if not encontradas:
        raise HTTPException(
            status_code=404, detail=f"No existe la ciudad '{nombre.strip()}' en el catalogo."
        )
    if len(encontradas) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El nombre de ciudad '{nombre.strip()}' es ambiguo: coincide con "
                f"{len(encontradas)} ciudades del catalogo. No se puede determinar la "
                "disponibilidad."
            ),
        )
    return encontradas[0]


def agencias_disponibles(
    db: Session, usuario: Usuario, ciudad: str | None
) -> tuple[Ciudad, list]:
    """Agencias habilitadas con cobertura en la ciudad, ordenadas por
    lower(razon_social) e id_agencia. Devuelve (ciudad resuelta, filas)."""
    _exigir_lectura(usuario)  # 403 a V/C antes de tocar la BD
    ciudad_ = resolver_ciudad_consultada(db, ciudad)

    # Zonas de la ciudad agrupadas por agencia (una fila por agencia).
    zonas = (
        db.query(
            AgenciaZona.id_agencia.label("id_agencia"),
            func.count(AgenciaZona.id_zona).label("zonas_en_ciudad"),
            # zona sin subzona (NULL o solo espacios) = cubre toda la ciudad
            func.bool_or(func.coalesce(func.btrim(AgenciaZona.nombre_zona), "") == "").label(
                "cobertura_completa"
            ),
        )
        .filter(AgenciaZona.id_ciudad == ciudad_.id)
        .group_by(AgenciaZona.id_agencia)
        .subquery()
    )
    filas = (
        db.query(
            AgenciaReparto.id_agencia,
            AgenciaReparto.razon_social,
            AgenciaReparto.contacto_operativo,
            AgenciaReparto.telefono,
            AgenciaReparto.is_active,
            zonas.c.zonas_en_ciudad,
            zonas.c.cobertura_completa,
        )
        .join(zonas, zonas.c.id_agencia == AgenciaReparto.id_agencia)
        .filter(AgenciaReparto.is_active.is_(True))
        .order_by(func.lower(AgenciaReparto.razon_social), AgenciaReparto.id_agencia)
        .all()
    )
    return ciudad_, filas


def serializar_disponible(fila) -> dict:
    return AgenciaDisponible(
        id_agencia=fila.id_agencia,
        razon_social=fila.razon_social,
        contacto_operativo=fila.contacto_operativo,
        telefono=fila.telefono,
        is_active=fila.is_active,
        cubre_ciudad=True,
        cobertura_completa=bool(fila.cobertura_completa),
        zonas_en_ciudad=int(fila.zonas_en_ciudad),
    ).model_dump()


def serializar_ciudad(ciudad: Ciudad) -> dict:
    return CiudadConsultada(
        id_ciudad=ciudad.id, nombre=ciudad.nombre, departamento=ciudad.departamento
    ).model_dump()
