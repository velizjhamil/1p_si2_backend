# backend/app/schemas/ia.py
"""Esquemas Pydantic para el Asistente IA de Recomendaciones y Chatbot."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Mensaje individual en el historial conversacional."""

    rol: Literal["usuario", "asistente", "sistema"] = Field(
        ..., description="Rol del emisor del mensaje"
    )
    contenido: str = Field(..., min_length=1, description="Texto del mensaje")


class ChatRequest(BaseModel):
    """Payload para el endpoint POST /api/v1/ia/chat."""

    mensaje: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Pregunta o consulta de moda / prendas del usuario",
        examples=["¿Qué blusas elegantes tienen disponibles en talla M?"],
    )
    historial: List[ChatMessage] = Field(
        default=[],
        description="Historial reciente de la conversación para mantener contexto",
    )
    id_sucursal: Optional[int] = Field(
        default=None,
        description="ID opcional de sucursal para consultar disponibilidad física",
    )


class ColorResumen(BaseModel):
    """Color disponible para una prenda recomendada."""

    nombre_color: str
    codigo_hex: Optional[str] = None


class ProductoResumenIA(BaseModel):
    """Detalle compacto de una prenda recomendada para tarjetas móviles interactivas."""

    id_producto: int
    nombre: str
    precio_venta: float
    imagen_url: Optional[str] = None
    categoria: Optional[str] = None
    linea: Optional[str] = None
    tallas: List[str] = []
    colores: List[ColorResumen] = []
    stock_total: int = 0


class ChatResponseData(BaseModel):
    """Contenido del payload 'data' en la respuesta del asistente IA."""

    respuesta: str = Field(
        ...,
        description="Respuesta conversacional completa del asistente en Markdown",
    )
    productos_recomendados: List[int] = Field(
        default=[],
        description="Lista de IDs de productos citados o recomendados",
    )
    productos_detalle: List[ProductoResumenIA] = Field(
        default=[],
        description="Información completa de los productos recomendados para renderizado visual",
    )
    sugerencias: List[str] = Field(
        default=[],
        description="Preguntas rápidas sugeridas que el usuario puede realizar a continuación",
    )


class ChatResponse(BaseModel):
    """Envelope contractual estándar del backend: {status, data, message}."""

    status: str = "success"
    data: ChatResponseData
    message: str = "Respuesta generada exitosamente"
