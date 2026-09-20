# backend/app/modules/delivery/asignacion_agencia_service.py
# CU19 - ASIGNACION de una agencia de reparto a un envio existente.
#
# Se integra en el endpoint de CU18 `PATCH /envios/{id}/asignar` (payload con
# `id_agencia` en lugar de `id_repartidor`) y reutiliza, sin duplicarlo:
#   - CU18 (`delivery/service.py`): obtener_envio (404 + FOR UPDATE), permisos
#     (_exigir_permiso), maquina de estados (_validar_transicion: solo desde
#     LISTO_ENVIO), historial (_cambiar_estado), fecha futura, serializacion.
#   - CU19 Fase 8 (`cotizacion_service.cotizar`): agencia habilitada, resolucion
#     de la ciudad, cobertura, tarifas candidatas y la regla de seleccion
#     CONGELADA (mayor costo -> PESO -> ciudad completa -> 409). Aqui NO existe una
#     segunda implementacion de esa seleccion: se llama a `cotizar` y se guarda su
#     resultado.
#   - `agencias_service._buscar` (404 + FOR UPDATE de la agencia).
#
# CIUDAD del envio: el snapshot de la venta (`ventas.ciudad`, texto libre), que se
# resuelve por nombre normalizado igual que en Fase 5/7/8 (404 no existe, 409
# ambigua). No se modifica `ventas.ciudad`.
#
# FLUJO Y CODIGOS HTTP (el primer error corta y NO se persiste nada)
#   envio inexistente ........................ 404
#   rol distinto de ASU/GS (D, V, C) ......... 403
#   envio fuera de LISTO_ENVIO (ya asignado,
#     en ruta, cancelado...) ................. 409  (evita doble asignacion)
#   envio con repartidor ..................... 409  (exclusion mutua)
#   peso/volumen invalidos ................... 422
#   fecha estimada no futura ................. 400  (igual que CU18)
#   agencia inexistente ...................... 404
#   agencia deshabilitada .................... 400
#   ciudad vacia / inexistente / ambigua ..... 422 / 404 / 409
#   agencia sin cobertura en la ciudad ....... 404 ("no tiene cobertura")
#   sin tarifa aplicable ..................... 404 ("no existe tarifa aplicable")
#   tarifas candidatas equivalentes .......... 409 (ambiguedad de Fase 8)
#
# QUE SE GUARDA en `envios` (snapshot; el costo/tarifa NO cambian si luego se edita
# la tarifa): id_agencia, id_tarifa_aplicada, costo_agencia (costo INTERNO que se
# pagara a la agencia; NO es ventas.costo_envio), peso_kg y volumen_m3 recibidos,
# id_repartidor queda NULL. El envio pasa a ASIGNADO con una fila de historial
# ("Asignado a agencia X"; el costo interno no se escribe ahi porque el cliente
# puede leer el historial de su envio).
#
# ATOMICIDAD Y CONCURRENCIA: una sola transaccion con un unico commit al final. Se
# bloquea primero el envio (FOR UPDATE, igual que CU18) y luego la agencia (FOR
# UPDATE, igual que las escrituras de zonas/tarifas y el cambio de estado): mientras
# se cotiza y se escribe nadie puede deshabilitar/eliminar la agencia ni cambiar sus
# zonas o tarifas, y dos asignaciones simultaneas al mismo envio se serializan (la
# segunda ve ASIGNADO y recibe 409). Los CHECK/FK de la DB (repartidor_o_agencia,
# snapshot completo) son la ultima defensa y se traducen a 409/422, nunca a 500.
#
# NO incluye: gestion de conductores de la agencia, cambios de estado adicionales
# de CU18 ni desasignacion.
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.delivery.agencias_service import _buscar, _nombre_restriccion
from app.modules.delivery.cotizacion_service import cotizar, validar_dimensiones
from app.modules.delivery.models import Envio
from app.modules.delivery.service import (
    _cambiar_estado,
    _exigir_fecha_futura,
    _exigir_permiso,
    _texto,
    _validar_transicion,
    obtener_envio,
)
from app.modules.usuarios.models import Usuario
from app.schemas.envio import AsignarEnvioPayload


def traducir_integrity_error(exc: IntegrityError) -> HTTPException:
    """IntegrityError -> HTTPException. Nunca un 500."""
    nombre = _nombre_restriccion(exc)
    if "ck_envios_repartidor_o_agencia" in nombre:
        return HTTPException(
            status_code=409,
            detail="El envio no puede tener a la vez repartidor y agencia.",
        )
    if "fk_envios_id_agencia" in nombre or "fk_envios_id_tarifa_aplicada" in nombre:
        return HTTPException(
            status_code=409,
            detail="La agencia o la tarifa cambiaron durante la asignacion. Reintente.",
        )
    if "ck_envios_" in nombre:  # snapshot incompleto, costo negativo...
        return HTTPException(
            status_code=422, detail="Los datos de la asignacion no cumplen las restricciones."
        )
    return HTTPException(
        status_code=409, detail="No se pudo registrar la asignacion. Reintente."
    )


def asignar_agencia(
    db: Session, usuario: Usuario, id_envio: int, payload: AsignarEnvioPayload
) -> Envio:
    """LISTO_ENVIO -> ASIGNADO con una agencia. Atomico: o se guarda todo o nada."""
    try:
        envio = obtener_envio(db, id_envio, bloquear=True)          # 404 + FOR UPDATE
        _exigir_permiso(usuario, envio, "ASIGNADO")                  # 403 (solo ASU/GS)
        _validar_transicion(envio, "ASIGNADO")                       # 409 (solo desde LISTO_ENVIO)
        if envio.id_repartidor is not None or envio.id_agencia is not None:
            raise HTTPException(
                status_code=409,
                detail="El envio ya tiene un repartidor o una agencia asignados.",
            )
        peso, volumen = validar_dimensiones(payload.peso_kg, payload.volumen_m3)   # 422
        fecha_estimada = None
        if payload.fecha_estimada_entrega is not None:
            fecha_estimada = _exigir_fecha_futura(
                payload.fecha_estimada_entrega, "La fecha estimada de entrega"
            )

        agencia = _buscar(db, payload.id_agencia, bloquear=True)     # 404 + FOR UPDATE
        # Cotizacion/seleccion de Fase 8 (agencia habilitada, ciudad, cobertura,
        # tarifa aplicable y regla de mayor costo -> PESO -> ciudad completa).
        cotizacion = cotizar(db, usuario, agencia.id_agencia, envio.venta.ciudad, peso, volumen)
    except HTTPException:
        db.rollback()  # nada se escribio: libera los bloqueos y deja el envio intacto
        raise

    envio.id_agencia = agencia.id_agencia
    envio.id_tarifa_aplicada = cotizacion["tarifa"]["id_tarifa"]
    envio.costo_agencia = cotizacion["costo_agencia"]
    envio.peso_kg = cotizacion["peso_kg"]
    envio.volumen_m3 = cotizacion["volumen_m3"]
    if fecha_estimada is not None:
        envio.fecha_estimada_entrega = fecha_estimada
    _cambiar_estado(
        envio,
        "ASIGNADO",
        usuario,
        _texto(f"Asignado a agencia {agencia.razon_social}", payload.observacion),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise traducir_integrity_error(exc) from exc
    db.refresh(envio)
    return envio
