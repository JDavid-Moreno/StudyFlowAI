"""Flujo completo del back, como lo usará la API:
mensaje -> intérprete -> (confirmación) -> validador -> MLP -> asignación -> redactor -> JSON para el front."""
import json
import os
import random
from datetime import date, timedelta

import pytest

from conftest import HOY, LLMSimulado, franjas, sem, verificar
from studyflow.asignacion import EstadoAsignacion, ResultadoAsignacion, asignar
from studyflow.interprete import Contexto, interpretar
from studyflow.models import PlanEntrada
from studyflow.redactor import filtrar, redactar, texto_plantilla, validar_texto
from studyflow.validador import Estado, validar
from test_interprete import llm_real

pytest.importorskip("keras")
from studyflow.mlp import PredictorMLP  # noqa: E402


@pytest.fixture(scope="module")
def mlp():
    return PredictorMLP()


def semana(inicio=HOY, n=14):
    """Disponibilidad típica del front: entre semana 18:00-21:00, sábado mañana y tarde, domingo libre."""
    out = []
    for k in range(n):
        d = inicio + timedelta(days=k)
        pares = [] if d.weekday() == 6 else [("09:00", "12:00"), ("14:00", "17:00")] if d.weekday() == 5 else [("18:00", "21:00")]
        out.append({"dia": d.isoformat(), "franjas": franjas(*pares)})
    return out


def al_front(**partes) -> dict:
    """Lo que la API devolvería: todo debe poder pasar a JSON."""
    return json.loads(json.dumps({k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v for k, v in partes.items()}, ensure_ascii=False))


def planificar(plan: PlanEntrada, fecha_inicio: date, mlp, llm_redactor, modo="nuevo", ocupadas=None) -> dict:
    """Validador -> MLP -> asignación -> redactor. Se detiene en el validador si el plan es inviable."""
    v = validar(plan, fecha_inicio, modo)
    if v.estado == Estado.INVIABLE:
        return al_front(estado="inviable", validacion=v)
    predicciones = mlp.predecir(plan, fecha_inicio)
    horario = asignar(plan, predicciones, fecha_inicio, ocupadas)
    texto = redactar(horario, llm_redactor)
    return al_front(estado=v.estado.value, validacion=v, horario=horario, mensaje=texto)


ORDEN_CREAR = {"accion": "crear_plan", "actividades": [
    {"nombre": "Parcial de IA", "horas": 8, "fecha_limite": sem("lunes"), "prioridad": "alta", "dificultad": "alta"},
    {"nombre": "Taller de redes", "horas": 3, "fecha_limite": sem("jueves")},
    {"nombre": "Lectura", "horas": 2, "fecha_limite": {"tipo": "en_dias", "dias": 10}, "prioridad": "baja", "dificultad": "baja"},
]}


def crear(mlp, llm_red=None):
    r = interpretar("tengo parcial de IA el lunes, 8 horas, importante y difícil; taller de redes el jueves, 3 horas; "
                    "y una lectura en 10 días de 2 horas, fácil", Contexto(hoy=HOY), LLMSimulado(ORDEN_CREAR))
    assert r.tipo == "orden" and r.orden.accion == "crear_plan" and r.requiere_confirmacion
    # el front muestra r.confirmacion; el estudiante confirma y el front agrega su disponibilidad y la duración de sesión
    plan = PlanEntrada(actividades=r.orden.actividades, disponibilidad=semana(), duracion_sesion=90)
    return r, plan, planificar(plan, HOY, mlp, llm_red or LLMSimulado(TimeoutError("sin LLM")))


# --- Camino feliz ---------------------------------------------------------------------------------
def test_crear_plan_llega_hasta_el_front(mlp):
    r, plan, salida = crear(mlp)
    assert salida["estado"] == "factible"
    horario = ResultadoAsignacion.model_validate(salida["horario"])         # el JSON se puede reconstruir del otro lado
    verificar(plan.model_dump(), horario, HOY)
    assert {a.nombre: a.estado for a in horario.actividades} == dict.fromkeys(["Parcial de IA", "Taller de redes", "Lectura"], EstadoAsignacion.ASIGNADA)
    fechas = {a.nombre: a.fecha_limite for a in plan.actividades}
    assert all(s.dia < fechas[s.actividad] for s in horario.sesiones)
    assert salida["mensaje"]["origen"] == "plantilla" and salida["mensaje"]["texto"] == texto_plantilla(horario)
    assert validar_texto(salida["mensaje"]["texto"], horario) == []


