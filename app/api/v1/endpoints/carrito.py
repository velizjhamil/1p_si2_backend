# backend/app/api/v1/endpoints/carrito.py
# CU15 — Carrito de Compras: Endpoints persistentes en base de datos.
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import InventarioSucursal, Producto
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import Carrito, ItemCarrito
from app.schemas.carrito import (
    CarritoResponse,
    ItemCarritoCreate,
    ItemCarritoResponse,
    ItemCarritoUpdate,
    SincronizarCarritoPayload,
)

router = APIRouter()

ENVIO_GRATIS_DESDE = 300
COSTO_ENVIO = 25


def _envelope(data: Any, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar {status, data, message}."""
    payload = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


def _obtener_o_crear_carrito(db: Session, id_usuario: UUID | str) -> Carrito:
    """Recupera el carrito del usuario o lo inicializa si es su primera vez."""
    carrito = db.query(Carrito).filter(Carrito.id_usuario == id_usuario).first()
    if not carrito:
        carrito = Carrito(id_usuario=id_usuario)
        db.add(carrito)
        db.commit()
        db.refresh(carrito)
    return carrito


def _serializar_carrito(
    db: Session, carrito: Carrito, id_sucursal_filtro: int | None = None
) -> dict:
    """Calcula disponibilidad en tiempo real, subtotales y costos de envío."""
    items_serializados = []
    advertencias = []
    todos_disponibles = True

    for item in carrito.items:
        prod = item.producto
        if not prod:
            continue

        precio = float(prod.precio_venta)
        subtotal_item = round(precio * item.cantidad, 2)
        stock_total = prod.stock_total

        # Sucursal efectiva para validar disponibilidad
        suc_id = item.id_sucursal_preferida or id_sucursal_filtro
        stock_sucursal = None
        sucursal_nombre = None

        if suc_id:
            sucursal = db.get(Sucursal, suc_id)
            if sucursal:
                sucursal_nombre = sucursal.nombre

            inv_suc = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == suc_id,
                    InventarioSucursal.id_producto == prod.id_producto,
                )
                .first()
            )
            stock_sucursal = inv_suc.stock if inv_suc else 0
            disp = (stock_sucursal >= item.cantidad) and (prod.estado == "Activo")
        else:
            disp = (stock_total >= item.cantidad) and (prod.estado == "Activo")

        if not disp:
            todos_disponibles = False
            if prod.estado != "Activo":
                advertencias.append(f"El producto '{prod.nombre}' ya no está disponible para compra.")
            elif suc_id:
                advertencias.append(
                    f"Stock insuficiente para '{prod.nombre}' en {sucursal_nombre or f'Sucursal {suc_id}'}: "
                    f"solicitados {item.cantidad}, disponibles {stock_sucursal}."
                )
            else:
                advertencias.append(
                    f"Stock total insuficiente para '{prod.nombre}': "
                    f"solicitados {item.cantidad}, disponibles {stock_total}."
                )

        items_serializados.append(
            ItemCarritoResponse(
                id_item=item.id_item,
                id_producto=prod.id_producto,
                nombre_producto=prod.nombre,
                imagen_url=prod.imagen_url,
                precio_unitario=precio,
                cantidad=item.cantidad,
                subtotal=subtotal_item,
                talla=item.talla,
                color=item.color,
                id_sucursal_preferida=item.id_sucursal_preferida,
                nombre_sucursal_preferida=sucursal_nombre,
                stock_disponible_sucursal=stock_sucursal,
                stock_total_disponible=stock_total,
                disponible=disp,
                fecha_agregado=item.fecha_agregado,
            ).model_dump()
        )

    subtotal_gral = round(sum(i["subtotal"] for i in items_serializados), 2)
    costo_envio = 0.0 if (subtotal_gral >= ENVIO_GRATIS_DESDE or len(items_serializados) == 0) else float(COSTO_ENVIO)
    total = round(subtotal_gral + costo_envio, 2)
    cant_total = sum(i["cantidad"] for i in items_serializados)

    return {
        "id_carrito": carrito.id_carrito,
        "items": items_serializados,
        "cantidad_total_items": cant_total,
        "subtotal": subtotal_gral,
        "costo_envio": costo_envio,
        "total": total,
        "todos_disponibles": todos_disponibles,
        "advertencias": advertencias,
    }


@router.get("", response_model=None)
@router.get("/", response_model=None)
def obtener_carrito(
    id_sucursal: int | None = Query(
        None, description="Filtrar o evaluar disponibilidad en una sucursal específica"
    ),
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede gestionar su carrito de compras.")
    ),
):
    """CU15: Obtiene el carrito persistente del cliente con validación de stock en tiempo real."""
    if not isinstance(id_sucursal, int):
        id_sucursal = None
    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)
    data = _serializar_carrito(db, carrito, id_sucursal)
    return _envelope(data, message="Carrito obtenido exitosamente.")


@router.post("/items", response_model=None, status_code=status.HTTP_201_CREATED)
def agregar_item_carrito(
    payload: ItemCarritoCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede agregar prendas a su carrito.")
    ),
):
    """CU15: Añade una prenda (con talla y color) al carrito persistente."""
    producto = db.get(Producto, payload.id_producto)
    if not producto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el producto con ID {payload.id_producto}.",
        )
    if producto.estado != "Activo":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El producto '{producto.nombre}' no se encuentra disponible ({producto.estado}).",
        )

    # Validar sucursal preferida si fue enviada
    if payload.id_sucursal_preferida:
        suc = db.get(Sucursal, payload.id_sucursal_preferida)
        if not suc or not suc.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"La sucursal con código {payload.id_sucursal_preferida} no existe o no está activa.",
            )
        # Validar stock físico en la sucursal elegida
        inv_suc = (
            db.query(InventarioSucursal)
            .filter(
                InventarioSucursal.id_sucursal == payload.id_sucursal_preferida,
                InventarioSucursal.id_producto == payload.id_producto,
            )
            .first()
        )
        stock_suc = inv_suc.stock if inv_suc else 0
        if stock_suc < payload.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock insuficiente en la sucursal '{suc.nombre}': "
                    f"disponibles {stock_suc}, solicitados {payload.cantidad}."
                ),
            )
    else:
        if producto.stock_total < payload.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Stock insuficiente para '{producto.nombre}': "
                    f"disponibles {producto.stock_total}, solicitados {payload.cantidad}."
                ),
            )

    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)

    # Buscar si ya existe la misma variante (producto + talla + color)
    item_existente = (
        db.query(ItemCarrito)
        .filter(
            ItemCarrito.id_carrito == carrito.id_carrito,
            ItemCarrito.id_producto == payload.id_producto,
            ItemCarrito.talla == payload.talla,
            ItemCarrito.color == payload.color,
        )
        .first()
    )

    if item_existente:
        nueva_cant = item_existente.cantidad + payload.cantidad
        if payload.id_sucursal_preferida:
            inv = (
                db.query(InventarioSucursal)
                .filter(
                    InventarioSucursal.id_sucursal == payload.id_sucursal_preferida,
                    InventarioSucursal.id_producto == payload.id_producto,
                )
                .first()
            )
            if inv and inv.stock < nueva_cant:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"No puede agregar más unidades: stock local máximo disponible {inv.stock}.",
                )
        elif producto.stock_total < nueva_cant:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No puede agregar más unidades: stock total máximo disponible {producto.stock_total}.",
            )

        item_existente.cantidad = nueva_cant
        if payload.id_sucursal_preferida:
            item_existente.id_sucursal_preferida = payload.id_sucursal_preferida
    else:
        nuevo_item = ItemCarrito(
            id_carrito=carrito.id_carrito,
            id_producto=payload.id_producto,
            cantidad=payload.cantidad,
            talla=payload.talla,
            color=payload.color,
            id_sucursal_preferida=payload.id_sucursal_preferida,
        )
        db.add(nuevo_item)

    db.commit()
    db.refresh(carrito)

    data = _serializar_carrito(db, carrito, payload.id_sucursal_preferida)
    return _envelope(data, message="Prenda agregada al carrito con éxito.")


@router.patch("/items/{id_item}", response_model=None)
def actualizar_item_carrito(
    id_item: int,
    payload: ItemCarritoUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede modificar su carrito.")
    ),
):
    """CU15: Actualiza la cantidad o sucursal de una prenda en el carrito."""
    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)
    item = (
        db.query(ItemCarrito)
        .filter(
            ItemCarrito.id_item == id_item,
            ItemCarrito.id_carrito == carrito.id_carrito,
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el ítem con ID {id_item} en su carrito.",
        )

    prod = item.producto
    suc_id = payload.id_sucursal_preferida or item.id_sucursal_preferida
    if suc_id:
        inv = (
            db.query(InventarioSucursal)
            .filter(
                InventarioSucursal.id_sucursal == suc_id,
                InventarioSucursal.id_producto == prod.id_producto,
            )
            .first()
        )
        stock_disp = inv.stock if inv else 0
        if stock_disp < payload.cantidad:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Stock insuficiente en la sucursal seleccionada: disponibles {stock_disp}.",
            )
    elif prod.stock_total < payload.cantidad:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stock insuficiente: disponibles {prod.stock_total}.",
        )

    item.cantidad = payload.cantidad
    if payload.id_sucursal_preferida is not None:
        item.id_sucursal_preferida = payload.id_sucursal_preferida

    db.commit()
    db.refresh(carrito)

    data = _serializar_carrito(db, carrito, suc_id)
    return _envelope(data, message="Cantidad actualizada correctamente.")


@router.delete("/items/{id_item}", response_model=None)
def eliminar_item_carrito(
    id_item: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede eliminar ítems de su carrito.")
    ),
):
    """CU15: Elimina una línea de prenda del carrito."""
    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)
    item = (
        db.query(ItemCarrito)
        .filter(
            ItemCarrito.id_item == id_item,
            ItemCarrito.id_carrito == carrito.id_carrito,
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el ítem con ID {id_item} en su carrito.",
        )

    db.delete(item)
    db.commit()
    db.refresh(carrito)

    data = _serializar_carrito(db, carrito)
    return _envelope(data, message="Ítem eliminado del carrito.")


@router.delete("", response_model=None)
@router.delete("/", response_model=None)
def vaciar_carrito(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede vaciar su carrito.")
    ),
):
    """CU15: Vacía todos los ítems del carrito del cliente."""
    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)
    for it in list(carrito.items):
        db.delete(it)

    db.commit()
    db.refresh(carrito)

    data = _serializar_carrito(db, carrito)
    return _envelope(data, message="Carrito vaciado exitosamente.")


@router.post("/sincronizar", response_model=None)
def sincronizar_carrito(
    payload: SincronizarCarritoPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(
        require_roles("C", detail="Solo el rol Cliente (C) puede sincronizar su carrito.")
    ),
):
    """CU15: Sincroniza ítems locales del frontend (localStorage) a la base de datos."""
    carrito = _obtener_o_crear_carrito(db, usuario_actual.id_usuario)

    if payload.reemplazar:
        for it in list(carrito.items):
            db.delete(it)
        db.flush()

    for item_in in payload.items:
        prod = db.get(Producto, item_in.id_producto)
        if not prod or prod.estado != "Activo":
            continue

        item_existente = (
            db.query(ItemCarrito)
            .filter(
                ItemCarrito.id_carrito == carrito.id_carrito,
                ItemCarrito.id_producto == item_in.id_producto,
                ItemCarrito.talla == item_in.talla,
                ItemCarrito.color == item_in.color,
            )
            .first()
        )

        if item_existente:
            item_existente.cantidad += item_in.cantidad
            if item_in.id_sucursal_preferida:
                item_existente.id_sucursal_preferida = item_in.id_sucursal_preferida
        else:
            nuevo = ItemCarrito(
                id_carrito=carrito.id_carrito,
                id_producto=item_in.id_producto,
                cantidad=item_in.cantidad,
                talla=item_in.talla,
                color=item_in.color,
                id_sucursal_preferida=item_in.id_sucursal_preferida,
            )
            db.add(nuevo)

    db.commit()
    db.refresh(carrito)

    data = _serializar_carrito(db, carrito)
    return _envelope(data, message="Carrito sincronizado con éxito.")
