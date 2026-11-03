"""Utilidades de texto del cliente del LLM. Ver StudyFlow_CapaLLM.ipynb, 2.1."""
from datetime import date

from studyflow.llm import DIAS_SEMANA, MESES, duracion_texto, fecha_texto


def test_fecha_texto():
    assert fecha_texto(date(2026, 11, 5)) == "jueves 5 de noviembre"
    assert fecha_texto(date(2026, 11, 1)) == "domingo 1 de noviembre"
    assert fecha_texto(date(2027, 3, 31)) == "miércoles 31 de marzo"


def test_duracion_texto():
    assert [duracion_texto(m) for m in (0, 30, 45, 60, 90, 120, 190)] == ["0 min", "30 min", "45 min", "1 h", "1 h 30 min", "2 h", "3 h 10 min"]
    assert len(DIAS_SEMANA) == 7 and len(MESES) == 12
