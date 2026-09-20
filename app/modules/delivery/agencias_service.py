# backend/app/modules/delivery/agencias_service.py
# CU19 - Gestion de Agencias de Reparto: logica de negocio del CRUD de agencias.
#
# Capa entre el router (Fase 4) y los modelos (AgenciaReparto). Aca viven:
# la normalizacion/validacion de datos de facturacion, los duplicados, la
# matriz de permisos por rol, el bloqueo de eliminacion y las serializaciones.
# Zonas, tarifas, disponibilidad, cotizacion y asignacion a envios viven en sus
# propios modulos (zonas_service, tarifas_service, disponibilidad_service,
# cotizacion_service, asignacion_agencia_service).
#
# Convenciones (las mismas de CU18, delivery/service.py):
# - Errores de negocio como HTTPException: 403 permisos, 404 no existe, 409
#   duplicado/conflicto, 422 datos invalidos.
# - Cada operacion que muta hace commit UNA sola vez al final; ante un
#   IntegrityError (carrera entre dos altas del mismo NIT, por ejemplo) hace
#   rollback y lo traduce a 409/422 en lugar de dejar escapar un 500.
# - Las operaciones que mutan bloquean la fila con SELECT ... FOR UPDATE.
#
# Permisos: ASU/GS administran el catalogo (crear, editar, habilitar,
# deshabilitar, eliminar) y ven todo. D solo CONSULTA agencias habilitadas y sin
# datos de facturacion. V y C no acceden.
#
# Facturacion: la validacion es LOCAL (formato y consistencia basica). El
# sistema NO consulta al SIN ni a ningun servicio externo: un NIT aceptado
# aqui NO esta verificado fiscalmente.
import re
from typing import Any

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.delivery.models import AgenciaReparto, Envio
from app.modules.delivery.service import (
    ROL_REPARTIDOR,
    ROLES_ADMIN_ENVIO,
    _rol_nombre,
)
from app.modules.usuarios.models import Usuario
from app.schemas.agencia import (
    AgenciaCreate,
    AgenciaDetalle,
    AgenciaRead,
    AgenciaResumen,
    AgenciaUpdate,
)

# Nombres de las restricciones de la migracion CU19 (para traducir
# IntegrityError sin depender del texto del motor).
_CONSTRAINT_NIT = "ix_agencias_reparto_nit"
_CONSTRAINT_RAZON = "uq_agencias_reparto_razon_social_lower"

# NIT: solo digitos tras normalizar. 5 es el minimo que ya usan proveedores y
# empresa; 20 es el largo de la columna.
NIT_MIN_DIGITOS = 5
NIT_MAX_DIGITOS = 20
RAZON_SOCIAL_MIN = 2
DIRECCION_FISCAL_MIN = 5

_SEPARADORES_NIT = re.compile(r"[\s.\-/]")
_ESPACIOS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Autorizacion
# ---------------------------------------------------------------------------
def _es_admin(usuario: Usuario) -> bool:
    return _rol_nombre(usuario) in ROLES_ADMIN_ENVIO


def _exigir_admin(usuario: Usuario) -> None:
    """403 salvo ASU/GS (D solo consulta; V y C no acceden)."""
    if not _es_admin(usuario):
        raise HTTPException(
            status_code=403,
            detail="Solo Gerente de Sucursal o Administrador gestionan agencias de reparto.",
        )


def _exigir_lectura(usuario: Usuario) -> str:
    """403 salvo ASU/GS/D. Devuelve el rol."""
    rol = _rol_nombre(usuario)
    if rol not in (*ROLES_ADMIN_ENVIO, ROL_REPARTIDOR):
        raise HTTPException(
            status_code=403, detail="No tiene acceso a las agencias de reparto."
        )
    return rol


# ---------------------------------------------------------------------------
# Normalizacion y validacion de datos (funciones puras)
# ---------------------------------------------------------------------------
def normalizar_nit(nit: str) -> str:
    """Quita espacios, guiones, puntos y barras: '1.020-304 050' -> '1020304050'."""
    return _SEPARADORES_NIT.sub("", nit or "")


def normalizar_razon_social(razon_social: str) -> str:
    """Recorta y colapsa espacios internos: '  Andes   Express ' -> 'Andes Express'.

    La unicidad ademas ignora mayusculas (ver `_razon_social_en_uso`)."""
    return _ESPACIOS.sub(" ", (razon_social or "").strip())


def _invalido(detalle: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detalle)


