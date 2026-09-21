# backend/app/modules/inventario/stock_alert.py
# CU10 + CU22 — Regla automática de alerta de stock crítico por sucursal.
#
# Cuando una venta, reserva o ajuste reduzca el stock de un producto en una
# sucursal a <= 5 unidades, se emite automáticamente una notificación in-app (CU10)
# dirigida al Gerente de Sucursal (GS) responsable de dicha tienda.
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import Producto
from app.modules.notificaciones.models import Notificacion
from app.modules.notificaciones.service import emitir
from app.modules.usuarios.models import Rol, Usuario

UMBRAL_STOCK_CRITICO = 5


def verificar_y_notificar_stock_critico(
    db: Session,
    *,
    id_producto: int,
    id_sucursal: int,
    stock_nuevo: int,
    umbral: int = UMBRAL_STOCK_CRITICO,
    commit: bool = False,
) -> list[Notificacion]:
    """Evalúa si el stock físico resultante en una sucursal es crítico (<= umbral)
    y, de ser así, genera la notificación (CU10) para el Gerente de Sucursal (GS).

    Args:
        db: Sesión activa de SQLAlchemy.
        id_producto: ID del producto cuyo stock disminuyó.
        id_sucursal: Código de la sucursal afectada.
        stock_nuevo: Nivel de stock resultante tras la operación.
        umbral: Límite numérico para considerar stock crítico (default 5).
        commit: Si True, realiza commit. Si False, se adhiere a la transacción atómica del caller.

    Returns:
        Lista de notificaciones emitidas (una por gerente/destinatario).
    """
    if stock_nuevo > umbral:
        return []

    producto = db.get(Producto, id_producto)
    sucursal = db.get(Sucursal, id_sucursal)

    if not producto or not sucursal:
        return []

    nombre_producto = producto.nombre
    nombre_sucursal = sucursal.nombre

    # Localizar al Gerente de Sucursal (destinatario del rol GS)
    destinatarios: list[UUID] = []

    if sucursal.id_gerente:
        destinatarios.append(sucursal.id_gerente)
    else:
        # Si no tiene id_gerente titular directo, buscar usuarios con rol GS asignados a esta sucursal
        gerentes = (
            db.query(Usuario)
            .join(Rol, Rol.id_rol == Usuario.id_rol)
            .filter(
                Rol.nombre_rol == "GS",
                Usuario.id_sucursal == id_sucursal,
                Usuario.estado.is_(True),
            )
            .all()
        )
        for g in gerentes:
            destinatarios.append(g.id_usuario)

    # Si la sucursal no tiene ningún GS asignado, alertar a los administradores (ASU) como red de seguridad
    if not destinatarios:
        admins = (
            db.query(Usuario)
            .join(Rol, Rol.id_rol == Usuario.id_rol)
            .filter(
                Rol.nombre_rol == "ASU",
                Usuario.estado.is_(True),
            )
            .all()
        )
        for a in admins:
            destinatarios.append(a.id_usuario)

    if not destinatarios:
        return []

    titulo = f"Alerta Stock Crítico: {nombre_producto[:60]}"
    mensaje = (
        f"El stock de '{nombre_producto}' en la sucursal '{nombre_sucursal}' "
        f"ha bajado a {stock_nuevo} unidades (límite crítico: {umbral}). "
        f"Favor gestionar el reabastecimiento urgente de esta prenda."
    )

    notificaciones: list[Notificacion] = []
    for uid in destinatarios:
        try:
            noti = emitir(
                db,
                id_usuario=uid,
                titulo=titulo,
                mensaje=mensaje,
                tipo="STOCK",
                referencia_tipo="INVENTARIO",
                referencia_id=str(id_producto),
                commit=commit,
            )
            notificaciones.append(noti)
        except Exception:
            # Nunca bloquear la transacción principal por un error de notificación
            pass

    return notificaciones
