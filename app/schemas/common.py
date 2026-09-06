# backend/app/schemas/common.py
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Formato de respuesta estándar del proyecto (backend.md).

    {"status": "success", "data": {...}, "message": "..."}
    """

    status: str = "success"
    data: Optional[T] = None
    message: str = ""
