# backend/app/api/v1/endpoints/probador.py
# CU8 — Probador Virtual AR: subir foto del cliente, probar prenda
# (superposición + recomendación de talla/ajuste) e historial lookbook.
#
# MOTOR DE RECOMENDACIÓN (mock de IA, misma semántica del frontend):
#   1. complexión derivada del IMC de referencia (< 20 DELGADA,
#      <= 27 MEDIA, > 27 ROBUSTA; sin medidas NO_INDICADA)
#   2. talla objetivo por complexión (DELGADA→S, MEDIA→M, ROBUSTA→L)
#      contra las tallas reales del producto (N:M producto_tallas)
#   3. ajuste: PERFECTO si elegida==recomendada; AJUSTADO si es menor;
#      HOLGADO si es mayor
# Las tallas del producto se resuelven desde el CATÁLOGO REAL (no del
# payload) — el backend es la autoridad de qué variantes existen.
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.modules.inventario.models import Producto, Talla
from app.modules.probador.models import FotoUsuario, SimulacionProbador
from app.modules.usuarios.models import Usuario
from app.schemas.probador import (
    AJUSTES_ESTIMADOS,
    COMPLEXIONES,
    ORDEN_TALLAS,
    FotoUsuarioPayload,
    SimulacionDirectaPayload,
    SimulacionGuardarPayload,
    SimulacionPayload,
)
from app.services.probador_ia_service import ProbadorIAService

router = APIRouter()


def _envelope(data, message: str = "Operación exitosa", **extra) -> dict:
    """Envelope estándar: {status, data, message} + extras."""
    payload: dict = {"status": "success", "data": data, "message": message}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Motor de recomendación (misma semántica del mock del frontend)
# ---------------------------------------------------------------------------
def _derivar_complexion(estatura: int | None, peso: int | None) -> str:
    """IMC de referencia: <20 DELGADA, <=27 MEDIA, >27 ROBUSTA."""
    if not estatura or not peso:
        return "NO_INDICADA"
    imc = peso / (estatura / 100) ** 2
    if imc < 20:
        return "DELGADA"
    if imc <= 27:
        return "MEDIA"
    return "ROBUSTA"


def _indice_talla(talla: str) -> int:
    """Índice relativo: XS=0, S=1, M=2, L=3, XL=4, XXL=5."""
    try:
        return ORDEN_TALLAS.index(talla.upper())
    except ValueError:
        return 2  # talla no estándar: trata como M


def _recomendar_talla(complexion: str, tallas_disponibles: list[str]) -> str:
    """Talla objetivo por complexión contra las tallas del producto."""
    if not tallas_disponibles:
        return "M"
    ordenadas = sorted(tallas_disponibles, key=_indice_talla)
    objetivo = {"DELGADA": 0, "MEDIA": 1, "ROBUSTA": 2}.get(complexion)
    if objetivo is None:  # NO_INDICADA
        objetivo = min(1, len(ordenadas) - 1)
    return ordenadas[min(objetivo, len(ordenadas) - 1)]


def _estimar_ajuste(elegida: str, recomendada: str) -> str:
    """PERFECTO si coinciden; AJUSTADO si menor; HOLGADO si mayor."""
    if elegida.upper() == recomendada.upper():
        return "PERFECTO"
    if _indice_talla(elegida) < _indice_talla(recomendada):
        return "AJUSTADO"
    return "HOLGADO"


# ---------------------------------------------------------------------------
# Helpers de dominio
# ---------------------------------------------------------------------------
def _buscar_foto_propia(db: Session, id_foto: int, usuario: Usuario) -> FotoUsuario:
    """Foto del usuario autenticado (404 si ajena o inexistente)."""
    foto = db.get(FotoUsuario, id_foto)
    if not foto or str(foto.id_usuario) != str(usuario.id_usuario):
        raise HTTPException(
            status_code=404,
            detail=f"No existe la foto con id {id_foto} para este usuario.",
        )
    return foto


