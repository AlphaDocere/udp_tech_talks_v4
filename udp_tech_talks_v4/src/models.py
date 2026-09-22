"""
Clases de datos (Pydantic) usadas en toda la app.
Pydantic nos da validación automática (ej: energia_llegada entre 0-100) y
serialización a dict/JSON consistente en todo el proyecto.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
from datetime import datetime


class Charla(BaseModel):
    """Representa una charla del evento, tal como viene en event_data.json."""

    model_config = ConfigDict(extra="ignore")

    id: str
    titulo: str
    charlista: str
    rol: str
    tema_tags: List[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump()

    @staticmethod
    def from_dict(data: dict) -> "Charla":
        return Charla(**{**data, "tema_tags": data.get("tema_tags", []) or []})


class Asistente(BaseModel):
    """
    Representa a un/a asistente del evento.
    Puede venir de Luma (origen="luma") o de un check-in en vivo (origen="live").
    Los campos de la "mini-encuesta" de check-in (energia_llegada, motivo_visita,
    curiosidad) se completan en la puerta, tanto para asistentes de Luma como
    para quienes llegan sin registro previo.
    """

    model_config = ConfigDict(extra="ignore")

    guest_id: str
    nombre: str
    email: str = ""
    comuna: str = ""
    rol_interes: str = ""
    origen: str = "live"  # "luma" | "live"
    estado_animo: str = ""  # emoji + etiqueta elegido en el check-in, ej. "😃 Con energía"
    energia_llegada: Optional[int] = None  # 0-100, derivado del estado de ánimo elegido
    motivo_visita: str = ""
    curiosidad: str = ""
    checked_in_at: Optional[str] = None

    @field_validator("energia_llegada")
    @classmethod
    def _validar_rango_energia(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("energia_llegada debe estar entre 0 y 100")
        return v

    def to_dict(self) -> dict:
        return self.model_dump()

    @staticmethod
    def from_dict(data: dict) -> "Asistente":
        return Asistente(**data)

    def esta_checkeado(self) -> bool:
        return bool(self.checked_in_at)


def timestamp_ahora() -> str:
    """Timestamp ISO 8601 usado en todo el proyecto para checked_in_at, etc."""
    return datetime.now().isoformat(timespec="seconds")
