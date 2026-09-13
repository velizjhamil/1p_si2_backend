# backend/app/schemas/catalogo.py
# Esquemas Pydantic para CU24 — Temporadas y Colecciones.
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Colecciones
# ---------------------------------------------------------------------------
class ColeccionRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/colecciones."""

    model_config = ConfigDict(from_attributes=True)

    id_coleccion: int
    nombre_coleccion: str
    fecha_creacion: datetime | None = None


class ColeccionCreate(BaseModel):
    """Payload de POST /api/v1/colecciones — nombre obligatorio y único."""

    nombre_coleccion: str = Field(min_length=2, max_length=100)


class ColeccionUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/colecciones/{id}.

    None = "no cambiar" (semántica PATCH sobre PUT, igual que el resto).
    """

    nombre_coleccion: str | None = Field(default=None, min_length=2, max_length=100)


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------
class TemporadaRead(BaseModel):
    """Respuesta de GET/POST/PUT /api/v1/temporadas.

    `vigente` es un campo calculado en el schema: fecha_inicio <= hoy <=
    fecha_fin. Se deriva en la serialización para que el badge del frontend
    siempre refleje el estado al día de la consulta.
    """

    model_config = ConfigDict(from_attributes=True)

    id_temporada: int
    id_coleccion: int | None = None
    nombre_temporada: str
    fecha_inicio: date
    fecha_fin: date
    nombre_coleccion: str | None = None
    vigente: bool = False

    @model_validator(mode="after")
    def _derivar_vigencia(self) -> "TemporadaRead":
        hoy = date.today()
        self.vigente = self.fecha_inicio <= hoy <= self.fecha_fin
        return self


class TemporadaCreate(BaseModel):
    """Payload de POST /api/v1/temporadas.

    Regla de negocio CU24: fecha_fin >= fecha_inicio (422 si no se cumple).
    """

    id_coleccion: int | None = Field(default=None, description="FK opcional a colecciones")
    nombre_temporada: str = Field(min_length=2, max_length=100)
    fecha_inicio: date
    fecha_fin: date

    @model_validator(mode="after")
    def _validar_rango(self) -> "TemporadaCreate":
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de fin no puede ser anterior a la fecha de inicio.")
        return self


class TemporadaUpdate(BaseModel):
    """Payload parcial de PUT /api/v1/temporadas/{id}.

    La regla fecha_fin >= fecha_inicio se re-valida CONTRA LA ENTIDAD
    COMBINADA: si solo viene un extremo, se contrasta con el valor persistido
    (ej: mover fecha_inicio más allá del fecha_fin actual debe fallar).
    """

    id_coleccion: int | None = None
    nombre_temporada: str | None = Field(default=None, min_length=2, max_length=100)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
