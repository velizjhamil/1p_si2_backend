# backend/app/modules/delivery/tarifas_service.py
# CU19 - tarifas de las zonas de cobertura de las agencias de reparto.
#
# Extension separada (agencias_service.py y zonas_service.py no se modifican):
# reutiliza la autorizacion/visibilidad de zonas (`obtener_zona`, que a su vez
# usa la de agencias: D solo ve agencias habilitadas), `_agencia_para_escritura`
# (403, 404, FOR UPDATE, agencia habilitada 400) y `_buscar_zona`.
# Cotizacion, /disponibles y la asignacion a envios NO estan aca.
#
# MODELO (Fase 2, sin cambios): agencia_tarifas(id_zona FK CASCADE, criterio
# PESO|VOLUMEN, rango_min, rango_max NULL, costo, vigente_desde, vigente_hasta
# NULL, is_active) con CHECK de criterio, rango, costo y vigencia. NO hay
# restriccion de solapamiento en la DB (sin EXCLUDE/btree_gist): la valida este
# servicio.
#
# SEMANTICA
# - Rango: [rango_min, rango_max); el maximo NO se incluye. rango_max NULL =
#   tramo abierto (sin tope, se compara como +infinito). [0,5) y [5,10) son
#   contiguos y NO se solapan; [5, abierto) y [0,10) si.
# - Vigencia: [vigente_desde, vigente_hasta], ambas fechas se incluyen.
#   vigente_hasta NULL = sin fin. [1-ene, 31-ene] y [1-feb, ...) no se solapan;
#   compartir un dia si.
# - Solapamiento (409): misma zona + mismo criterio + rangos que se intersectan
#   + vigencias que se intersectan. PESO y VOLUMEN nunca se bloquean entre si;
#   una tarifa expirada no bloquea una futura si los periodos no se cruzan.
#   Solo cuentan las tarifas ACTIVAS (una desactivada no es aplicable y no
#   bloquea; al reactivarla se vuelve a validar).
# - `vigente` (respuesta) = activa y hoy en [desde, hasta]. "Hoy" es la fecha
#   UTC, igual que el resto de CU18/CU19.
# - Concurrencia: la validacion de solapamiento no puede apoyarse en la DB, asi
#   que toda escritura bloquea la fila de la agencia y de la zona FOR UPDATE:
#   dos altas simultaneas en la misma zona se serializan y la segunda ve a la
#   primera (y responde 409).
# - Habilitacion: agencia deshabilitada -> no se crean ni actualizan tarifas
#   (400); si se pueden consultar (ASU/GS) y ELIMINAR (quitar una tarifa no es
#   una configuracion nueva).
# - Eliminacion: 409 si un envio referencia la tarifa (snapshot historico; la
#   FK RESTRICT lo garantiza): se sugiere desactivarla.
# - Autorizacion: ASU/GS administran; D solo consulta tarifas de agencias
#   habilitadas; V/C 403.
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.delivery.agencias_service import (
    _buscar,
    _exigir_admin,
    _nombre_restriccion,
)
from app.modules.delivery.models import AgenciaTarifa, AgenciaZona
from app.modules.delivery.zonas_service import (
    _agencia_para_escritura,
    _buscar_zona,
    obtener_zona,
)
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_tarifa import TarifaCreate, TarifaRead, TarifaUpdate

_INFINITO = Decimal("Infinity")
_SIN_FIN = date.max

# Campos que un PUT puede cambiar.
_CAMPOS = ("criterio", "rango_min", "rango_max", "costo", "vigente_desde", "vigente_hasta", "is_active")

_MENSAJES_CHECK = {
    "ck_agencia_tarifas_criterio_valido": "El criterio debe ser PESO o VOLUMEN.",
    "ck_agencia_tarifas_rango_min_no_negativo": "El rango minimo no puede ser negativo.",
    "ck_agencia_tarifas_rango_max_mayor_que_min": "El rango maximo debe ser mayor que el minimo.",
    "ck_agencia_tarifas_costo_no_negativo": "El costo no puede ser negativo.",
    "ck_agencia_tarifas_vigencia_valida": "La vigencia final no puede ser anterior a la inicial.",
}


