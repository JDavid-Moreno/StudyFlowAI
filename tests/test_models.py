"""Capa Pydantic. Ver StudyFlow_CapaValidacion.ipynb, 2.1."""
import pytest

from conftest import franjas, rechaza
from studyflow.models import Actividad, DisponibilidadDia, Franja, Nivel, PlanEntrada

ACT_OK = dict(nombre="  Parcial  ", prioridad="ALTA", dificultad=" Media", fecha_limite="2026-11-02", horas_estimadas=4)
BASE = dict(actividades=[ACT_OK], disponibilidad=[{"dia": "2026-10-26", "franjas": franjas(("18:00", "20:00"))}], duracion_sesion=60)


def test_actividad_normaliza_texto():
    a = Actividad(**ACT_OK)
    assert a.nombre == "Parcial" and a.prioridad is Nivel.ALTA and a.dificultad is Nivel.MEDIA


@pytest.mark.parametrize("cambio, fragmento", [
    ({"nombre": "   "}, "string_too_short"),
    ({"nombre": "x" * 101}, "string_too_long"),
    ({"horas_estimadas": 0}, "greater_than"),
    ({"horas_estimadas": 101}, "less_than_equal"),
    ({"prioridad": "urgente"}, "enum"),
    ({"fecha_limite": "mañana"}, "date"),
])
def test_actividad_rechaza(cambio, fragmento):
    rechaza(lambda: Actividad(**{**ACT_OK, **cambio}), fragmento)


def test_franjas():
    Franja(inicio="18:00", fin="20:00")
    rechaza(lambda: Franja(inicio="20:00", fin="18:00"), "terminar")
    rechaza(lambda: Franja(inicio="18:00", fin="18:00"), "terminar")


def test_disponibilidad_dia():
    DisponibilidadDia(dia="2026-10-26", franjas=franjas(("18:00", "20:00"), ("20:00", "21:00")))  # pegadas, sin traslape
    DisponibilidadDia(dia="2026-10-26", franjas=[])
    rechaza(lambda: DisponibilidadDia(dia="2026-10-26", franjas=franjas(("18:00", "20:00"), ("19:00", "21:00"))), "traslapan")


@pytest.mark.parametrize("cambio, fragmento", [
    ({"actividades": []}, "too_short"),
    ({"disponibilidad": []}, "too_short"),
    ({"duracion_sesion": 45}, "enum"),
    ({"max_horas_dia": 0}, "greater_than"),
    ({"max_horas_dia": 9}, "less_than_equal"),
    ({"disponibilidad": BASE["disponibilidad"] * 2}, "repetidos"),
])
def test_plan_rechaza(cambio, fragmento):
    rechaza(lambda: PlanEntrada(**{**BASE, **cambio}), fragmento)


def test_plan_valido():
    PlanEntrada(**BASE)
    assert PlanEntrada(**{**BASE, "duracion_sesion": 240, "max_horas_dia": 8}).duracion_sesion == 240
