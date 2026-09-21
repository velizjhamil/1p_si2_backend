# backend/app/modules/delivery/service.py
# CU18 - Gestion de Envio: logica de negocio del despacho.
#
# Capa entre el router (HTTP) y los modelos (Envio / EnvioHistorial). Aca
# viven: la maquina de estados, la matriz de permisos por rol, el historial,
# las notificaciones al cliente y las serializaciones. El router solo
# resuelve parametros HTTP y delega.
#
# Convenciones:
# - Errores de negocio como HTTPException (400 operacion invalida, 403
#   permisos, 404 no existe, 409 conflicto de estado), igual que el resto
#   del backend.
# - Cada operacion que muta el envio hace commit UNA sola vez al final: el
#   cambio de estado, su fila de historial y la notificacion al cliente
#   quedan en la misma transaccion (todo o nada).
# - `crear_envio_para_venta` NO hace commit: la invoca el checkout dentro de
#   su propia transaccion.
# - Concurrencia: las operaciones que mutan bloquean la fila con
#   SELECT ... FOR UPDATE OF envios (dos PATCH simultaneos no pisan el estado).
#
# Notificaciones al cliente (siempre via CU10 `notificaciones.service.emitir`,
# sin otro sistema): EN_RUTA, ENTREGADO, INTENTO_FALLIDO, REPROGRAMADO y
# CANCELADO; UNA por cambio de estado. Hoy son solo in-app: correo/SMS/push
# se conectan en `_notificar_cliente`.
#
# DECISIONES DE ALCANCE (CU18):
# - Sucursal: el envio conserva la sucursal responsable (codigo_sucursal) y
#   se puede filtrar por ella, pero NO se restringe automaticamente lo que ve
#   un Gerente de Sucursal. Mejora/requisito transversal futuro: "El sistema
#   actualmente no restringe automaticamente los registros visibles segun la
#   sucursal del Gerente de Sucursal porque Usuario no esta asociado a una
#   sucursal." (afecta ventas, envios, inventario, etc.; requiere CU3).
# - CANCELADO cierra SOLO el flujo logistico del envio. NO cancela la venta,
#   NO repone stock, NO genera devolucion NI reembolso: esas
#   responsabilidades siguen en el flujo de devoluciones (CU13) / inventario
#   (CU22) y se ejecutan por sus propios endpoints.
# - Fechas: todo se normaliza a UTC (`_a_utc`); un datetime sin zona horaria
#   se asume UTC. El frontend debe enviar ISO 8601 con offset.
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.modules.delivery.models import (
    ESTADOS_TERMINALES_ENVIO,
    ROLES_GESTION_ENVIO,
    TRANSICIONES_ENVIO,
    Envio,
    EnvioHistorial,
)
from app.modules.empresa.models import Sucursal
from app.modules.notificaciones.service import emitir
from app.modules.usuarios.models import Rol, Usuario
from app.modules.ventas.models import Venta
from app.schemas.envio import (
    AsignarEnvioPayload,
    CambiarEstadoPayload,
    ConfirmarPreparacionPayload,
    IntentoFallidoPayload,
    ReprogramarEnvioPayload,
)

# Rol repartidor (Delivery) y roles con control total del despacho.
ROL_REPARTIDOR = "D"
ROLES_ADMIN_ENVIO = ("ASU", "GS")

# Estados destino que solo pueden pedir ASU/GS. El resto de destinos
# (EN_RUTA, ENTREGADO, INTENTO_FALLIDO, REPROGRAMADO) tambien los puede
# ejecutar el repartidor (D) cuando el envio esta asignado a el.
_DESTINOS_SOLO_ADMIN = ("LISTO_ENVIO", "ASIGNADO", "CANCELADO")

# Estados en los que el repartidor tiene el paquete "en su cartera".
ESTADOS_ACTIVOS_REPARTIDOR = ("ASIGNADO", "EN_RUTA", "REPROGRAMADO")


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _rol_nombre(usuario: Usuario) -> str:
    return usuario.rol.nombre_rol if usuario.rol else ""


