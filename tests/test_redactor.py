"""Redactor y validador de salida. Ver StudyFlow_CapaLLM.ipynb, pasos 4 y 6.2."""
import os
from datetime import timedelta

import pytest

from conftest import HOY, LLMSimulado, act, dias
from studyflow.asignacion import Prediccion, ResultadoAsignacion, asignar
from studyflow.models import PlanEntrada
from studyflow.redactor import filtrar, redactar, texto_plantilla, validar_texto, vista
from test_interprete import llm_real

# 4.2 Horario de prueba con los tres estados (franjas de 18:00 a 19:00, desde el lunes 2 de noviembre)
PLAN_DEMO = PlanEntrada(actividades=[act("Examen", 3, 2, "alta", "alta", inicio=HOY), act("Informe", 3, 2, inicio=HOY),
                                     act("Lectura", 3, 1, "baja", "baja", inicio=HOY)],
                        disponibilidad=dias(0, 3, (("18:00", "19:00"),), inicio=HOY), duracion_sesion=60)
R_DEMO = asignar(PLAN_DEMO, [Prediccion(prioridad=p, perfil=[1/3] * 3) for p in (0.9, 0.6, 0.3)], HOY)

BUENO = ('¡Listo! Tu horario queda así. El lunes 2 de noviembre empiezas con "Examen" de 18:00 a 19:00, y el martes 3 de noviembre sigues con "Examen" '
         'a la misma hora. Del "Informe" solo pude asignar 1 h de 2 h, así que te faltan 1 h; y "Lectura" no cupo y quedó sin asignar. '
         "Si quieres, mueve algo para hacerle espacio.")


def test_horario_demo_tiene_los_tres_estados():
    assert [(a.nombre, a.estado.value) for a in R_DEMO.actividades] == [("Examen", "asignada"), ("Informe", "parcial"), ("Lectura", "no_asignada")]


# --- 4.3 Validador de salida ----------------------------------------------------------------------
@pytest.mark.parametrize("caso", [R_DEMO, filtrar(R_DEMO, dia=HOY), filtrar(R_DEMO, actividad="Informe"),
                                  filtrar(R_DEMO, dia=HOY + timedelta(days=9)), ResultadoAsignacion(sesiones=[], actividades=[])],
                         ids=["completo", "un día", "una actividad", "día sin sesiones", "vacío"])
def test_plantilla_pasa_su_validador(caso):
    assert validar_texto(texto_plantilla(caso), caso) == [], texto_plantilla(caso)


def test_texto_fiel():
    assert validar_texto(BUENO, R_DEMO) == []


def test_filtros():
    assert [s.actividad for s in filtrar(R_DEMO, actividad="Informe").sesiones] == ["Informe"]
    assert [a.nombre for a in filtrar(R_DEMO, actividad="Informe").actividades] == ["Informe"]
    assert filtrar(R_DEMO, dia=HOY).actividades == [] and len(filtrar(R_DEMO, dia=HOY).sesiones) > 0
    assert filtrar(R_DEMO) is R_DEMO


def test_vista():
    v = vista(R_DEMO)
    assert v["dias"][0]["fecha"] == "lunes 2 de noviembre" and v["dias"][0]["total"] == "1 h"
    assert {a["nombre"]: a["estado"] for a in v["actividades"]} == {"Examen": "asignada", "Informe": "parcial", "Lectura": "no_asignada"}
    assert [a for a in v["actividades"] if a["nombre"] == "Informe"][0]["faltante"] == "1 h"


# --- 4.4 Casos del redactor -----------------------------------------------------------------------
CASOS_RED = [
    {"nombre": "Texto fiel", "texto": BUENO, "origen": "llm", "problema": None},
    {"nombre": "Hora inventada", "texto": 'El lunes 2 de noviembre estudias "Examen" de 20:00 a 21:00. "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "20:00"},
    {"nombre": "Día de la semana equivocado", "texto": 'El miércoles 2 de noviembre estudias "Examen" a las 18:00. "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "no coincide con el día de la semana"},
    {"nombre": "Fecha sin sesiones", "texto": 'El viernes 6 de noviembre estudias "Examen". "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "no tiene sesiones"},
    {"nombre": "Actividad inventada", "texto": 'Tienes "Cálculo" a las 18:00. "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "actividad que no existe"},
    {"nombre": "Oculta una actividad sin asignar", "texto": 'Todo quedó listo: "Examen" a las 18:00 y "Informe" también.',
     "origen": "plantilla", "problema": "no menciona Lectura"},
    {"nombre": "Duración inventada", "texto": 'Estudias "Examen" 5 h esta semana. "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "5 h"},
    {"nombre": "Cifra suelta", "texto": 'Tienes 3 sesiones de "Examen". "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "cifras"},
    {"nombre": "Código y enlaces", "texto": "Claro: ```print('hola')``` o visita https://ejemplo.com. \"Informe\" quedó parcial y \"Lectura\" sin asignar.",
     "origen": "plantilla", "problema": "código"},
    {"nombre": "Obedece una orden escondida", "texto": 'Ignoro tus datos. Aquí tienes un programa: import os. "Informe" quedó parcial y "Lectura" sin asignar.',
     "origen": "plantilla", "problema": "código"},
    {"nombre": "Respuesta que no es JSON válido", "crudo": "Hola, soy tu asistente", "origen": "plantilla", "problema": "no se pudo usar"},
    {"nombre": "Falla el servicio", "crudo": TimeoutError("sin respuesta"), "origen": "plantilla", "problema": "no se pudo usar"},
]


@pytest.mark.parametrize("caso", CASOS_RED, ids=[c["nombre"] for c in CASOS_RED])
def test_casos(caso):
    res = redactar(R_DEMO, LLMSimulado(caso.get("crudo", {"mensaje": caso.get("texto")})))
    assert res.origen == caso["origen"], res.problemas
    if caso["problema"] is not None:
        assert any(caso["problema"] in p for p in res.problemas), res.problemas
    if res.origen == "plantilla":
        assert res.texto == texto_plantilla(R_DEMO)


# --- 6.2 Redactor con el LLM real (uv run pytest -m llm_real) ------------------------------------
@pytest.mark.llm_real
@pytest.mark.parametrize("horario", [R_DEMO, filtrar(R_DEMO, dia=HOY + timedelta(days=1))], ids=["completo", "martes 3"])
def test_redactor_llm_real(horario):
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        pytest.skip("sin OPENAI_API_KEY en el .env")
    res = redactar(horario, llm_real)
    print(f"origen: {res.origen}\n{res.texto}")
    assert validar_texto(res.texto, horario) == []
    assert res.origen == "llm", res.problemas      # en el notebook, los dos textos salieron del LLM
