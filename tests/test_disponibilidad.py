"""Capacidad efectiva. Ver StudyFlow_CapaValidacion.ipynb, 3.1."""
from datetime import date

import pytest

from conftest import dia
from studyflow.disponibilidad import capacidad_dia, capacidad_entre, dias_con_capacidad


@pytest.mark.parametrize("pares, tope, esperado", [
    ((("11:00", "14:00"), ("16:00", "17:30")), None, 4.5),
    ((("11:00", "14:00"), ("16:00", "17:30")), 4, 4.0),
    ((("11:00", "14:00"), ("16:00", "17:30")), 2.75, 2.5),       # el tope también se redondea a bloques de 30 min
    ((("18:00", "19:30"),), None, 1.5),                           # 1 h 30 completa, sin perder el sobrante
    ((("16:00", "20:00"),), None, 4.0),
    ((("18:00", "18:50"),), None, 0.5),                           # 50 min -> 30 min
    ((("18:00", "18:45"), ("19:00", "19:45")), None, 1.0),        # cada franja se redondea sola
    ((("18:00", "18:20"),), None, 0),
    ((), None, 0),
])
def test_capacidad_dia(pares, tope, esperado):
    assert capacidad_dia(dia("2026-10-26", *pares), tope) == esperado


def test_capacidad_entre_y_dias():
    tres_dias = [dia(f"2026-10-{n}", ("18:00", "21:00")) for n in (26, 27, 28)]
    assert capacidad_entre(tres_dias, None, date(2026, 10, 26), date(2026, 10, 28)) == 6   # el día 28 (fin) no cuenta
    assert capacidad_entre(tres_dias, None, date(2026, 10, 27), date(2026, 10, 29)) == 6   # el día 26 queda antes del inicio
    assert capacidad_entre(tres_dias, 1, date(2026, 10, 26), date(2026, 10, 29)) == 3
    assert dias_con_capacidad(tres_dias + [dia("2026-10-29", ("18:00", "18:10"))], None, date(2026, 10, 26), date(2026, 10, 30)) == 3