def _serializar_simulacion(s: SimulacionProbador) -> dict:
    """Simulación con producto embebido (contrato del frontend)."""
    return {
        "id_simulacion": s.id_simulacion,
        "producto_id": s.id_producto,
        "producto_nombre": s.producto.nombre if s.producto else f"Producto {s.id_producto}",
        "prenda_imagen_url": s.producto.imagen_url if s.producto else None,
        "resultado_imagen_url": s.url_resultado,
        "talla_seleccionada": s.talla_elegida,
        "talla_recomendada": s.talla_recomendada,
        "ajuste_estimado": s.ajuste_estimado,
        "color_seleccionado": s.color_nombre,
        "color_hex": s.color_hex,
        "precio": float(s.producto.precio_venta) if s.producto else None,
        "categoria": (
            s.producto.categoria.nombre if s.producto and s.producto.categoria else None
        ),
        "fecha_simulacion": s.fecha_simulacion,
        "guardada": s.fecha_simulacion is not None,  # persistida en DB = guardada
    }


# ---------------------------------------------------------------------------
# POST /subir-foto — foto del cliente + medidas opcionales
# ---------------------------------------------------------------------------
@router.post(
    "/subir-foto", response_model=None, status_code=status.HTTP_201_CREATED
)
@router.post(
    "/subir-foto/", response_model=None, status_code=status.HTTP_201_CREATED
)
def subir_foto(
    payload: FotoUsuarioPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU8: Guarda la foto del usuario y deriva su complexión.

    - Usuario: del token de la sesión.
    - Complexión: server-side por IMC de referencia si hay medidas.
    - La imagen viaja como Data URL (base64) y se persiste completa.
    """
    foto = FotoUsuario(
        id_usuario=usuario_actual.id_usuario,
        url_imagen=payload.imagen_data_url,
        estatura_cm=payload.estatura,
        peso_kg=payload.peso,
        complexion=_derivar_complexion(payload.estatura, payload.peso),
    )
    db.add(foto)
    db.commit()
    db.refresh(foto)

    return _envelope(
        {
            "id_foto": foto.id_foto,
            "url_imagen": foto.url_imagen,
            "fecha_subida": foto.fecha_subida,
            "estatura_cm": foto.estatura_cm,
            "peso_kg": foto.peso_kg,
            "complexion": foto.complexion,
        },
        message="Foto procesada correctamente. Ya puede probar prendas.",
    )


# ---------------------------------------------------------------------------
# GET /fotos — fotos del usuario (para retomar sesión)
# ---------------------------------------------------------------------------
@router.get("/fotos", response_model=None)
def fotos_usuario(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
    limit: int = Query(default=10, ge=1, le=50),
):
    """CU8: Fotos subidas por el usuario (recientes primero)."""
    items = (
        db.query(FotoUsuario)
        .filter(FotoUsuario.id_usuario == usuario_actual.id_usuario)
        .order_by(FotoUsuario.fecha_subida.desc())
        .limit(limit)
        .all()
    )

    return _envelope(
        [
            {
                "id_foto": f.id_foto,
                "url_imagen": f.url_imagen,
                "fecha_subida": f.fecha_subida,
                "estatura_cm": f.estatura_cm,
                "peso_kg": f.peso_kg,
                "complexion": f.complexion,
            }
            for f in items
        ],
        total=len(items),
    )


# ---------------------------------------------------------------------------
# POST /probar — simulación AR de la prenda sobre la foto
# ---------------------------------------------------------------------------
@router.post("/probar", response_model=None, status_code=status.HTTP_201_CREATED)
@router.post("/probar/", response_model=None, status_code=status.HTTP_201_CREATED)
def probar_prenda(
    payload: SimulacionPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU8: Procesa la prenda seleccionada sobre la foto del cliente.

    - Foto: debe pertenecer al usuario del token (404 si ajena).
    - Producto: del catálogo REAL; sus tallas se resuelven desde las
      tablas pivote N:M (la talla elegida se valida contra ellas, 422).
    - Recomendación: complexión de la foto → talla objetivo → ajuste.
    - url_resultado: la foto del cliente (el overlay AR lo dibuja el
      visualizador del frontend).
    """
    foto = _buscar_foto_propia(db, payload.foto_id, usuario_actual)

    producto = db.get(Producto, payload.producto_id)
    if not producto:
        raise HTTPException(
            status_code=422,
            detail=f"No existe el producto con id {payload.producto_id}.",
        )
    if producto.estado != "Activo":
        raise HTTPException(
            status_code=409,
            detail=f"El producto '{producto.nombre}' no está disponible ({producto.estado}).",
        )

    # Tallas reales del producto (autoridad del backend, no del payload)
    tallas_producto = [t.nombre_talla for t in producto.tallas]
    if payload.talla_seleccionada.upper() not in [t.upper() for t in tallas_producto]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"La talla '{payload.talla_seleccionada}' no está disponible para "
                f"'{producto.nombre}'. Tallas: {', '.join(tallas_producto) or 'sin tallas'}."
            ),
        )

    # Motor de simulación AR con Google Gemini + Composición fotográfica en Pillow
    resultado_ia = ProbadorIAService.procesar_simulacion_ar(
        foto_usuario_data_url=foto.url_imagen,
        producto_nombre=producto.nombre,
        producto_categoria=producto.categoria.nombre if producto.categoria else "Prenda",
        talla_elegida=payload.talla_seleccionada,
        tallas_disponibles=tallas_producto,
        color_nombre=payload.color_nombre,
        color_hex=payload.color_hex,
        prenda_imagen_url=producto.imagen_url,  # Blindaje Anti-SSRF: Solo imagen oficial de DB
        complexion=foto.complexion,
        estatura_cm=foto.estatura_cm,
        peso_kg=foto.peso_kg,
    )

    talla_recomendada = resultado_ia.get("talla_recomendada") or _recomendar_talla(
        foto.complexion, tallas_producto
    )
    ajuste = resultado_ia.get("ajuste_estimado") or _estimar_ajuste(
        payload.talla_seleccionada, talla_recomendada
    )
    url_resultado = resultado_ia.get("resultado_imagen_url") or foto.url_imagen

    simulacion = SimulacionProbador(
        id_usuario=usuario_actual.id_usuario,
        id_producto=producto.id_producto,
        id_foto=foto.id_foto,
        url_resultado=url_resultado,
        talla_elegida=payload.talla_seleccionada.upper(),
        talla_recomendada=talla_recomendada,
        ajuste_estimado=ajuste,
        color_nombre=payload.color_nombre,
        color_hex=payload.color_hex,
    )
    db.add(simulacion)
    db.commit()
    db.refresh(simulacion)

    data_simulacion = _serializar_simulacion(simulacion)
    if "comentario_estilo" in resultado_ia:
        data_simulacion["comentario_estilo"] = resultado_ia["comentario_estilo"]
    data_simulacion["gemini_activo"] = resultado_ia.get("gemini_activo", False)
    if "motor" in resultado_ia:
        data_simulacion["motor"] = resultado_ia["motor"]

    return _envelope(
        data_simulacion,
        message="Simulación procesada exitosamente con IA.",
    )


