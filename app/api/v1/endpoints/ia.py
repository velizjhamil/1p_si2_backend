# backend/app/api/v1/endpoints/ia.py
"""Router para el Asistente Inteligente y Recomendaciones con Google Gemini."""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user
from app.core.config import get_settings
from app.modules.usuarios.models import Usuario
from app.schemas.ia import ChatRequest, ChatResponse
from app.services.ia_service import IAService

router = APIRouter()
logger = logging.getLogger("attention.api.ia")


def _envelope(data: Any, message: str = "Operación exitosa", **extra) -> Dict[str, Any]:
    """Envelope estándar contractual: {status, data, message}."""
    payload = {
        "status": "success",
        "data": data,
        "message": message,
    }
    payload.update(extra)
    return payload


@router.post(
    "/chat",
    response_model=None,
    summary="Chat conversacional y recomendador de moda con Gemini IA",
    description=(
        "Recibe la consulta del cliente, realiza búsqueda contextual en la base de datos "
        "(productos, inventario, categorías, sucursales y promociones) y utiliza Google Gemini "
        "para responder en lenguaje natural con recomendaciones de prendas estructuradas."
    ),
)
def chat_asistente(
    request: ChatRequest,
    db: Session = Depends(get_db),
    usuario_actual: Usuario | None = Depends(get_optional_user),
) -> Dict[str, Any]:
    """Procesa el mensaje del usuario y devuelve la respuesta del asistente."""
    # Control de acceso: Si hay sesión activa y no es Cliente (C) ni Administrador, denegar
    if usuario_actual and usuario_actual.rol and usuario_actual.rol.nombre_rol not in ("C", "ADMIN", "ASU"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El asistente virtual de compras está reservado exclusivamente para clientes.",
        )

    try:
        resultado = IAService.generar_respuesta_chat(
            db, request, usuario_actual=usuario_actual
        )
        return _envelope(
            data=resultado.model_dump(),
            message="Respuesta generada exitosamente",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Error no controlado en endpoint /ia/chat: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al procesar la consulta con IA: {exc}",
        )


@router.get(
    "/status",
    response_model=None,
    summary="Estado de salud de la integración de Inteligencia Artificial",
)
def status_ia(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retorna el diagnóstico de configuración de Gemini y la base de datos."""
    settings = get_settings()
    has_api_key = bool(settings.GEMINI_API_KEY)
    masked_key = (
        f"{settings.GEMINI_API_KEY[:6]}...{settings.GEMINI_API_KEY[-4:]}"
        if has_api_key and len(settings.GEMINI_API_KEY) > 10
        else "No configurada"
    )

    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"Error de base de datos en health check IA: {e}")

    return _envelope(
        data={
            "servicio": "Attention IA Assistant",
            "gemini_configurado": has_api_key,
            "gemini_api_key": masked_key,
            "modelo_principal": settings.GEMINI_MODEL,
            "database_online": db_ok,
            "capacidades": [
                "Recomendación de prendas por estilo, talla y color",
                "Consulta de horarios y direcciones de sucursales",
                "Información de cupones y descuentos activos",
                "Generación de sugerencias conversacionales",
            ],
        },
        message="Servicio de IA activo y operativo",
    )
