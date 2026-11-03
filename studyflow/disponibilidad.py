"""Capacidad efectiva de estudio por día. Ver StudyFlow_CapaValidacion.ipynb, paso 3."""
from datetime import date, time

from studyflow.models import DisponibilidadDia

BLOQUE_MIN = 30  # unidad de capacidad y duración mínima de una sesión (minutos)


def minutos(t: time):
    return t.hour * 60 + t.minute


def capacidad_dia(dia: DisponibilidadDia, max_horas_dia: float | None = None):
    total = sum((minutos(f.fin) - minutos(f.inicio)) // BLOQUE_MIN * BLOQUE_MIN for f in dia.franjas)
    if max_horas_dia is not None:
        total = min(total, int(max_horas_dia * 60) // BLOQUE_MIN * BLOQUE_MIN)
    return total / 60


def capacidad_entre(disponibilidad: list[DisponibilidadDia], max_horas_dia: float | None, inicio: date, fin: date):
    """Horas efectivas de los días d con inicio <= d < fin (el día `fin` no cuenta)."""
    return sum(capacidad_dia(d, max_horas_dia) for d in disponibilidad if inicio <= d.dia < fin)


def dias_con_capacidad(disponibilidad: list[DisponibilidadDia], max_horas_dia: float | None, inicio: date, fin: date):
    return sum(1 for d in disponibilidad if inicio <= d.dia < fin and capacidad_dia(d, max_horas_dia) > 0)