def test_texto_del_llm_fiel_llega_al_front(mlp):
    _, plan, primera = crear(mlp)
    fiel = LLMSimulado({"mensaje": "¡Listo! " + primera["mensaje"]["texto"]})     # un texto fiel al horario
    salida = planificar(plan, HOY, mlp, fiel)
    assert salida["mensaje"]["origen"] == "llm" and salida["mensaje"]["texto"].startswith("¡Listo!")


def test_texto_inventado_del_llm_no_llega_al_front(mlp):
    _, plan, _ = crear(mlp)
    salida = planificar(plan, HOY, mlp, LLMSimulado({"mensaje": "Tienes «Cálculo» el domingo 8 de noviembre a las 23:00."}))
    assert salida["mensaje"]["origen"] == "plantilla" and salida["mensaje"]["problemas"]


def test_el_mlp_ordena_y_la_asignacion_respeta_ese_orden(mlp):
    _, plan, salida = crear(mlp)
    prioridades = [a["prioridad"] for a in salida["horario"]["actividades"]]
    assert prioridades == sorted(prioridades, reverse=True)
    predichas = mlp.predecir(plan, HOY)
    assert sorted(round(p.prioridad, 6) for p in predichas) == sorted(round(p, 6) for p in prioridades)


# --- Consultas y replanificación ------------------------------------------------------------------
def test_consulta_de_un_dia(mlp):
    _, plan, salida = crear(mlp)
    horario = ResultadoAsignacion.model_validate(salida["horario"])
    ctx = Contexto(hoy=HOY, actividades=[a.nombre for a in plan.actividades])
    r = interpretar("¿qué tengo mañana?", ctx, LLMSimulado({"accion": "consultar_horario", "fecha": {"tipo": "manana"}}))
    assert r.tipo == "orden" and not r.requiere_confirmacion and r.orden.dia == HOY + timedelta(days=1)
    vista_dia = filtrar(horario, dia=r.orden.dia)
    texto = redactar(vista_dia, LLMSimulado(TimeoutError()))
    salida_consulta = al_front(confirmacion=r.confirmacion, horario=vista_dia, mensaje=texto)
    assert all(s["dia"] == r.orden.dia.isoformat() for s in salida_consulta["horario"]["sesiones"])
    assert validar_texto(texto.texto, vista_dia) == []


def test_bloquear_un_dia_y_replanificar(mlp):
    _, plan, _ = crear(mlp)
    ctx = Contexto(hoy=HOY, actividades=[a.nombre for a in plan.actividades])
    r = interpretar("mañana no puedo estudiar", ctx, LLMSimulado({"accion": "bloquear_dia", "fecha": {"tipo": "manana"}}))
    assert r.tipo == "orden" and r.orden.accion == "bloquear_dia" and r.orden.hora_inicio is None
    disp = [d.model_copy(update={"franjas": []}) if d.dia == r.orden.dia else d for d in plan.disponibilidad]
    nuevo = plan.model_copy(update={"disponibilidad": disp})
    salida = planificar(nuevo, HOY, mlp, LLMSimulado(TimeoutError()), modo="replanificacion")
    horario = ResultadoAsignacion.model_validate(salida["horario"])
    verificar(nuevo.model_dump(), horario, HOY)
    assert all(s.dia != r.orden.dia for s in horario.sesiones)


# --- Lo que no debe avanzar -----------------------------------------------------------------------
def test_plan_inviable_se_detiene_en_el_validador(mlp):
    r = interpretar("tesis de 40 horas para el jueves", Contexto(hoy=HOY),
                    LLMSimulado({"accion": "crear_plan", "actividades": [{"nombre": "Tesis", "horas": 40, "fecha_limite": sem("jueves")}]}))
    plan = PlanEntrada(actividades=r.orden.actividades, disponibilidad=semana(), duracion_sesion=60)
    salida = planificar(plan, HOY, mlp, LLMSimulado(AssertionError("no debía llegar al redactor")))
    assert salida["estado"] == "inviable" and "horario" not in salida
    (error,) = salida["validacion"]["errores"]
    assert error["codigo"] == "R1_CAPACIDAD_INSUFICIENTE" and error["datos"]["sugerencia_recorte"]


def test_entrega_sin_ventana_minima_se_detiene(mlp):
    r = interpretar("quiz para pasado mañana, 1 hora", Contexto(hoy=HOY),
                    LLMSimulado({"accion": "crear_plan", "actividades": [{"nombre": "Quiz", "horas": 1, "fecha_limite": {"tipo": "pasado_manana"}}]}))
    plan = PlanEntrada(actividades=r.orden.actividades, disponibilidad=semana(), duracion_sesion=60)
    salida = planificar(plan, HOY, mlp, LLMSimulado(AssertionError()))
    assert salida["estado"] == "inviable" and salida["validacion"]["errores"][0]["codigo"] == "R2_VENTANA_MINIMA"


