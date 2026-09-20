# backend/app/modules/delivery/cotizacion_service.py
# CU19 - COTIZACION del costo de agencia para una ciudad, un peso y un volumen.
#
# Solo LECTURA: no asigna la agencia a ningun envio ni persiste nada. Guardar
# `costo_agencia`/`id_tarifa_aplicada`/peso/volumen en `envios` es trabajo de
# asignacion_agencia_service, que LLAMA a `cotizar` (esta es la unica fuente de
# la regla de seleccion). No toca checkout, ventas ni productos. Las dimensiones
# llegan explicitamente en la consulta.
#
# `costo_agencia` = costo INTERNO que se pagaria a la agencia. NO es
# `ventas.costo_envio` (importe cobrado al cliente).
#
# FLUJO Y CODIGOS HTTP
#   rol V/C ................................. 403 (antes de tocar la BD)
#   dimension invalida ...................... 422 (>0, finita, <= 10 digitos con 3 decimales)
#   agencia inexistente ..................... 404
#   agencia deshabilitada ................... 400 para ASU/GS; 404 para D (D no ve
#                                             agencias deshabilitadas, igual que en
#                                             el resto del CU19). Ningun rol cotiza.
#   ciudad vacia / inexistente / ambigua .... 422 / 404 / 409 (misma resolucion de
#                                             Fase 5/7: `resolver_ciudad_consultada`)
#   la agencia no tiene zona en la ciudad ... 404 "no tiene cobertura"
#   cubre la ciudad, sin tarifa aplicable ... 404 "no existe tarifa aplicable" (mensaje
#                                             distinto del de cobertura)
#   varias candidatas equivalentes .......... 409 (ambiguedad, ver regla 4)
#
# COBERTURA: al menos una agencia_zona de la agencia en la ciudad. Una zona con
# solo subzona tambien cubre la ciudad (todavia no existe la subzona de la
# direccion de entrega).
#
# TARIFA CANDIDATA (todas las condiciones)
#   - pertenece a una zona de la agencia que cubre la ciudad;
#   - is_active;
#   - vigente hoy (fecha UTC): vigente_desde <= hoy <= vigente_hasta (NULL = sin fin);
#   - su criterio corresponde a la dimension: PESO -> peso_kg, VOLUMEN -> volumen_m3;
#   - el valor esta en su rango [rango_min, rango_max): valor >= rango_min y, si
#     rango_max no es NULL, valor < rango_max (NULL = sin limite superior).
# Se evaluan AMBAS dimensiones. No se requiere que la agencia tenga tarifas de las dos.
#
# REGLA DE SELECCION (decision de negocio CONGELADA para CU19)
#   1. Si aplica una sola tarifa, esa es.
#   2. Si aplican varias (PESO y VOLUMEN, y/o de varias zonas de la misma ciudad):
#        a) gana la de MAYOR costo;
#        b) si empatan en costo, PESO antes que VOLUMEN;
#        c) si continua el empate, la zona de ciudad completa (sin subzona) antes
#           que una zona con subzona;
#        d) si aun asi queda mas de una candidata equivalente -> 409 por ambiguedad.
#   NO se desempata por id_tarifa ni por ningun identificador tecnico.
#   (Un solo tramo por zona+criterio+dimension esta garantizado por la validacion
#   de solapamiento, asi que dentro de una zona no hay empates del mismo criterio.)
#
# EFICIENCIA: una consulta liviana para la agencia y UNA consulta con las zonas de
# la ciudad + sus tarifas activas (outer join: una zona sin tarifas tambien
# cuenta como cobertura). Vigencia, rango y seleccion se resuelven en Python con
# funciones puras. El numero de sentencias no depende de cuantas zonas o tarifas
# tenga la agencia.
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.delivery.agencias_service import _exigir_lectura
from app.modules.delivery.disponibilidad_service import (
    resolver_ciudad_consultada,
    serializar_ciudad,
)
from app.modules.delivery.models import AgenciaReparto, AgenciaTarifa, AgenciaZona
from app.modules.delivery.service import ROL_REPARTIDOR
from app.modules.delivery.tarifas_service import fecha_hoy, vigente_en
from app.modules.usuarios.models import Usuario
from app.schemas.agencia_cotizacion import (
    AgenciaCotizada,
    CandidataCotizacion,
    CotizacionRead,
    TarifaAplicada,
)

# Limites de las columnas envios.peso_kg / envios.volumen_m3: Numeric(10, 3).
DECIMALES_MAX = 3
DIGITOS_ENTEROS_MAX = 7


# ---------------------------------------------------------------------------
# Dimensiones (funciones puras)
# ---------------------------------------------------------------------------
def _invalido(detalle: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detalle)


def _a_decimal(valor: Any, campo: str) -> Decimal:
    try:
        d = valor if isinstance(valor, Decimal) else Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise _invalido(f"{campo} debe ser un numero.")
    return d