def validar_nit(nit: str) -> str:
    """Devuelve el NIT normalizado o lanza 422 si el formato no es valido."""
    limpio = normalizar_nit(nit)
    if not limpio.isascii() or not limpio.isdigit():
        raise _invalido("El NIT solo puede contener digitos (se ignoran espacios, guiones y puntos).")
    if not NIT_MIN_DIGITOS <= len(limpio) <= NIT_MAX_DIGITOS:
        raise _invalido(
            f"El NIT debe tener entre {NIT_MIN_DIGITOS} y {NIT_MAX_DIGITOS} digitos."
        )
    if int(limpio) == 0:
        raise _invalido("El NIT no puede ser todo ceros.")
    return limpio


def validar_razon_social(razon_social: str) -> str:
    """Devuelve la razon social normalizada o lanza 422."""
    limpia = normalizar_razon_social(razon_social)
    if len(limpia) < RAZON_SOCIAL_MIN:
        raise _invalido(f"La razon social debe tener al menos {RAZON_SOCIAL_MIN} caracteres.")
    if not any(c.isalnum() for c in limpia):
        raise _invalido("La razon social debe contener letras o numeros.")
    return limpia


def validar_correo(correo: str, campo: str = "correo") -> str:
    """Devuelve el correo normalizado (dominio en minusculas) o lanza 422.

    Solo valida sintaxis: no comprueba que el dominio reciba correo."""
    try:
        return validate_email(correo, check_deliverability=False).normalized
    except EmailNotValidError:
        raise _invalido(f"El {campo} no tiene un formato de correo valido.")


def validar_direccion_fiscal(direccion: str) -> str:
    limpia = _ESPACIOS.sub(" ", (direccion or "").strip())
    if len(limpia) < DIRECCION_FISCAL_MIN or not any(c.isalnum() for c in limpia):
        raise _invalido(
            f"La direccion fiscal debe tener al menos {DIRECCION_FISCAL_MIN} caracteres "
            "y contener letras o numeros."
        )
    return limpia


def validar_datos_facturacion(
    *,
    razon_social: str | None = None,
    nit: str | None = None,
    correo_facturacion: str | None = None,
    direccion_fiscal: str | None = None,
) -> dict[str, str]:
    """Valida los datos de facturacion que se pasen (los None se omiten) y
    devuelve solo los valores normalizados. Lanza 422 al primer error.

    VALIDACION LOCAL: no verifica el NIT ante el SIN."""
    normalizados: dict[str, str] = {}
    if razon_social is not None:
        normalizados["razon_social"] = validar_razon_social(razon_social)
    if nit is not None:
        normalizados["nit"] = validar_nit(nit)
    if correo_facturacion is not None:
        normalizados["correo_facturacion"] = validar_correo(
            correo_facturacion, "correo de facturacion"
        )
    if direccion_fiscal is not None:
        normalizados["direccion_fiscal"] = validar_direccion_fiscal(direccion_fiscal)
    return normalizados


def _normalizar_opcionales(datos: dict[str, Any]) -> dict[str, Any]:
    """Normaliza los campos opcionales presentes (correo de contacto valido)."""
    if datos.get("correo") is not None:
        datos["correo"] = validar_correo(datos["correo"], "correo de contacto")
    return datos


# ---------------------------------------------------------------------------
# Duplicados
# ---------------------------------------------------------------------------
def _nit_en_uso(db: Session, nit: str, excluir_id: int | None = None) -> bool:
    query = db.query(AgenciaReparto.id_agencia).filter(AgenciaReparto.nit == nit)
    if excluir_id is not None:
        query = query.filter(AgenciaReparto.id_agencia != excluir_id)
    return query.first() is not None


def _razon_social_en_uso(
    db: Session, razon_social: str, excluir_id: int | None = None
) -> bool:
    """Mismo criterio que el indice unico lower(btrim(razon_social))."""
    query = db.query(AgenciaReparto.id_agencia).filter(
        func.lower(func.btrim(AgenciaReparto.razon_social)) == razon_social.strip().lower()
    )
    if excluir_id is not None:
        query = query.filter(AgenciaReparto.id_agencia != excluir_id)
    return query.first() is not None


def _exigir_sin_duplicados(
    db: Session,
    *,
    nit: str | None,
    razon_social: str | None,
    excluir_id: int | None = None,
) -> None:
    """409 si el NIT o la razon social ya pertenecen a otra agencia."""
    if nit is not None and _nit_en_uso(db, nit, excluir_id):
        raise HTTPException(
            status_code=409, detail=f"Ya existe una agencia con el NIT '{nit}'."
        )
    if razon_social is not None and _razon_social_en_uso(db, razon_social, excluir_id):
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una agencia con la razon social '{razon_social}'.",
        )


