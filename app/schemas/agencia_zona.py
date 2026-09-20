# backend/app/schemas/agencia_zona.py
# Esquemas Pydantic para CU19 - zonas de cobertura de una agencia de reparto.
#
# Una zona = ciudad del catalogo (FK a `ciudades`) + subzona OPCIONAL
# (`nombre_zona`, la columna de agencia_zonas: barrio/zona dentro de la ciudad).
# La ciudad se indica de UNA de dos formas (exactamente una):
#   - `id_ciudad`: id del catalogo (inequivoco), o
#   - `ciudad`: nombre, que el service resuelve por nombre normalizado
#     (sin distinguir mayusculas ni tildes). Si el nombre es ambiguo, el service
#     rechaza con 409 y pide usar `id_ciudad`.
# Aca solo se valida la FORMA; las reglas de negocio viven en
# app/modules/delivery/zonas_service.py.
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.agencia import _recortar
from app.schemas.envio import _limpiar  # recorta; texto vacio -> None


class _ZonaBase(BaseModel):
    id_ciudad: int | None = Field(default=None, gt=0)
    ciudad: str | None = Field(default=None, max_length=100)
    nombre_zona: str | None = Field(default=None, max_length=100)

    _recorta = field_validator("ciudad", "nombre_zona", mode="before")(_recortar)
    _vacios = field_validator("ciudad", "nombre_zona")(_limpiar)

    @model_validator(mode="after")
    def _una_sola_forma_de_ciudad(self):
        if self.id_ciudad is not None and self.ciudad is not None:
            raise ValueError("Indique la ciudad con 'id_ciudad' o con 'ciudad', no con ambos.")
        return self


class ZonaCreate(_ZonaBase):
    """POST /api/v1/agencias-reparto/{id}/zonas. La ciudad es obligatoria."""

    @model_validator(mode="after")
    def _ciudad_obligatoria(self):
        if self.id_ciudad is None and self.ciudad is None:
            raise ValueError("Indique la ciudad con 'id_ciudad' o con 'ciudad'.")
        return self


class ZonaUpdate(_ZonaBase):
    """PUT /api/v1/agencias-reparto/{id}/zonas/{id_zona}.

    None = "no cambiar" (misma semantica que agencias). Por eso una subzona no
    se puede vaciar por update: para pasar a "toda la ciudad" se elimina y se
    vuelve a crear la zona."""


class ZonaRead(BaseModel):
    """Zona de cobertura. No incluye datos de la agencia (ni facturacion), asi
    que ASU/GS y D reciben la misma forma."""

    id_zona: int
    id_agencia: int
    id_ciudad: int
    ciudad: str
    departamento: str | None = None
    nombre_zona: str | None = None
    total_tarifas: int = 0
    fecha_creacion: datetime