# ---------------------------------------------------------------------------
# Reglas puras (sin DB)
# ---------------------------------------------------------------------------
def fecha_hoy() -> date:
    """'Hoy' para decidir la vigencia: fecha UTC (igual que CU18)."""
    return datetime.now(timezone.utc).date()


def _invalido(detalle: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detalle)


def validar_rango(rango_min: Decimal, rango_max: Decimal | None) -> None:
    """422 si el rango es imposible. Rango [min, max): min >= 0 y, si hay max,
    max > min (un rango de ancho cero no contiene ningun valor)."""
    if rango_min < 0:
        raise _invalido("El rango minimo no puede ser negativo.")
    if rango_max is not None and rango_max <= rango_min:
        raise _invalido(
            "El rango maximo debe ser mayor que el minimo (el maximo no se incluye en el rango)."
        )


def validar_vigencia(vigente_desde: date, vigente_hasta: date | None) -> None:
    """422 si la vigencia final es anterior a la inicial (hasta NULL = sin fin)."""
    if vigente_hasta is not None and vigente_hasta < vigente_desde:
        raise _invalido("La vigencia final no puede ser anterior a la vigencia inicial.")


def validar_costo(costo: Decimal) -> None:
    if costo < 0:
        raise _invalido("El costo no puede ser negativo.")


def rangos_se_intersectan(min1: Decimal, max1: Decimal | None, min2: Decimal, max2: Decimal | None) -> bool:
    """[min1, max1) y [min2, max2) comparten algun valor. max NULL = +infinito."""
    fin1 = _INFINITO if max1 is None else max1
    fin2 = _INFINITO if max2 is None else max2
    return min1 < fin2 and min2 < fin1


def vigencias_se_intersectan(desde1: date, hasta1: date | None, desde2: date, hasta2: date | None) -> bool:
    """[desde1, hasta1] y [desde2, hasta2] comparten algun dia. hasta NULL = sin fin."""
    fin1 = _SIN_FIN if hasta1 is None else hasta1
    fin2 = _SIN_FIN if hasta2 is None else hasta2
    return desde1 <= fin2 and desde2 <= fin1


