"""Modelos de entrada (forma y valores). Ver StudyFlow_CapaValidacion.ipynb, paso 2."""
from datetime import date, time
from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Nivel(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class DuracionSesion(IntEnum):
    M30 = 30
    M60 = 60
    M90 = 90
    M120 = 120
    M150 = 150
    M180 = 180
    M210 = 210
    M240 = 240


class Actividad(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    nombre: str = Field(min_length=1, max_length=100)
    prioridad: Nivel
    dificultad: Nivel
    fecha_limite: date
    horas_estimadas: float = Field(gt=0, le=100)

    @field_validator("prioridad", "dificultad", mode="before")
    @classmethod
    def _nivel_sin_mayusculas(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class Franja(BaseModel):
    inicio: time
    fin: time

    @model_validator(mode="after")
    def _fin_despues_de_inicio(self):
        if self.fin <= self.inicio:
            raise ValueError("la franja debe terminar después de empezar")
        return self


class DisponibilidadDia(BaseModel):
    dia: date
    franjas: list[Franja]

    @model_validator(mode="after")
    def _sin_traslapes(self):
        ordenadas = sorted(self.franjas, key=lambda f: f.inicio)
        for a, b in zip(ordenadas, ordenadas[1:]):
            if b.inicio < a.fin:
                raise ValueError(f"las franjas {a.inicio}-{a.fin} y {b.inicio}-{b.fin} se traslapan")
        return self


class PlanEntrada(BaseModel):
    actividades: list[Actividad] = Field(min_length=1)
    disponibilidad: list[DisponibilidadDia] = Field(min_length=1)
    max_horas_dia: float | None = Field(default=None, gt=0, le=8)
    duracion_sesion: DuracionSesion

    @model_validator(mode="after")
    def _sin_dias_repetidos(self):
        dias = [d.dia for d in self.disponibilidad]
        if len(dias) != len(set(dias)):
            raise ValueError("hay días repetidos en la disponibilidad")
        return self
