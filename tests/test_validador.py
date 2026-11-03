"""Validador de coherencia. Ver StudyFlow_CapaValidacion.ipynb, pasos 4 y 5."""
import pytest
from pydantic import ValidationError

from conftest import INICIO, act, dias, f, franjas, plan
from studyflow.models import PlanEntrada
from studyflow.validador import Estado, validar

R1, R2, R3A, R3B = "R1_CAPACIDAD_INSUFICIENTE", "R2_VENTANA_MINIMA", "R3A_SESION_MAYOR_MAXIMO", "R3B_DIA_SIN_CAPACIDAD"
R4, R5, R6 = "R4_FECHA_PASADA", "R5_DISPONIBILIDAD_PASADA", "R6_NOMBRE_DUPLICADO"


def esp(estado, errores=(), advertencias=()):
    return {"estado": estado, "errores": sorted(errores), "advertencias": sorted(advertencias)}


CASOS = [
    {"nombre": "Plan factible holgado", "entrada": plan([act("Taller", 7, 4), act("Informe", 10, 3)], dias(0, 10)), "esperado": esp("factible")},
    {"nombre": "Total inviable", "entrada": plan([act("Tesis", 5, 40)], dias(0, 10)), "esperado": esp("inviable", [R1])},
    {"nombre": "Total viable, entrega cercana inviable",
     "entrada": plan([act("Taller A", 3, 5), act("Taller B", 3, 5), act("Proyecto", 10, 4)], dias(0, 10)), "esperado": esp("inviable", [R1])},
    {"nombre": "Plan justo al 100 %", "entrada": plan([act("Examen", 3, 9)], dias(0, 5)), "esperado": esp("factible")},
    {"nombre": "Ventana de 2 días (R2, nuevo)", "entrada": plan([act("Quiz", 2, 2)], dias(0, 5)), "esperado": esp("inviable", [R2])},
    {"nombre": "Ventana de 2 días (R2, replanificación)", "modo": "replanificacion",
     "entrada": plan([act("Quiz", 2, 2)], dias(0, 5)), "esperado": esp("factible_con_advertencias", [], [R2])},
    {"nombre": "Ventana con un día sin disponibilidad",
     "entrada": plan([act("Lectura", 4, 2)], dias(0, 1) + dias(2, 3) + dias(5, 7)), "esperado": esp("inviable", [R2])},
    {"nombre": "Sesión mayor que el máximo diario (R3a)",
     "entrada": plan([act("Taller", 7, 2)], dias(0, 10), sesion=90, max_horas_dia=1), "esperado": esp("inviable", [R3A])},
    {"nombre": "Día con franja menor a 30 min (R3b)",
     "entrada": plan([act("Taller", 7, 4)], dias(0, 3) + dias(3, 4, (("18:00", "18:20"),)) + dias(4, 10)),
     "esperado": esp("factible_con_advertencias", [], [R3B])},
    {"nombre": "Sobrante de la franja aprovechado",
     "entrada": plan([act("Proyecto", 3, 12)], dias(0, 5, (("16:00", "20:00"),)), sesion=90), "esperado": esp("factible")},
    {"nombre": "Fecha límite en el pasado (R4)",
     "entrada": plan([act("Entrega vieja", -2, 2), act("Taller", 7, 4)], dias(0, 10)), "esperado": esp("inviable", [R4])},
    {"nombre": "Disponibilidad de días pasados (R5)",
     "entrada": plan([act("Taller", 7, 4)], dias(-2, 10)), "esperado": esp("factible_con_advertencias", [], [R5])},
    {"nombre": "Nombres duplicados (R6)",
     "entrada": plan([act("Parcial", 7, 2), act("parcial ", 9, 2)], dias(0, 10)), "esperado": esp("factible_con_advertencias", [], [R6])},
    {"nombre": "Prioridad con mayúsculas", "entrada": plan([act("Taller", 7, 2, "ALTA", " Baja")], dias(0, 10)), "esperado": esp("factible")},
    {"nombre": "Días repetidos", "entrada": plan([act("Taller", 7, 2)], dias(0, 3) + dias(2, 5)), "esperado": {"pydantic": "repetidos"}},
    {"nombre": "Franjas traslapadas",
     "entrada": plan([act("Taller", 7, 2)], [{"dia": f(0), "franjas": franjas(("18:00", "20:00"), ("19:00", "21:00"))}]),
     "esperado": {"pydantic": "traslapan"}},
    {"nombre": "Disponibilidad vacía", "entrada": plan([act("Taller", 7, 2)], []), "esperado": {"pydantic": "too_short"}},
    {"nombre": "Actividad de 0 horas", "entrada": plan([act("Taller", 7, 0)], dias(0, 10)), "esperado": {"pydantic": "greater_than"}},
    {"nombre": "Nombre solo espacios", "entrada": plan([act("   ", 7, 2)], dias(0, 10)), "esperado": {"pydantic": "string_too_short"}},
    {"nombre": "Sesión fuera del enum", "entrada": plan([act("Taller", 7, 2)], dias(0, 10), sesion=45), "esperado": {"pydantic": "enum"}},
]


def ejecutar(caso):
    try:
        p = PlanEntrada(**caso["entrada"])
    except ValidationError as e:
        return {"pydantic": "; ".join(f"{x['type']} ({x['msg']})" for x in e.errors())}
    r = validar(p, INICIO, caso.get("modo", "nuevo"))
    return esp(r.estado.value, [h.codigo for h in r.errores], [h.codigo for h in r.advertencias])


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_casos(caso):
    lograda, esperado = ejecutar(caso), caso["esperado"]
    if "pydantic" in esperado:
        assert "pydantic" in lograda and esperado["pydantic"] in lograda["pydantic"], lograda
    else:
        assert lograda == esperado


def test_ejemplo_completo():
    ejemplo = plan([act("Taller", 3, 4, "alta"), act("Lectura", 3, 3, "baja"), act("Informe", 3, 3, "media")], dias(0, 10))
    r = validar(PlanEntrada(**ejemplo), INICIO)
    assert r.estado == Estado.INVIABLE
    (e,) = r.errores
    assert e.codigo == R1 and e.datos["deficit_horas"] == 1 and e.datos["sugerencia_recorte"] == [{"nombre": "Lectura", "horas": 3.0}]
