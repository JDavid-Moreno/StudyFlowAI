"""Puente con el MLP guardado: entradas de cada actividad y predicción. Ver StudyFlow_MLP.ipynb, pasos 5 y 6."""
import json
import os
from datetime import date
from pathlib import Path

from studyflow.asignacion import Prediccion
from studyflow.disponibilidad import capacidad_entre, dias_con_capacidad
from studyflow.models import PlanEntrada

CARPETA_MODELOS = Path(__file__).resolve().parent.parent / "modelos"


def _escalar(x: float, rango: list[float]) -> float:
    """Recorta al rango de entrenamiento y escala a [-1, 1]."""
    lo, hi = rango
    return 2.0 * (min(max(x, lo), hi) - lo) / (hi - lo) - 1.0


def caracteristicas(plan: PlanEntrada, fecha_inicio: date, escalado: dict) -> list[list[float]]:
    """Las 6 entradas de cada actividad, en el orden de `plan.actividades` y de `escalado["entradas"]`."""
    filas = []
    for a in plan.actividades:
        ventana = capacidad_entre(plan.disponibilidad, plan.max_horas_dia, fecha_inicio, a.fecha_limite)
        vencen = sum(b.horas_estimadas for b in plan.actividades if b.fecha_limite <= a.fecha_limite)
        x = {"x_dias": _escalar(dias_con_capacidad(plan.disponibilidad, plan.max_horas_dia, fecha_inicio, a.fecha_limite), escalado["dias"]),
             "x_prioridad": escalado["nivel"][a.prioridad.value],
             "x_dificultad": escalado["nivel"][a.dificultad.value],
             "x_horas": _escalar(a.horas_estimadas, escalado["horas"]),
             "x_ventana": _escalar(ventana, escalado["ventana"]),
             "x_carga": _escalar(vencen / ventana if ventana > 0 else 1.0, [0.0, 1.0])}
        filas.append([x[k] for k in escalado["entradas"]])
    return filas


class PredictorMLP:
    def __init__(self, carpeta: Path = CARPETA_MODELOS):
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        import keras
        self.modelo = keras.models.load_model(Path(carpeta) / "studyflow_mlp.keras")
        self.escalado = json.loads((Path(carpeta) / "escalado.json").read_text(encoding="utf-8"))

    def predecir(self, plan: PlanEntrada, fecha_inicio: date) -> list[Prediccion]:
        import numpy as np
        x = np.array(caracteristicas(plan, fecha_inicio, self.escalado), dtype="float32")
        prioridad, reparto = self.modelo.predict(x, verbose=0)
        return [Prediccion(prioridad=float(p[0]), perfil=[float(v) for v in r]) for p, r in zip(prioridad, reparto)]