def validar_dimension(valor: Any, campo: str) -> Decimal:
    """Devuelve la dimension como Decimal o lanza 422: numero finito (sin NaN ni
    Infinity), > 0 y que cabe en Numeric(10, 3) (hasta 7 enteros y 3 decimales;
    los ceros a la derecha no cuentan)."""
    d = _a_decimal(valor, campo)
    if not d.is_finite():
        raise _invalido(f"{campo} debe ser un numero finito (no se admite NaN ni Infinity).")
    if d <= 0:
        raise _invalido(f"{campo} debe ser mayor que 0.")
    signo, digitos, exponente = d.normalize().as_tuple()
    decimales = max(0, -exponente)
    enteros = max(0, len(digitos) + exponente)
    if decimales > DECIMALES_MAX or enteros > DIGITOS_ENTEROS_MAX:
        raise _invalido(
            f"{campo} admite hasta {DIGITOS_ENTEROS_MAX} digitos enteros y {DECIMALES_MAX} decimales."
        )
    return d


def validar_dimensiones(peso_kg: Any, volumen_m3: Any) -> tuple[Decimal, Decimal]:
    return validar_dimension(peso_kg, "peso_kg"), validar_dimension(volumen_m3, "volumen_m3")


def rango_contiene(rango_min: Decimal, rango_max: Decimal | None, valor: Decimal) -> bool:
    """[rango_min, rango_max): minimo incluido, maximo excluido, NULL = sin tope."""
    return valor >= rango_min and (rango_max is None or valor < rango_max)


# ---------------------------------------------------------------------------
# Candidatas y seleccion (funciones puras)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidata:
    id_tarifa: int
    id_zona: int
    nombre_zona: str | None
    criterio: str
    rango_min: Decimal
    rango_max: Decimal | None
    costo: Decimal
    vigente_desde: date
    vigente_hasta: date | None
    is_active: bool = True

    @property
    def zona_completa(self) -> bool:
        """Zona de ciudad completa = sin subzona."""
        return not (self.nombre_zona or "").strip()

    @property
    def prioridad(self) -> tuple:
        """Clave de la regla de seleccion (menor = preferida): mayor costo, luego
        PESO antes que VOLUMEN, luego zona de ciudad completa. Sin ids."""
        return (-self.costo, 0 if self.criterio == "PESO" else 1, 0 if self.zona_completa else 1)

    @property
    def orden(self) -> tuple:
        """Orden total y determinista para listar (prioridad y luego nombre de
        subzona; el nombre es unico por agencia+ciudad y la zona no repite criterio)."""
        return (*self.prioridad, (self.nombre_zona or "").strip().lower(), self.criterio)


def candidatas(filas, peso_kg: Decimal, volumen_m3: Decimal, hoy: date) -> list[Candidata]:
    """Tarifas que aplican: con tarifa (id_tarifa no nulo), activas, vigentes hoy,
    y con el valor de SU dimension dentro de su rango [min, max)."""
    valor_de = {"PESO": peso_kg, "VOLUMEN": volumen_m3}
    resultado = []
    for f in filas:
        if f.id_tarifa is None or f.criterio not in valor_de:
            continue
        c = Candidata(
            id_tarifa=f.id_tarifa, id_zona=f.id_zona, nombre_zona=f.nombre_zona, criterio=f.criterio,
            rango_min=f.rango_min, rango_max=f.rango_max, costo=f.costo,
            vigente_desde=f.vigente_desde, vigente_hasta=f.vigente_hasta, is_active=f.is_active,
        )
        if vigente_en(c, hoy) and rango_contiene(c.rango_min, c.rango_max, valor_de[c.criterio]):
            resultado.append(c)
    return resultado


def seleccionar(cands: list[Candidata]) -> tuple[Candidata, list[Candidata]]:
    """Aplica la regla congelada y devuelve (elegida, todas en orden de preferencia).
    409 si tras mayor costo -> PESO -> ciudad completa siguen empatadas 2+."""
    if not cands:
        raise ValueError("seleccionar() requiere al menos una candidata")
    ordenadas = sorted(cands, key=lambda c: c.orden)
    mejores = [c for c in ordenadas if c.prioridad == ordenadas[0].prioridad]
    if len(mejores) > 1:
        zonas = ", ".join(sorted(f"'{c.nombre_zona}'" if not c.zona_completa else "ciudad completa" for c in mejores))
        raise HTTPException(
            status_code=409,
            detail=(
                f"Hay {len(mejores)} tarifas equivalentes ({mejores[0].criterio}, costo {_num(mejores[0].costo)}, "
                f"{'ciudad completa' if mejores[0].zona_completa else 'subzona'}) en las zonas: {zonas}. "
                "No se puede determinar la tarifa aplicable."
            ),
        )
    return ordenadas[0], ordenadas