def _nombre_restriccion(exc: IntegrityError) -> str:
    """Nombre de la restriccion violada (psycopg2 lo expone en diag; si no,
    se busca en el texto del error)."""
    diag = getattr(exc.orig, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    return nombre or str(exc.orig)


def traducir_integrity_error(exc: IntegrityError) -> HTTPException:
    """IntegrityError -> HTTPException: duplicado 409, FK de envios 409, el
    resto (CHECK/NOT NULL) 422. Nunca un 500."""
    nombre = _nombre_restriccion(exc)
    if _CONSTRAINT_NIT in nombre:
        return HTTPException(status_code=409, detail="Ya existe una agencia con ese NIT.")
    if _CONSTRAINT_RAZON in nombre:
        return HTTPException(
            status_code=409, detail="Ya existe una agencia con esa razon social."
        )
    if "fk_envios_id_agencia" in nombre:
        return HTTPException(
            status_code=409,
            detail=(
                "No se puede eliminar: la agencia tiene envios asociados. "
                "Sugerencia: deshabilitela para conservar el historial."
            ),
        )
    return HTTPException(
        status_code=422, detail="Los datos de la agencia no cumplen las restricciones."
    )


def _confirmar(db: Session) -> None:
    """Commit unico; ante IntegrityError hace rollback y lo traduce."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise traducir_integrity_error(exc) from exc


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def _buscar(db: Session, id_agencia: int, *, bloquear: bool = False) -> AgenciaReparto:
    """404 si no existe. `bloquear=True` toma FOR UPDATE sobre la fila."""
    query = db.query(AgenciaReparto).filter(AgenciaReparto.id_agencia == id_agencia)
    if bloquear:
        query = query.with_for_update(of=AgenciaReparto)
    agencia = query.first()
    if not agencia:
        raise HTTPException(
            status_code=404, detail=f"No existe la agencia con id {id_agencia}."
        )
    return agencia


def obtener_agencia(db: Session, usuario: Usuario, id_agencia: int) -> AgenciaReparto:
    """ASU/GS: cualquiera. D: solo habilitadas (una deshabilitada le responde
    404, igual que si no existiera). V/C: 403."""
    rol = _exigir_lectura(usuario)
    agencia = _buscar(db, id_agencia)
    if rol == ROL_REPARTIDOR and not agencia.is_active:
        raise HTTPException(
            status_code=404, detail=f"No existe la agencia con id {id_agencia}."
        )
    return agencia


def contar_envios(db: Session, id_agencia: int) -> int:
    return (
        db.query(func.count(Envio.id_envio)).filter(Envio.id_agencia == id_agencia).scalar()
        or 0
    )


def listar_agencias(
    db: Session,
    usuario: Usuario,
    *,
    q: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[AgenciaReparto], int]:
    """Listado paginado con visibilidad por rol.

    ASU/GS ven todas (filtro opcional por habilitacion). D solo las
    habilitadas: su filtro `is_active` se ignora. V/C: 403."""
    rol = _exigir_lectura(usuario)
    if q and "\x00" in q:  # PostgreSQL no admite NUL en texto: sin esto seria un 500
        raise _invalido("El filtro 'q' no puede contener el caracter NUL (0x00).")

    query = db.query(AgenciaReparto)
    if rol == ROL_REPARTIDOR:
        query = query.filter(AgenciaReparto.is_active.is_(True))
    elif is_active is not None:
        query = query.filter(AgenciaReparto.is_active.is_(is_active))
    if q and q.strip():
        term = f"%{q.strip()}%"
        filtros = [
            AgenciaReparto.razon_social.ilike(term),
            AgenciaReparto.contacto_operativo.ilike(term),
        ]
        if rol != ROL_REPARTIDOR:  # D no ve ni busca por datos de facturacion
            filtros.append(AgenciaReparto.nit.ilike(term))
        query = query.filter(or_(*filtros))

    total = query.count()
    items = (
        query.order_by(func.lower(AgenciaReparto.razon_social), AgenciaReparto.id_agencia)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return items, total


# ---------------------------------------------------------------------------
# Operaciones (solo ASU/GS)
# ---------------------------------------------------------------------------
def crear_agencia(db: Session, usuario: Usuario, payload: AgenciaCreate) -> AgenciaReparto:
    """Registra una agencia habilitada. 422 datos invalidos, 409 duplicada."""
    _exigir_admin(usuario)

    datos = validar_datos_facturacion(
        razon_social=payload.razon_social,
        nit=payload.nit,
        correo_facturacion=payload.correo_facturacion,
        direccion_fiscal=payload.direccion_fiscal,
    )
    datos.update(
        _normalizar_opcionales(
            {
                "contacto_operativo": payload.contacto_operativo,
                "telefono": payload.telefono,
                "correo": payload.correo,
                "direccion": payload.direccion,
            }
        )
    )
    _exigir_sin_duplicados(db, nit=datos["nit"], razon_social=datos["razon_social"])

    agencia = AgenciaReparto(**datos, is_active=True)
    db.add(agencia)
    _confirmar(db)  # la carrera con otra alta igual la cubren los UNIQUE
    db.refresh(agencia)
    return agencia


def actualizar_agencia(
    db: Session, usuario: Usuario, id_agencia: int, payload: AgenciaUpdate
) -> AgenciaReparto:
    """Actualiza solo los campos enviados (None = no cambiar). No toca
    `is_active` ni los envios existentes."""
    _exigir_admin(usuario)

    enviados = payload.model_dump(exclude_none=True)
    if not enviados:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar.")

    agencia = _buscar(db, id_agencia, bloquear=True)

    datos = validar_datos_facturacion(
        razon_social=enviados.get("razon_social"),
        nit=enviados.get("nit"),
        correo_facturacion=enviados.get("correo_facturacion"),
        direccion_fiscal=enviados.get("direccion_fiscal"),
    )
    datos.update(
        _normalizar_opcionales(
            {c: enviados[c] for c in ("contacto_operativo", "telefono", "correo", "direccion") if c in enviados}
        )
    )
    # Solo se revisa duplicado de lo que realmente cambia.
    _exigir_sin_duplicados(
        db,
        nit=datos["nit"] if datos.get("nit") not in (None, agencia.nit) else None,
        razon_social=(
            datos["razon_social"]
            if datos.get("razon_social") is not None
            and datos["razon_social"].lower() != agencia.razon_social.strip().lower()
            else None
        ),
        excluir_id=agencia.id_agencia,
    )

    for campo, valor in datos.items():
        setattr(agencia, campo, valor)
    _confirmar(db)
    db.refresh(agencia)
    return agencia


def cambiar_estado(
    db: Session, usuario: Usuario, id_agencia: int, is_active: bool
) -> AgenciaReparto:
    """Habilita/deshabilita. Idempotente. NO modifica envios historicos: una
    agencia deshabilitada solo deja de ofrecerse para nuevas asignaciones."""
    _exigir_admin(usuario)
    agencia = _buscar(db, id_agencia, bloquear=True)
    if agencia.is_active != is_active:
        agencia.is_active = is_active
        _confirmar(db)
        db.refresh(agencia)
    else:
        db.rollback()  # libera el FOR UPDATE sin cambios
    return agencia


def eliminar_agencia(db: Session, usuario: Usuario, id_agencia: int) -> str:
    """Eliminacion fisica. 409 si tiene envios (se sugiere deshabilitar).

    Sus zonas y tarifas (aun sin funcionalidad) se van en cascada. Devuelve
    la razon social eliminada para el mensaje."""
    _exigir_admin(usuario)
    agencia = _buscar(db, id_agencia, bloquear=True)

    envios = contar_envios(db, agencia.id_agencia)
    if envios > 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede eliminar: la agencia '{agencia.razon_social}' tiene "
                f"{envios} envio(s) asociado(s). Sugerencia: deshabilitela para "
                "conservar el historial."
            ),
        )

    razon_social = agencia.razon_social
    db.delete(agencia)
    _confirmar(db)  # un envio asignado en paralelo dispara la FK -> 409
    return razon_social


# ---------------------------------------------------------------------------
# Serializacion (dicts listos para el envelope)
# ---------------------------------------------------------------------------
def serializar_agencia(agencia: AgenciaReparto, usuario: Usuario) -> dict:
    """ASU/GS: vista completa. D: resumen sin datos de facturacion."""
    esquema = AgenciaRead if _es_admin(usuario) else AgenciaResumen
    return esquema.model_validate(agencia).model_dump()


def serializar_detalle(db: Session, agencia: AgenciaReparto, usuario: Usuario) -> dict:
    """Detalle con totales (ASU/GS). D recibe el mismo resumen del listado."""
    if not _es_admin(usuario):
        return serializar_agencia(agencia, usuario)
    base = AgenciaRead.model_validate(agencia).model_dump()
    return AgenciaDetalle(
        **base,
        total_zonas=len(agencia.zonas),
        total_envios=contar_envios(db, agencia.id_agencia),
    ).model_dump()