# ---------------------------------------------------------------------------
# POST /probar-directo — simulación AR directa sin persistencia biométrica
# ---------------------------------------------------------------------------
@router.post("/probar-directo", response_model=None, status_code=status.HTTP_200_OK)
@router.post("/probar-directo/", response_model=None, status_code=status.HTTP_200_OK)
def probar_prenda_directo(
    payload: SimulacionDirectaPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """Prueba virtual fotorrealista con IA y reglas de seguridad estrictas:

    1. Blindaje Anti-SSRF: Solo se acepta producto_id interno. La imagen oficial
       de la prenda se extrae exclusivamente del catálogo registrado en la BD.
    2. Privacidad Absoluta y Cero Retención Biométrica: La fotografía del usuario
       se procesa en memoria volátil sin persistirse en la base de datos ni en disco.
    3. Autenticación y Control contra Abuso: Protegido por JWT.
    4. Enmascaramiento de Errores: Trazas técnicas en logs internos protegidos,
       entregando al cliente mensajes seguros y amigables.
    """
    producto = db.get(Producto, payload.producto_id)
    if not producto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe la prenda con id {payload.producto_id} en el catálogo.",
        )
    if producto.estado != "Activo":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"La prenda '{producto.nombre}' no está disponible actualmente ({producto.estado}).",
        )

    # Tallas reales del catálogo (autoridad del backend)
    tallas_producto = [t.nombre_talla for t in producto.tallas]
    talla_usada = payload.talla_seleccionada.upper()
    if tallas_producto and talla_usada not in [t.upper() for t in tallas_producto]:
        talla_usada = tallas_producto[0]

    # Derivación de complexión
    complexion = payload.complexion
    if payload.estatura_cm and payload.peso_kg:
        complexion = _derivar_complexion(payload.estatura_cm, payload.peso_kg)

    # Inferencia con estrategia dual y failover automático
    import logging
    _log = logging.getLogger(__name__)

    try:
        resultado_ia = ProbadorIAService.procesar_simulacion_ar(
            foto_usuario_data_url=payload.imagen_usuario,
            producto_nombre=producto.nombre,
            producto_categoria=producto.categoria.nombre if producto.categoria else "Prenda",
            talla_elegida=talla_usada,
            tallas_disponibles=tallas_producto or ["M"],
            color_nombre=payload.color_nombre,
            color_hex=payload.color_hex,
            prenda_imagen_url=producto.imagen_url,  # Blindaje Anti-SSRF: Imagen oficial de DB
            complexion=complexion,
            estatura_cm=payload.estatura_cm,
            peso_kg=payload.peso_kg,
        )
    except Exception as exc:
        # Enmascaramiento de errores técnicos
        _log.exception(f"Error procesando vestidor virtual para producto {payload.producto_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de vestidor virtual está experimentando una alta demanda. Por favor, intenta de nuevo en unos momentos.",
        )

    talla_recomendada = resultado_ia.get("talla_recomendada") or _recomendar_talla(
        complexion, tallas_producto
    )
    ajuste = resultado_ia.get("ajuste_estimado") or _estimar_ajuste(
        talla_usada, talla_recomendada
    )
    url_resultado = resultado_ia.get("resultado_imagen_url") or payload.imagen_usuario

    return _envelope(
        {
            "producto_id": producto.id_producto,
            "producto_nombre": producto.nombre,
            "prenda_imagen_url": producto.imagen_url,
            "resultado_imagen_url": url_resultado,
            "talla_seleccionada": talla_usada,
            "talla_recomendada": talla_recomendada,
            "ajuste_estimado": ajuste,
            "color_seleccionado": payload.color_nombre,
            "color_hex": payload.color_hex,
            "precio": float(producto.precio_venta),
            "categoria": producto.categoria.nombre if producto.categoria else None,
            "comentario_estilo": resultado_ia.get("comentario_estilo"),
            "gemini_activo": resultado_ia.get("gemini_activo", False),
            "motor": resultado_ia.get("motor"),
        },
        message="Simulación procesada exitosamente con IA.",
    )