def _num(valor: Decimal) -> str:
    return format(valor.normalize(), "f") if valor != 0 else "0"


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def _agencia_cotizable(db: Session, rol: str, id_agencia: int):
    """Fila liviana (id, razon_social) de una agencia HABILITADA. 404 si no
    existe (o si es deshabilitada y el rol es D); 400 si esta deshabilitada."""
    fila = (
        db.query(AgenciaReparto.id_agencia, AgenciaReparto.razon_social, AgenciaReparto.is_active)
        .filter(AgenciaReparto.id_agencia == id_agencia)
        .first()
    )
    if fila is None or (rol == ROL_REPARTIDOR and not fila.is_active):
        raise HTTPException(status_code=404, detail=f"No existe la agencia con id {id_agencia}.")
    if not fila.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"La agencia '{fila.razon_social}' esta deshabilitada: no puede cotizar.",
        )
    return fila


def _zonas_y_tarifas(db: Session, id_agencia: int, id_ciudad: int):
    """UNA consulta: zonas de la agencia en la ciudad + sus tarifas activas
    (outer join, asi una zona sin tarifas tambien aparece y prueba la cobertura)."""
    return (
        db.query(
            AgenciaZona.id_zona,
            AgenciaZona.nombre_zona,
            AgenciaTarifa.id_tarifa,
            AgenciaTarifa.criterio,
            AgenciaTarifa.rango_min,
            AgenciaTarifa.rango_max,
            AgenciaTarifa.costo,
            AgenciaTarifa.vigente_desde,
            AgenciaTarifa.vigente_hasta,
            AgenciaTarifa.is_active,
        )
        .outerjoin(
            AgenciaTarifa,
            (AgenciaTarifa.id_zona == AgenciaZona.id_zona) & AgenciaTarifa.is_active.is_(True),
        )
        .filter(AgenciaZona.id_agencia == id_agencia, AgenciaZona.id_ciudad == id_ciudad)
        .order_by(func.lower(func.coalesce(AgenciaZona.nombre_zona, "")), AgenciaTarifa.criterio)
        .all()
    )


# ---------------------------------------------------------------------------
# Operacion
# ---------------------------------------------------------------------------
def cotizar(
    db: Session,
    usuario: Usuario,
    id_agencia: int,
    ciudad: str | None,
    peso_kg: Any,
    volumen_m3: Any,
    hoy: date | None = None,
) -> dict:
    """Cotiza el costo de agencia. Solo lectura (no hace add/delete/commit)."""
    rol = _exigir_lectura(usuario)                       # 403 a V/C antes de tocar la BD
    peso, volumen = validar_dimensiones(peso_kg, volumen_m3)   # 422
    agencia = _agencia_cotizable(db, rol, id_agencia)          # 404 / 400
    ciudad_ = resolver_ciudad_consultada(db, ciudad)           # 422 / 404 / 409 (Fase 5/7)
    hoy = hoy or fecha_hoy()

    filas = _zonas_y_tarifas(db, id_agencia, ciudad_.id)
    if not filas:
        raise HTTPException(
            status_code=404,
            detail=f"La agencia '{agencia.razon_social}' no tiene cobertura en {ciudad_.nombre}.",
        )
    cands = candidatas(filas, peso, volumen, hoy)
    if not cands:
        raise HTTPException(
            status_code=404,
            detail=(
                f"La agencia '{agencia.razon_social}' cubre {ciudad_.nombre}, pero no existe tarifa "
                f"aplicable para peso {_num(peso)} kg y volumen {_num(volumen)} m3 "
                f"(tarifas activas y vigentes al {hoy.isoformat()})."
            ),
        )
    elegida, ordenadas = seleccionar(cands)                    # 409 si hay ambiguedad

    return CotizacionRead(
        agencia=AgenciaCotizada(id_agencia=agencia.id_agencia, razon_social=agencia.razon_social),
        ciudad=serializar_ciudad(ciudad_),
        peso_kg=peso,
        volumen_m3=volumen,
        fecha_referencia=hoy,
        criterio=elegida.criterio,
        costo_agencia=elegida.costo,
        tarifa=TarifaAplicada(
            id_tarifa=elegida.id_tarifa, id_zona=elegida.id_zona, nombre_zona=elegida.nombre_zona,
            criterio=elegida.criterio, rango_min=elegida.rango_min, rango_max=elegida.rango_max,
            costo=elegida.costo, vigente_desde=elegida.vigente_desde, vigente_hasta=elegida.vigente_hasta,
        ),
        candidatas=[
            CandidataCotizacion(
                id_tarifa=c.id_tarifa, criterio=c.criterio, costo=c.costo,
                nombre_zona=c.nombre_zona, seleccionada=c is elegida,
            )
            for c in ordenadas
        ],
    ).model_dump()
