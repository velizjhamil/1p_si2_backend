# backend/app/modules/notificaciones/service.py
# CU10 - Servicio interno para que el sistema automatico emita notificaciones
# sin pasar por el endpoint HTTP (bypasea RBAC y los throttling de FastAPI).
#
# Uso desde otros modulos:
#   from app.modules.notificaciones.service import emitir
#   emitir(db, id_usuario=..., titulo=..., mensaje=..., tipo="PEDIDO",
#          referencia_tipo="venta", referencia_id="42")
#
# Esto evita acoplar `usuarios` a `notificaciones.models` directamente
# desde cada CU: hay un solo punto de entrada tipado y testeable.
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.notificaciones.models import Notificacion, TIPOS_NOTIFICACION


def emitir(
    db: Session,
    *,
    id_usuario: UUID | str,
    titulo: str,
    mensaje: str,
    tipo: str = "INFO",
    referencia_tipo: Optional[str] = None,
    referencia_id: Optional[str] = None,
    commit: bool = True,
) -> Notificacion:
    """Inserta una notificacion en la bandeja del usuario destinatario.

    Args:
        db: sesion de SQLAlchemy.
        id_usuario: UUID del destinatario (FK a usuarios.id_usuario).
        titulo: 1..100 chars.
        mensaje: 1..500 chars.
        tipo: uno de TIPOS_NOTIFICACION (default 'INFO').
        referencia_tipo: tipo de recurso para deep-link (ej: 'venta').
        referencia_id: id del recurso para deep-link (ej: '42').
        commit: si True (default), hace commit al final. Si False, el
            caller controla la transaccion (util para emitir dentro de
            otra operacion atomica, p.ej. al registrar una devolucion).

    Returns:
        La fila Notificacion persistida (con id_notificacion asignado).

    Raises:
        ValueError: si `tipo` no es valido. La validacion vive en Python
            para fallar rapido y con un mensaje claro; el CHECK en la DB
            es la red de seguridad final.
    """
    if tipo not in TIPOS_NOTIFICACION:
        raise ValueError(
            f"Tipo de notificacion invalido: {tipo!r}. "
            f"Valores permitidos: {', '.join(TIPOS_NOTIFICACION)}."
        )
    if not 1 <= len(titulo) <= 100:
        raise ValueError("titulo debe tener entre 1 y 100 caracteres.")
    if not 1 <= len(mensaje) <= 500:
        raise ValueError("mensaje debe tener entre 1 y 500 caracteres.")

    noti = Notificacion(
        id_usuario=id_usuario,
        titulo=titulo.strip(),
        mensaje=mensaje.strip(),
        tipo=tipo,
        referencia_tipo=referencia_tipo,
        referencia_id=str(referencia_id) if referencia_id is not None else None,
    )
    db.add(noti)
    if commit:
        db.commit()
        db.refresh(noti)
    else:
        # El caller hara flush/commit; con flush el id_notificacion queda
        # asignado para que el caller pueda referenciarlo si quiere.
        db.flush()
    return noti


def emitir_a_rol(
    db: Session,
    *,
    nombre_rol: str,
    titulo: str,
    mensaje: str,
    tipo: str = "INFO",
    referencia_tipo: Optional[str] = None,
    referencia_id: Optional[str] = None,
) -> int:
    """Fan-out: emite la misma notificacion a TODOS los usuarios activos
    de un rol (ej: avisar a todos los Vendedores que hay stock bajo).

    Importacion diferida de Usuario/Rol para evitar ciclo de imports
    (notificaciones -> usuarios -> ...). Devuelve la cantidad de
    notificaciones creadas.
    """
    from app.modules.usuarios.models import Rol, Usuario  # noqa: PLC0415

    rol = db.query(Rol).filter(Rol.nombre_rol == nombre_rol).first()
    if not rol:
        return 0
    usuarios = (
        db.query(Usuario)
        .filter(Usuario.id_rol == rol.id_rol, Usuario.estado.is_(True))
        .all()
    )
    count = 0
    for u in usuarios:
        emitir(
            db,
            id_usuario=u.id_usuario,
            titulo=titulo,
            mensaje=mensaje,
            tipo=tipo,
            referencia_tipo=referencia_tipo,
            referencia_id=referencia_id,
            commit=False,
        )
        count += 1
    db.commit()
    return count
