# backend/app/schemas/probador.py
# Esquemas Pydantic para CU8 — Probador Virtual AR.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Complexiones derivadas del IMC de referencia (mismo criterio del mock)
COMPLEXIONES = ("DELGADA", "MEDIA", "ROBUSTA", "NO_INDICADA")

# Ajustes estimados posibles de la simulación
AJUSTES_ESTIMADOS = ("PERFECTO", "AJUSTADO", "HOLGADO")

# Rangos de validación de medidas morfométricas (mismos del frontend)
ESTATURA_MIN, ESTATURA_MAX = 100, 230
PESO_MIN, PESO_MAX = 25, 250

# Orden relativo de tallas para el motor de recomendación
ORDEN_TALLAS = ("XS", "S", "M", "L", "XL", "XXL")


class FotoUsuarioPayload(BaseModel):
    """Payload de POST /api/v1/probador-virtual/subir-foto.

    imagen_data_url: Data URL (base64) de la foto — la "IA" del probador
    la procesa y deriva la complexión si hay medidas. El usuario se toma
    del TOKEN de la sesión.
    """

    imagen_data_url: str = Field(min_length=10)
    estatura: int | None = Field(default=None, ge=ESTATURA_MIN, le=ESTATURA_MAX)
    peso: int | None = Field(default=None, ge=PESO_MIN, le=PESO_MAX)

    @model_validator(mode="after")
    def _validar_data_url(self) -> "FotoUsuarioPayload":
        if not self.imagen_data_url.startswith("data:image/"):
            raise ValueError(
                "imagen_data_url debe ser un Data URL de imagen (data:image/...)."
            )
        return self


class FotoUsuarioResponse(BaseModel):
    """Foto procesada con las medidas derivadas."""

    model_config = ConfigDict(from_attributes=True)

    id_foto: int
    url_imagen: str
    fecha_subida: datetime
    estatura_cm: int | None = None
    peso_kg: int | None = None
    complexion: str


class SimulacionPayload(BaseModel):
    """Payload de POST /api/v1/probador-virtual/probar.

    foto_id: foto base del cliente (subida previamente).
    producto_id: prenda del catálogo real a superponer.
    talla_seleccionada: talla que el cliente quiere probar.
    color_nombre / color_hex: variante cromática probada (opcional).
    """

    foto_id: int = Field(gt=0)
    producto_id: int = Field(gt=0)
    talla_seleccionada: str = Field(min_length=1, max_length=20)
    color_nombre: str | None = Field(default=None, max_length=50)
    color_hex: str | None = Field(default=None, max_length=7)

    @model_validator(mode="after")
    def _validar_hex(self) -> "SimulacionPayload":
        if self.color_hex is not None and not (
            self.color_hex.startswith("#") and len(self.color_hex) == 7
        ):
            raise ValueError("color_hex debe tener formato #RRGGBB.")
        return self


class SimulacionResponse(BaseModel):
    """Resultado de la simulación con recomendación de ajuste.

    Estructura alineada con el modelo SimulacionProbador del frontend:
    el componente recibe talla_recomendada, ajuste_estimado y la URL de
    la imagen superpuesta (la foto del cliente; el overlay AR lo dibuja
    el visualizador).
    """

    model_config = ConfigDict(from_attributes=True)

    id_simulacion: int
    producto_id: int
    producto_nombre: str
    prenda_imagen_url: str | None = None
    resultado_imagen_url: str | None = None
    talla_seleccionada: str
    talla_recomendada: str
    ajuste_estimado: str
    color_seleccionado: str | None = None
    color_hex: str | None = None
    precio: float | None = None
    categoria: str | None = None
    fecha_simulacion: datetime
    guardada: bool = False


class SimulacionGuardarPayload(BaseModel):
    """Payload de POST /api/v1/probador-virtual/lookbook.

    Marca una simulación como guardada en "Mis Lookbooks" del usuario.
    """

    id_simulacion: int = Field(gt=0)