def _nombre(u: Usuario | None) -> str | None:
    if u is None:
        return None
    return f"{u.nombre} {u.apellido or ''}".strip()


def _a_utc(fecha: datetime) -> datetime:
    """Normaliza a UTC. Un datetime sin zona horaria se asume UTC (el
    frontend debe enviar ISO con offset, ej. toISOString())."""
    if fecha.tzinfo is None:
        return fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(timezone.utc)


def _exigir_fecha_futura(fecha: datetime, campo: str) -> datetime:
    fecha_utc = _a_utc(fecha)
    if fecha_utc <= _ahora():
        raise HTTPException(
            status_code=400, detail=f"{campo} debe ser una fecha y hora futuras."
        )
    return fecha_utc


def _es_repartidor_asignado(usuario: Usuario, envio: Envio) -> bool:
    return (
        _rol_nombre(usuario) == ROL_REPARTIDOR
        and envio.id_repartidor is not None
        and str(envio.id_repartidor) == str(usuario.id_usuario)
    )


def tiene_transportista(envio: Envio) -> bool:
    """¿Alguien lleva el paquete? Repartidor propio (CU18) O agencia de reparto
    (CU19). Son mutuamente excluyentes (CHECK `repartidor_o_agencia` en la DB y
    validacion al asignar), asi que un envio tiene uno u otro, nunca ambos.

    Es la condicion para despachar (EN_RUTA): la usan `cambiar_estado` (guarda)
    y `transiciones_permitidas` (lo que ofrece la UI), para que coincidan."""
    return envio.id_repartidor is not None or envio.id_agencia is not None


def puede_ejecutar(usuario: Usuario, envio: Envio, destino: str) -> bool:
    """Matriz de permisos: ¿puede `usuario` llevar `envio` a `destino`?

    - ASU/GS: todos los destinos (con repartidor o con agencia).
    - D: EN_RUTA / ENTREGADO / INTENTO_FALLIDO / REPROGRAMADO, y solo si
      el envio esta asignado a el (`id_repartidor`). Por eso D NUNCA opera un
      envio con agencia (CU19): esos los avanzan ASU/GS. No prepara, asigna
      ni cancela.
    - Cualquier otro rol: nada.
    """
    rol = _rol_nombre(usuario)
    if rol in ROLES_ADMIN_ENVIO:
        return True
    if rol == ROL_REPARTIDOR and destino not in _DESTINOS_SOLO_ADMIN:
        return _es_repartidor_asignado(usuario, envio)
    return False


def _exigir_permiso(usuario: Usuario, envio: Envio, destino: str) -> None:
    if puede_ejecutar(usuario, envio, destino):
        return
    if _rol_nombre(usuario) == ROL_REPARTIDOR and destino not in _DESTINOS_SOLO_ADMIN:
        raise HTTPException(
            status_code=403, detail="El envio no esta asignado a este repartidor."
        )
    raise HTTPException(
        status_code=403,
        detail="No tiene permisos para realizar esta accion sobre el envio.",
    )


def _validar_transicion(envio: Envio, destino: str) -> None:
    """409 si el destino no es alcanzable desde el estado actual."""
    if envio.estado in ESTADOS_TERMINALES_ENVIO:
        raise HTTPException(
            status_code=409,
            detail=f"El envio ya esta {envio.estado} y no admite mas cambios.",
        )
    permitidos = TRANSICIONES_ENVIO.get(envio.estado, ())
    if destino not in permitidos:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transicion invalida: {envio.estado} -> {destino}. "
                f"Desde {envio.estado} solo se puede pasar a: "
                f"{', '.join(permitidos) or 'ninguno'}."
            ),
        )