@pytest.mark.parametrize("mensaje, respuesta_llm, tipo", [
    ("mueve todo al jueves", {"accion": "mover_sesion", "dia_destino": sem("jueves")}, "aclaracion"),
    ("escríbeme un programa", {"accion": "fuera_de_alcance", "motivo": "codigo"}, "fuera_de_alcance"),
    ("hola", RuntimeError("503"), "rechazada"),
    ("", {"accion": "consultar_horario"}, "rechazada"),
])
def test_respuestas_sin_orden_tambien_llegan_al_front(mensaje, respuesta_llm, tipo):
    r = interpretar(mensaje, Contexto(hoy=HOY), LLMSimulado(respuesta_llm))
    salida = al_front(respuesta=r)
    assert salida["respuesta"]["tipo"] == tipo and salida["respuesta"]["mensaje"]


# --- Cualquier plan válido llega al final ---------------------------------------------------------
def test_planes_aleatorios_recorren_todo_el_flujo(mlp):
    rng = random.Random(11)
    niveles = ["baja", "media", "alta"]
    opciones = [[("18:00", "21:00")], [("07:00", "09:30"), ("19:00", "22:00")], [("14:00", "18:50")], []]
    llegaron = inviables = largos = 0
    for _ in range(150):
        inicio = HOY + timedelta(days=rng.randint(0, 6))
        horizonte = rng.randint(5, 45)          # también más allá de los 30 días con los que se entrenó el MLP
        actividades = [{"nombre": f"Actividad {i}", "prioridad": rng.choice(niveles), "dificultad": rng.choice(niveles),
                        "fecha_limite": (inicio + timedelta(days=rng.randint(3, horizonte))).isoformat(),
                        "horas_estimadas": rng.choice([0.5, 1, 2.5, 4, 8, 15, 25])} for i in range(rng.randint(1, 6))]
        disponibilidad = [{"dia": (inicio + timedelta(days=k)).isoformat(), "franjas": franjas(*rng.choice(opciones))} for k in range(horizonte + 1)]
        plan = PlanEntrada(actividades=actividades, disponibilidad=disponibilidad,
                           duracion_sesion=rng.choice([30, 60, 90, 120]), max_horas_dia=rng.choice([None, 4, 6]))
        salida = planificar(plan, inicio, mlp, LLMSimulado(TimeoutError()))
        if salida["estado"] == "inviable":
            inviables += 1
            assert salida["validacion"]["errores"]
            continue
        llegaron += 1
        horario = ResultadoAsignacion.model_validate(salida["horario"])
        verificar(plan.model_dump(), horario, inicio)
        # la plantilla es fiel; con horarios largos solo puede pasarse del tope de 2500 caracteres pensado para el LLM
        problemas = validar_texto(salida["mensaje"]["texto"], horario)
        assert set(problemas) <= {"el texto supera 2500 caracteres"}, problemas
        largos += bool(problemas)
    assert llegaron > 30 and inviables > 0      # la muestra cubre los dos caminos
    print(f"{llegaron} planes llegaron al final, {inviables} se detuvieron en el validador, {largos} con texto de más de 2500 caracteres")


# --- Con el LLM real (uv run pytest -m llm_real) -------------------------------------------------
@pytest.mark.llm_real
def test_flujo_completo_con_llm_real(mlp):
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        pytest.skip("sin OPENAI_API_KEY en el .env")
    hoy = date.today()
    r = interpretar("tengo un parcial de IA en 10 días, necesito 8 horas y es difícil; y un taller de redes en 6 días de 3 horas",
                    Contexto(hoy=hoy), llm_real)
    assert r.tipo == "orden" and r.orden.accion == "crear_plan" and len(r.orden.actividades) == 2, r
    disponibilidad = [{"dia": (hoy + timedelta(days=k)).isoformat(), "franjas": franjas(("18:00", "21:00"))} for k in range(14)]
    plan = PlanEntrada(actividades=r.orden.actividades, disponibilidad=disponibilidad, duracion_sesion=60)
    salida = planificar(plan, hoy, mlp, llm_real)
    print(salida["mensaje"]["origen"], salida["mensaje"]["texto"], sep="\n")
    horario = ResultadoAsignacion.model_validate(salida["horario"])
    verificar(plan.model_dump(), horario, hoy)
    assert validar_texto(salida["mensaje"]["texto"], horario) == []