# ---------------------------------------------------------------------------
# POST /lookbook — guardar una simulación en "Mis Lookbooks"
# ---------------------------------------------------------------------------
# Nota: en el backend real TODAS las simulaciones quedan persistidas (el
# historial es la fuente de verdad); "guardar" marca la intención del
# usuario de conservarla visible en su lookbook. El DELETE la quita.
@router.post("/lookbook", response_model=None)
def guardar_en_lookbook(
    payload: SimulacionGuardarPayload,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU8: Guarda la simulación en el lookbook del usuario."""
    simulacion = db.get(SimulacionProbador, payload.id_simulacion)
    if not simulacion or str(simulacion.id_usuario) != str(usuario_actual.id_usuario):
        raise HTTPException(
            status_code=404,
            detail=f"No existe la simulación con id {payload.id_simulacion}.",
        )

    return _envelope(
        _serializar_simulacion(simulacion),
        message="Prueba guardada en Mis Lookbooks.",
    )


# ---------------------------------------------------------------------------
# GET /historial — simulaciones del usuario
# ---------------------------------------------------------------------------
@router.get("/historial", response_model=None)
def historial_simulaciones(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=100),
):
    """CU8: Historial de simulaciones del usuario (recientes primero)."""
    items = (
        db.query(SimulacionProbador)
        .filter(SimulacionProbador.id_usuario == usuario_actual.id_usuario)
        .order_by(SimulacionProbador.fecha_simulacion.desc())
        .limit(limit)
        .all()
    )

    return _envelope(
        [_serializar_simulacion(s) for s in items],
        total=len(items),
    )


# ---------------------------------------------------------------------------
# DELETE /lookbook/{id} — quitar una prueba del lookbook
# ---------------------------------------------------------------------------
@router.delete("/lookbook/{id_simulacion}", response_model=None)
def eliminar_prueba(
    id_simulacion: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """CU8: Elimina una prueba del historial/lookbook del usuario."""
    simulacion = db.get(SimulacionProbador, id_simulacion)
    if not simulacion or str(simulacion.id_usuario) != str(usuario_actual.id_usuario):
        raise HTTPException(
            status_code=404,
            detail=f"No existe la simulación con id {id_simulacion}.",
        )

    db.delete(simulacion)
    db.commit()

    return _envelope(None, message="Prueba eliminada del lookbook.")