def _cambiar_estado(
    envio: Envio, destino: str, usuario: Usuario | None, observacion: str | None
) -> None:
    """Aplica la transicion y agrega la fila de historial (sin commit)."""
    _validar_transicion(envio, destino)
    envio.historial.append(
        EnvioHistorial(
            estado_anterior=envio.estado,
            estado_nuevo=destino,
            id_usuario=usuario.id_usuario if usuario else None,
            observacion=observacion,
        )
    )
    envio.estado = destino


def _notificar_cliente(
    db: Session, envio: Envio, titulo: str, mensaje: str, tipo: str = "PEDIDO"
) -> None:
    """Notificacion in-app al cliente de la venta (sin commit).

    Punto unico de salida hacia el cliente: si mas adelante se integra
    correo/SMS/push, se agrega aca junto al `emitir` in-app.
    """
    emitir(
        db,
        id_usuario=envio.venta.id_cliente,
        titulo=titulo,
        mensaje=mensaje,
        tipo=tipo,
        referencia_tipo="envio",
        referencia_id=str(envio.id_envio),
        commit=False,
    )


def _texto(*partes: str | None) -> str | None:
    """Une partes no vacias con ' - ' (None si no hay ninguna)."""
    texto = " - ".join(p for p in partes if p)
    return texto[:500] or None


def _cerrar(db: Session, envio: Envio) -> Envio:
    """Commit unico de la operacion y recarga del envio."""
    db.commit()
    db.refresh(envio)
    return envio


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def obtener_envio(db: Session, id_envio: int, *, bloquear: bool = False) -> Envio:
    """404 si no existe. `bloquear=True` toma FOR UPDATE sobre la fila."""
    query = db.query(Envio).filter(Envio.id_envio == id_envio)
    if bloquear:
        # of=Envio: las relaciones lazy="joined" generan OUTER JOIN y
        # PostgreSQL rechaza FOR UPDATE sin restringir la tabla.
        query = query.with_for_update(of=Envio)
    envio = query.first()
    if not envio:
        raise HTTPException(status_code=404, detail=f"No existe el envio con id {id_envio}.")
    return envio


def obtener_envio_por_venta(db: Session, id_venta: int) -> Envio:
    envio = db.query(Envio).filter(Envio.id_venta == id_venta).first()
    if not envio:
        raise HTTPException(
            status_code=404, detail=f"La venta {id_venta} no tiene un envio asociado."
        )
    return envio


def validar_acceso(envio: Envio, usuario: Usuario) -> None:
    """403 si el usuario no puede ver este envio.

    - ASU/GS: todos. D: solo los asignados a el. C: solo los de SUS ventas.
    """
    rol = _rol_nombre(usuario)
    if rol in ROLES_ADMIN_ENVIO:
        return
    if rol == ROL_REPARTIDOR and _es_repartidor_asignado(usuario, envio):
        return
    if rol == "C" and str(envio.venta.id_cliente) == str(usuario.id_usuario):
        return
    raise HTTPException(status_code=403, detail="No tiene acceso a este envio.")