def tarifas_se_solapan(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Mismo criterio + rangos que se intersectan + vigencias que se intersectan.
    (La misma zona la garantiza quien arma la lista de candidatas.)"""
    return (
        a["criterio"] == b["criterio"]
        and rangos_se_intersectan(a["rango_min"], a["rango_max"], b["rango_min"], b["rango_max"])
        and vigencias_se_intersectan(a["vigente_desde"], a["vigente_hasta"], b["vigente_desde"], b["vigente_hasta"])
    )


def vigente_en(t: AgenciaTarifa | dict[str, Any], hoy: date) -> bool:
    """Aplicable en `hoy`: activa y `hoy` dentro de [desde, hasta]."""
    g = t.get if isinstance(t, dict) else lambda k: getattr(t, k)
    hasta = _SIN_FIN if g("vigente_hasta") is None else g("vigente_hasta")
    return bool(g("is_active")) and g("vigente_desde") <= hoy <= hasta


def encontrar_solapada(candidata: dict[str, Any], otras: Iterable[AgenciaTarifa]) -> AgenciaTarifa | None:
    """Primera tarifa de `otras` (ordenada por id) que se solapa con `candidata`."""
    for otra in sorted(otras, key=lambda t: t.id_tarifa):
        if tarifas_se_solapan(candidata, _datos(otra)):
            return otra
    return None


def _datos(t: AgenciaTarifa) -> dict[str, Any]:
    return {c: getattr(t, c) for c in _CAMPOS}


def _num(valor: Decimal) -> str:
    """Decimal sin ceros sobrantes: 5.000 -> '5', 0.500 -> '0.5', 0E-3 -> '0'."""
    return format(valor.normalize(), "f") if valor != 0 else "0"


def _descripcion(t: AgenciaTarifa) -> str:
    tope = "en adelante" if t.rango_max is None else _num(t.rango_max)
    hasta = "sin fin" if t.vigente_hasta is None else t.vigente_hasta.isoformat()
    return (
        f"#{t.id_tarifa} ({t.criterio} [{_num(t.rango_min)}, {tope}) "
        f"vigente {t.vigente_desde.isoformat()} a {hasta})"
    )


# ---------------------------------------------------------------------------
# Errores de base de datos
# ---------------------------------------------------------------------------
def traducir_integrity_error(exc: IntegrityError) -> HTTPException:
    """IntegrityError -> HTTPException. CHECK -> 422, zona desaparecida -> 404,
    envios que referencian la tarifa -> 409. Nunca un 500."""
    nombre = _nombre_restriccion(exc)
    for check, mensaje in _MENSAJES_CHECK.items():
        if check in nombre:
            return HTTPException(status_code=422, detail=mensaje)
    if "fk_agencia_tarifas_id_zona" in nombre:
        return HTTPException(status_code=404, detail="La zona indicada ya no existe.")
    if "fk_envios_id_tarifa_aplicada" in nombre:
        return HTTPException(
            status_code=409,
            detail=(
                "No se puede eliminar: hay envios asociados a esta tarifa. "
                "Sugerencia: desactivela para conservar el historial."
            ),
        )
    return HTTPException(status_code=422, detail="Los datos de la tarifa no cumplen las restricciones.")


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
def _buscar_tarifa(db: Session, id_zona: int, id_tarifa: int, *, bloquear: bool = False) -> AgenciaTarifa:
    """404 si la tarifa no existe O pertenece a otra zona."""
    query = db.query(AgenciaTarifa).filter(
        AgenciaTarifa.id_tarifa == id_tarifa, AgenciaTarifa.id_zona == id_zona
    )
    if bloquear:
        query = query.with_for_update(of=AgenciaTarifa)
    tarifa = query.first()
    if tarifa is None:
        raise HTTPException(
            status_code=404, detail=f"La zona {id_zona} no tiene una tarifa con id {id_tarifa}."
        )
    return tarifa


def _activas_de_la_zona(db: Session, id_zona: int, criterio: str, excluir_id: int | None) -> list[AgenciaTarifa]:
    query = db.query(AgenciaTarifa).filter(
        AgenciaTarifa.id_zona == id_zona,
        AgenciaTarifa.criterio == criterio,
        AgenciaTarifa.is_active.is_(True),
    )
    if excluir_id is not None:
        query = query.filter(AgenciaTarifa.id_tarifa != excluir_id)
    return query.all()


def _exigir_sin_solapamiento(db: Session, id_zona: int, candidata: dict[str, Any], excluir_id: int | None = None) -> None:
    """409 si la tarifa candidata (solo si esta activa) se solapa con otra activa
    de la misma zona y criterio."""
    if not candidata["is_active"]:
        return
    otras = _activas_de_la_zona(db, id_zona, candidata["criterio"], excluir_id)
    choque = encontrar_solapada(candidata, otras)
    if choque is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La tarifa se solapa con la tarifa {_descripcion(choque)} de esta zona: "
                "mismo criterio, rangos y vigencias que se cruzan."
            ),
        )


def _validar(datos: dict[str, Any]) -> None:
    validar_rango(datos["rango_min"], datos["rango_max"])
    validar_costo(datos["costo"])
    validar_vigencia(datos["vigente_desde"], datos["vigente_hasta"])


def _zona_para_escritura(db: Session, usuario: Usuario, id_agencia: int, id_zona: int) -> AgenciaZona:
    """ASU/GS; agencia existente y habilitada (FOR UPDATE); zona de esa agencia
    (FOR UPDATE). Los dos bloqueos serializan las escrituras de la zona."""
    agencia = _agencia_para_escritura(db, usuario, id_agencia)
    return _buscar_zona(db, agencia.id_agencia, id_zona, bloquear=True)


# ---------------------------------------------------------------------------
# Consultas (ASU/GS/D)
# ---------------------------------------------------------------------------
def listar_tarifas(db: Session, usuario: Usuario, id_agencia: int, id_zona: int) -> list[AgenciaTarifa]:
    """Tarifas de la zona ordenadas por criterio, rango y vigencia. ASU/GS:
    cualquier agencia (historico incluido); D: solo agencias habilitadas (404 si
    no); V/C: 403."""
    zona = obtener_zona(db, usuario, id_agencia, id_zona)
    return sorted(zona.tarifas, key=lambda t: (t.criterio, t.rango_min, t.vigente_desde, t.id_tarifa))


def obtener_tarifa(db: Session, usuario: Usuario, id_agencia: int, id_zona: int, id_tarifa: int) -> AgenciaTarifa:
    obtener_zona(db, usuario, id_agencia, id_zona)  # autorizacion + visibilidad + 404 de agencia/zona
    return _buscar_tarifa(db, id_zona, id_tarifa)


# ---------------------------------------------------------------------------
# Operaciones (solo ASU/GS)
# ---------------------------------------------------------------------------
def crear_tarifa(db: Session, usuario: Usuario, id_agencia: int, id_zona: int, payload: TarifaCreate) -> AgenciaTarifa:
    """Orden: rol (403) -> agencia (404) -> habilitada (400) -> zona (404) ->
    rango/costo/vigencia (422) -> solapamiento (409)."""
    zona = _zona_para_escritura(db, usuario, id_agencia, id_zona)
    datos = {c: getattr(payload, c) for c in _CAMPOS}
    _validar(datos)
    _exigir_sin_solapamiento(db, zona.id_zona, datos)

    tarifa = AgenciaTarifa(id_zona=zona.id_zona, **datos)
    db.add(tarifa)
    _confirmar(db)
    db.refresh(tarifa)
    return tarifa


def actualizar_tarifa(
    db: Session, usuario: Usuario, id_agencia: int, id_zona: int, id_tarifa: int, payload: TarifaUpdate
) -> AgenciaTarifa:
    """Cambia solo los campos enviados. `rango_max`/`vigente_hasta` en null
    explicito = tramo abierto / sin fin. Se valida el resultado completo
    (rango, vigencia, costo y solapamiento contra las OTRAS tarifas activas)."""
    _exigir_admin(usuario)
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar.")

    zona = _zona_para_escritura(db, usuario, id_agencia, id_zona)
    tarifa = _buscar_tarifa(db, zona.id_zona, id_tarifa, bloquear=True)

    actuales = _datos(tarifa)
    datos = {**actuales, **{c: getattr(payload, c) for c in payload.model_fields_set}}
    if datos == actuales:
        db.rollback()  # sin cambios reales: libera los bloqueos
        return tarifa

    _validar(datos)
    _exigir_sin_solapamiento(db, zona.id_zona, datos, excluir_id=tarifa.id_tarifa)
    for campo in _CAMPOS:
        setattr(tarifa, campo, datos[campo])
    _confirmar(db)
    db.refresh(tarifa)
    return tarifa


def eliminar_tarifa(db: Session, usuario: Usuario, id_agencia: int, id_zona: int, id_tarifa: int) -> str:
    """Elimina fisicamente la tarifa. Permitido con la agencia deshabilitada.
    409 si un envio la referencia (la FK RESTRICT); se sugiere desactivarla."""
    _exigir_admin(usuario)
    agencia = _buscar(db, id_agencia, bloquear=True)
    zona = _buscar_zona(db, agencia.id_agencia, id_zona, bloquear=True)
    tarifa = _buscar_tarifa(db, zona.id_zona, id_tarifa, bloquear=True)

    descripcion = _descripcion(tarifa)
    db.delete(tarifa)
    _confirmar(db)
    return descripcion


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------
def serializar_tarifa(tarifa: AgenciaTarifa, id_agencia: int, hoy: date | None = None) -> dict:
    """Misma forma para ASU/GS y D (una tarifa no contiene datos de facturacion)."""
    return TarifaRead(
        id_tarifa=tarifa.id_tarifa,
        id_zona=tarifa.id_zona,
        id_agencia=id_agencia,
        criterio=tarifa.criterio,
        rango_min=tarifa.rango_min,
        rango_max=tarifa.rango_max,
        costo=tarifa.costo,
        vigente_desde=tarifa.vigente_desde,
        vigente_hasta=tarifa.vigente_hasta,
        is_active=tarifa.is_active,
        vigente=vigente_en(tarifa, hoy or fecha_hoy()),
        fecha_creacion=tarifa.fecha_creacion,
        fecha_actualizacion=tarifa.fecha_actualizacion,
    ).model_dump()
