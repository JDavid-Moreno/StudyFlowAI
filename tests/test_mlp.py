"""Puente con el MLP: entradas y predicción con el modelo guardado en modelos/."""
import json

import pytest

from conftest import INICIO, act, dias, plan
from studyflow.mlp import CARPETA_MODELOS, caracteristicas
from studyflow.models import PlanEntrada

ESCALADO = json.loads((CARPETA_MODELOS / "escalado.json").read_text(encoding="utf-8"))
I = {k: i for i, k in enumerate(ESCALADO["entradas"])}


def x(actividades, disponibilidad, **kw):
    return caracteristicas(PlanEntrada(**plan(actividades, disponibilidad, **kw)), INICIO, ESCALADO)


def test_un_caso_calculado_a_mano():
    (fila,) = x([act("Taller", 10, 4, "alta", "baja")], dias(0, 14))     # 10 días de 3 h antes de la entrega
    assert fila[I["x_dias"]] == pytest.approx(2 * (10 - 3) / 27 - 1)
    assert fila[I["x_prioridad"]] == 1 and fila[I["x_dificultad"]] == -1
    assert fila[I["x_horas"]] == pytest.approx(2 * (4 - 1) / 19 - 1)
    assert fila[I["x_ventana"]] == pytest.approx(2 * (30 - 6) / 234 - 1)
    assert fila[I["x_carga"]] == pytest.approx(2 * 4 / 30 - 1)


def test_recorta_al_rango_de_entrenamiento():
    (fila,) = x([act("Tesis", 60, 50)], dias(0, 70, (("08:00", "18:00"),)), max_horas_dia=8)   # 60 días, 50 h, 480 h libres
    assert fila[I["x_dias"]] == 1 and fila[I["x_horas"]] == 1 and fila[I["x_ventana"]] == 1
    assert all(-1 <= v <= 1 for v in fila)


def test_solo_cuentan_los_dias_con_capacidad():
    disponibilidad = [d for n, d in enumerate(dias(0, 12)) if n % 2 == 0]     # día de por medio
    (fila,) = x([act("Taller", 10, 4)], disponibilidad)
    assert fila[I["x_dias"]] == pytest.approx(2 * (5 - 3) / 27 - 1)             # 5 días con capacidad, no 10
    assert fila[I["x_ventana"]] == pytest.approx(2 * (15 - 6) / 234 - 1)


def test_carga_cuenta_lo_que_vence_antes():
    a, b = x([act("Pronto", 5, 6), act("Tarde", 10, 6)], dias(0, 14))
    assert a[I["x_carga"]] == pytest.approx(2 * 6 / 15 - 1)                    # solo vence "Pronto"
    assert b[I["x_carga"]] == pytest.approx(2 * 12 / 30 - 1)                   # vencen las dos


def test_ventana_vacia_no_falla():
    (fila,) = x([act("Vieja", 0, 2)], dias(0, 5))
    assert fila[I["x_carga"]] == 1 and fila[I["x_ventana"]] == -1


@pytest.fixture(scope="module")
def predictor():
    pytest.importorskip("keras")
    from studyflow.mlp import PredictorMLP
    return PredictorMLP()


def test_predicciones_validas(predictor):
    p = PlanEntrada(**plan([act("Parcial", 4, 8, "alta", "alta"), act("Lectura", 20, 2, "baja", "baja"), act("Taller", 9, 5)], dias(0, 25)))
    preds = predictor.predecir(p, INICIO)
    assert len(preds) == 3
    for q in preds:
        assert 0 <= q.prioridad <= 1 and len(q.perfil) == 3 and abs(sum(q.perfil) - 1) < 1e-3


def test_lo_urgente_va_primero(predictor):
    p = PlanEntrada(**plan([act("Lejana", 25, 4), act("Cercana", 4, 4)], dias(0, 30)))
    lejana, cercana = predictor.predecir(p, INICIO)
    assert cercana.prioridad > lejana.prioridad