def listar_envios(
    db: Session,
    usuario: Usuario,
    *,
    estado: str | None = None,
    q: str | None = None,
    codigo_sucursal: int | None = None,
    id_repartidor: UUID | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Envio], int]:
    """Listado paginado con visibilidad por rol.

    ASU/GS ven todo; D solo lo asignado a el; C solo lo de sus ventas;
    cualquier otro rol recibe 403.
    """
    rol = _rol_nombre(usuario)
    if rol not in (*ROLES_GESTION_ENVIO, "C"):
        raise HTTPException(status_code=403, detail="No tiene acceso a los envios.")

    if estado and estado not in TRANSICIONES_ENVIO:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Estado invalido '{estado}'. Valores permitidos: "
                f"{', '.join(TRANSICIONES_ENVIO)}."
            ),
        )

    query = db.query(Envio).join(Venta, Venta.id_venta == Envio.id_venta)

    if rol == ROL_REPARTIDOR:
        query = query.filter(Envio.id_repartidor == usuario.id_usuario)
    elif rol == "C":
        query = query.filter(Venta.id_cliente == usuario.id_usuario)

    if estado:
        query = query.filter(Envio.estado == estado)
    if rol == "GS" and usuario.id_sucursal:
        suc_filtro = codigo_sucursal or usuario.id_sucursal
        query = query.filter(Envio.codigo_sucursal == suc_filtro)
    elif codigo_sucursal:
        query = query.filter(Envio.codigo_sucursal == codigo_sucursal)
    if id_repartidor:
        query = query.filter(Envio.id_repartidor == id_repartidor)
    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(Venta.codigo.ilike(term), Venta.nombre_cliente.ilike(term))
        )

    total = query.count()
    items = (
        query.options(selectinload(Envio.agencia))  # CU19: sin N+1 al serializar la agencia
        .order_by(Envio.fecha_creacion.desc(), Envio.id_envio.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return items, total


def listar_repartidores(db: Session) -> list[dict]:
    """Usuarios activos con rol D + cantidad de envios que llevan activos."""
    repartidores = (
        db.query(Usuario)
        .join(Rol, Rol.id_rol == Usuario.id_rol)
        .filter(Rol.nombre_rol == ROL_REPARTIDOR, Usuario.estado.is_(True))
        .order_by(Usuario.nombre)
        .all()
    )
    activos = dict(
        db.query(Envio.id_repartidor, func.count(Envio.id_envio))
        .filter(Envio.estado.in_(ESTADOS_ACTIVOS_REPARTIDOR))
        .filter(Envio.id_repartidor.is_not(None))
        .group_by(Envio.id_repartidor)
        .all()
    )
    return [
        {
            "id_usuario": r.id_usuario,
            "nombre": _nombre(r),
            "correo": r.correo,
            "envios_activos": int(activos.get(r.id_usuario, 0)),
        }
        for r in repartidores
    ]


# ---------------------------------------------------------------------------
# Operaciones del flujo
# ---------------------------------------------------------------------------
def crear_envio_para_venta(
    db: Session,
    venta: Venta,
    usuario: Usuario | None,
    observacion: str | None = None,
) -> Envio:
    """Crea el envio (PREPARANDO) de una venta a domicilio. SIN commit.

    Lo llama el checkout dentro de su transaccion (la venta ya hizo flush)
    y el endpoint manual para ventas anteriores a CU18.
    Precondicion de CU18: compra completada (PAGADO) con entrega a DOMICILIO.
    """
    if venta.tipo_entrega != "DOMICILIO":
        raise HTTPException(
            status_code=400,
            detail=f"La venta {venta.codigo} es de {venta.tipo_entrega}: no requiere envio.",
        )
    if venta.estado_pago != "PAGADO":
        raise HTTPException(
            status_code=409,
            detail=(
                f"La venta {venta.codigo} no esta PAGADO "
                f"(estado: {venta.estado_pago}): no se puede iniciar el envio."
            ),
        )
    if db.query(Envio.id_envio).filter(Envio.id_venta == venta.id_venta).first():
        raise HTTPException(
            status_code=409, detail=f"La venta {venta.codigo} ya tiene un envio."
        )

    envio = Envio(
        id_venta=venta.id_venta,
        codigo_sucursal=venta.id_sucursal,
        estado="PREPARANDO",
        intentos_fallidos=0,
    )
    envio.historial.append(
        EnvioHistorial(
            estado_anterior=None,
            estado_nuevo="PREPARANDO",
            id_usuario=usuario.id_usuario if usuario else None,
            observacion=observacion or "Envio creado tras la compra.",
        )
    )
    db.add(envio)
    db.flush()
    return envio


def iniciar_envio_manual(
    db: Session, usuario: Usuario, id_venta: int, observacion: str | None
) -> Envio:
    """POST /envios: crea el envio de una venta a domicilio sin envio (ASU/GS)."""
    if _rol_nombre(usuario) not in ROLES_ADMIN_ENVIO:
        raise HTTPException(
            status_code=403, detail="Solo Gerente de Sucursal o Administrador inician envios."
        )
    venta = db.get(Venta, id_venta)
    if not venta:
        raise HTTPException(status_code=404, detail=f"No existe la venta con id {id_venta}.")
    envio = crear_envio_para_venta(db, venta, usuario, observacion)
    return _cerrar(db, envio)


def confirmar_preparacion(
    db: Session, usuario: Usuario, id_envio: int, payload: ConfirmarPreparacionPayload
) -> Envio:
    """PREPARANDO -> LISTO_ENVIO fijando la sucursal responsable."""
    envio = obtener_envio(db, id_envio, bloquear=True)
    _exigir_permiso(usuario, envio, "LISTO_ENVIO")

    sucursal = db.get(Sucursal, payload.codigo_sucursal)
    if not sucursal:
        raise HTTPException(
            status_code=400,
            detail=f"No existe la sucursal con codigo {payload.codigo_sucursal}.",
        )
    if not sucursal.is_active:
        raise HTTPException(
            status_code=400, detail=f"La sucursal '{sucursal.nombre}' esta inactiva."
        )

    _cambiar_estado(
        envio,
        "LISTO_ENVIO",
        usuario,
        _texto(f"Paquete preparado en {sucursal.nombre}", payload.observacion),
    )
    envio.codigo_sucursal = sucursal.codigo_sucursal
    return _cerrar(db, envio)


def asignar_repartidor(
    db: Session, usuario: Usuario, id_envio: int, payload: AsignarEnvioPayload
) -> Envio:
    """LISTO_ENVIO -> ASIGNADO con un repartidor (rol D activo)."""
    envio = obtener_envio(db, id_envio, bloquear=True)
    _exigir_permiso(usuario, envio, "ASIGNADO")
    if envio.id_agencia is not None:  # CU19: agencia y repartidor son excluyentes
        raise HTTPException(
            status_code=409, detail="El envio ya tiene una agencia asignada."
        )

    repartidor = db.get(Usuario, payload.id_repartidor)
    if not repartidor:
        raise HTTPException(
            status_code=400, detail=f"No existe el usuario con id {payload.id_repartidor}."
        )
    if _rol_nombre(repartidor) != ROL_REPARTIDOR:
        raise HTTPException(
            status_code=400,
            detail=f"El usuario {repartidor.correo} no es repartidor (rol D).",
        )
    if not repartidor.estado:
        raise HTTPException(
            status_code=400, detail=f"El repartidor {repartidor.correo} esta inactivo."
        )

    fecha_estimada = None
    if payload.fecha_estimada_entrega is not None:
        fecha_estimada = _exigir_fecha_futura(
            payload.fecha_estimada_entrega, "La fecha estimada de entrega"
        )

    _cambiar_estado(
        envio,
        "ASIGNADO",
        usuario,
        _texto(f"Asignado a {_nombre(repartidor)}", payload.observacion),
    )
    envio.id_repartidor = repartidor.id_usuario
    if fecha_estimada is not None:
        envio.fecha_estimada_entrega = fecha_estimada
    return _cerrar(db, envio)


def cambiar_estado(
    db: Session, usuario: Usuario, id_envio: int, payload: CambiarEstadoPayload
) -> Envio:
    """EN_RUTA | ENTREGADO | CANCELADO, validado contra el estado actual.

    - EN_RUTA (desde ASIGNADO o REPROGRAMADO): exige repartidor o agencia
      (CU19) y notifica al cliente "Tu pedido esta en camino.".
    - ENTREGADO (desde EN_RUTA): registra fecha_entrega_real, cierra el
      flujo y notifica al cliente.
    - CANCELADO: cierra el flujo (motivo obligatorio, ya validado en el schema).
    """
    envio = obtener_envio(db, id_envio, bloquear=True)
    destino = payload.estado
    _exigir_permiso(usuario, envio, destino)
    _validar_transicion(envio, destino)  # 409 antes de otras validaciones

    codigo = envio.venta.codigo
    if destino == "EN_RUTA":
        if not tiene_transportista(envio):  # repartidor propio O agencia (CU19)
            raise HTTPException(
                status_code=400,
                detail="No se puede despachar un envio sin repartidor ni agencia asignados.",
            )
        _cambiar_estado(envio, destino, usuario, payload.observacion)
        _notificar_cliente(
            db, envio, "Pedido en camino", f"Tu pedido {codigo} esta en camino."
        )
    elif destino == "ENTREGADO":
        _cambiar_estado(envio, destino, usuario, payload.observacion)
        envio.fecha_entrega_real = _ahora()
        _notificar_cliente(
            db, envio, "Pedido entregado", f"Tu pedido {codigo} ha sido entregado."
        )
    else:  # CANCELADO
        _cambiar_estado(envio, destino, usuario, payload.observacion)
        _notificar_cliente(
            db,
            envio,
            "Envio cancelado",
            f"El envio de tu pedido {codigo} fue cancelado.",
            tipo="WARNING",
        )
    return _cerrar(db, envio)


def registrar_intento_fallido(
    db: Session, usuario: Usuario, id_envio: int, payload: IntentoFallidoPayload
) -> Envio:
    """EN_RUTA -> INTENTO_FALLIDO. Conserva el motivo en el historial.

    No es terminal: el siguiente paso es reprogramar (o cancelar).
    """
    envio = obtener_envio(db, id_envio, bloquear=True)
    _exigir_permiso(usuario, envio, "INTENTO_FALLIDO")

    _cambiar_estado(
        envio,
        "INTENTO_FALLIDO",
        usuario,
        _texto(payload.motivo, payload.observacion),
    )
    envio.motivo_fallo = payload.motivo
    envio.intentos_fallidos += 1
    # Aviso inmediato al cliente (CU10). El motivo interno NO se expone.
    _notificar_cliente(
        db,
        envio,
        "Intento de entrega fallido",
        f"No pudimos completar la entrega de tu pedido {envio.venta.codigo}. "
        "Coordinaremos una nueva fecha contigo.",
        tipo="WARNING",
    )
    return _cerrar(db, envio)


def reprogramar_entrega(
    db: Session, usuario: Usuario, id_envio: int, payload: ReprogramarEnvioPayload
) -> Envio:
    """INTENTO_FALLIDO -> REPROGRAMADO con nueva fecha estimada.

    Solo es posible tras un intento fallido registrado (lo garantiza la
    maquina de estados). El intento anterior queda intacto en el historial
    y en `motivo_fallo`. Luego el envio vuelve a EN_RUTA por /estado.
    """
    envio = obtener_envio(db, id_envio, bloquear=True)
    _exigir_permiso(usuario, envio, "REPROGRAMADO")
    _validar_transicion(envio, "REPROGRAMADO")

    nueva_fecha = _exigir_fecha_futura(
        payload.nueva_fecha_entrega, "La nueva fecha de entrega"
    )
    fecha_txt = nueva_fecha.strftime("%d/%m/%Y %H:%M UTC")
    _cambiar_estado(
        envio,
        "REPROGRAMADO",
        usuario,
        _texto(f"Nueva fecha estimada: {fecha_txt}", payload.observacion),
    )
    envio.fecha_estimada_entrega = nueva_fecha
    envio.fecha_reprogramacion = _ahora()
    _notificar_cliente(
        db,
        envio,
        "Entrega reprogramada",
        f"La entrega de tu pedido {envio.venta.codigo} fue reprogramada para {fecha_txt}.",
        tipo="INFO",
    )
    return _cerrar(db, envio)


# ---------------------------------------------------------------------------
# Serializacion (dicts listos para el envelope; misma forma que EnvioResponse)
# ---------------------------------------------------------------------------
def transiciones_permitidas(envio: Envio, usuario: Usuario) -> list[str]:
    """Destinos alcanzables desde el estado actual Y permitidos al usuario.

    Es lo que el frontend usa para pintar botones: nunca ofrece una
    accion que el backend vaya a rechazar por transicion o por rol.
    """
    return [
        destino
        for destino in TRANSICIONES_ENVIO.get(envio.estado, ())
        if puede_ejecutar(usuario, envio, destino)
        # Misma condicion que la guarda de cambiar_estado: no se ofrece despachar
        # un envio sin repartidor ni agencia.
        and (destino != "EN_RUTA" or tiene_transportista(envio))
    ]


def serializar_envio(envio: Envio, usuario: Usuario) -> dict:
    venta = envio.venta
    datos = {
        "id_envio": envio.id_envio,
        "id_venta": envio.id_venta,
        "codigo_venta": venta.codigo,
        "estado": envio.estado,
        "transiciones_permitidas": transiciones_permitidas(envio, usuario),
        "cliente_id": venta.id_cliente,
        "cliente_nombre": venta.nombre_cliente,
        "total_venta": venta.total,
        "datos_entrega": {
            "nombre_cliente": venta.nombre_cliente,
            "correo": venta.correo,
            "telefono": venta.telefono,
            "direccion": venta.direccion,
            "ciudad": venta.ciudad,
            "referencia": venta.referencia,
        },
        "items": [
            {
                "producto_id": d.id_producto,
                "nombre": d.producto.nombre if d.producto else f"Producto {d.id_producto}",
                "talla": d.talla,
                "color": d.color,
                "cantidad": d.cantidad,
            }
            for d in venta.detalles
        ],
        "codigo_sucursal": envio.codigo_sucursal,
        "sucursal_nombre": envio.sucursal.nombre if envio.sucursal else None,
        "repartidor_id": envio.id_repartidor,
        "repartidor_nombre": _nombre(envio.repartidor),
        # CU19: agencia de reparto (alternativa al repartidor). Sin agencia no
        # hay consulta extra: SQLAlchemy no consulta una FK nula.
        "agencia_id": envio.id_agencia,
        "agencia_nombre": envio.agencia.razon_social if envio.id_agencia is not None else None,
        "fecha_estimada_entrega": envio.fecha_estimada_entrega,
        "fecha_entrega_real": envio.fecha_entrega_real,
        "motivo_fallo": envio.motivo_fallo,
        "fecha_reprogramacion": envio.fecha_reprogramacion,
        "intentos_fallidos": envio.intentos_fallidos,
        "fecha_creacion": envio.fecha_creacion,
        "fecha_actualizacion": envio.fecha_actualizacion,
    }
    # CU19: el costo de agencia es un costo INTERNO de logistica y el snapshot
    # de la asignacion es de gestion: solo ASU/GS. El cliente (C) y D no lo ven.
    if _rol_nombre(usuario) in ROLES_ADMIN_ENVIO:
        datos.update(
            {
                "costo_agencia": envio.costo_agencia,
                "id_tarifa_aplicada": envio.id_tarifa_aplicada,
                "peso_kg": envio.peso_kg,
                "volumen_m3": envio.volumen_m3,
            }
        )
    return datos


def serializar_historial(envio: Envio) -> list[dict]:
    """Bitacora en orden cronologico (el orden lo fija la relacion)."""
    return [
        {
            "id_historial": h.id_historial,
            "id_envio": h.id_envio,
            "estado_anterior": h.estado_anterior,
            "estado_nuevo": h.estado_nuevo,
            "id_usuario": h.id_usuario,
            "usuario_nombre": _nombre(h.usuario),
            "observacion": h.observacion,
            "fecha": h.fecha,
        }
        for h in envio.historial
    ]
