"""Flujo completo del back, como lo usará la API:
mensaje -> intérprete -> (confirmación) -> validador -> MLP -> asignación -> redactor -> JSON para el front."""
import json
import os
import random
from datetime import date, time, timedelta

import pytest

from conftest import HOY, LLMSimulado, act, franjas, sem, verificar
from studyflow.asignacion import EstadoAsignacion, ResultadoAsignacion, ResumenActividad, Sesion, asignar
from studyflow.disponibilidad import minutos
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
    salida = planificar(plan, HOY, mlp, LLMSimulado({"mensaje": 'Tienes "Cálculo" el domingo 8 de noviembre a las 23:00.'}))
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


# --- Mover una sesión: la orden vuelve a pasar por el MLP -----------------------------------------
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


class MLPEspia:
    """Cuenta cuántas veces se le pide una predicción al MLP."""

    def __init__(self, mlp):
        self.mlp, self.consultas = mlp, 0

    def predecir(self, plan, fecha_inicio):
        self.consultas += 1
        return self.mlp.predecir(plan, fecha_inicio)


def _hora(m: int) -> time:
    return time(m // 60, m % 60)


def fijar_en_destino(horario: ResultadoAsignacion, orden, plan: PlanEntrada) -> list:
    """Pasa las sesiones del día de origen al de destino (desde hora_inicio, o a la misma hora si no se dio)."""
    movidas = sorted((s for s in horario.sesiones if s.dia == orden.dia_origen and orden.actividad in (None, s.actividad)),
                     key=lambda s: s.inicio)
    assert movidas, "no hay sesiones que mover en ese día"
    franjas_destino = [f for d in plan.disponibilidad if d.dia == orden.dia_destino for f in d.franjas]
    fijadas, cursor = [], minutos(orden.hora_inicio or movidas[0].inicio)
    for s in movidas:
        fin = cursor + minutos(s.fin) - minutos(s.inicio)
        assert any(minutos(f.inicio) <= cursor and fin <= minutos(f.fin) for f in franjas_destino), "el destino no tiene espacio a esa hora"
        fijadas.append(Sesion(actividad=s.actividad, dia=orden.dia_destino, inicio=_hora(cursor), fin=_hora(fin)))
        cursor = fin
    return fijadas


def unir(plan: PlanEntrada, original: ResultadoAsignacion, replanificado: ResultadoAsignacion, fijadas: list) -> ResultadoAsignacion:
    """El horario completo que vería el estudiante: lo fijado en el destino más lo replanificado."""
    sesiones = sorted(list(replanificado.sesiones) + fijadas, key=lambda s: (s.dia, s.inicio))
    previos = {a.nombre: a.prioridad for a in original.actividades} | {a.nombre: a.prioridad for a in replanificado.actividades}
    resumen = []
    for a in plan.actividades:
        asignadas = sum((minutos(s.fin) - minutos(s.inicio)) / 60 for s in sesiones if s.actividad == a.nombre)
        faltan = max(0.0, round(a.horas_estimadas - asignadas, 6))
        estado = EstadoAsignacion.ASIGNADA if faltan == 0 else EstadoAsignacion.NO_ASIGNADA if asignadas == 0 else EstadoAsignacion.PARCIAL
        resumen.append(ResumenActividad(nombre=a.nombre, prioridad=previos[a.nombre], horas_solicitadas=a.horas_estimadas,
                                        horas_asignadas=asignadas, horas_faltantes=faltan, estado=estado))
    return ResultadoAsignacion(sesiones=sesiones, actividades=sorted(resumen, key=lambda a: -a.prioridad))


def mover_y_replanificar(plan: PlanEntrada, original: ResultadoAsignacion, orden, hoy: date, mlp, llm_redactor) -> dict:
    """Lo que hará la capa de aplicación: fija lo movido y manda las horas restantes otra vez por validador, MLP y asignación."""
    fijadas = fijar_en_destino(original, orden, plan)
    fijas = {}
    for s in fijadas:
        fijas[s.actividad] = fijas.get(s.actividad, 0) + (minutos(s.fin) - minutos(s.inicio)) / 60
    pendientes = [a.model_copy(update={"horas_estimadas": a.horas_estimadas - fijas.get(a.nombre, 0)})
                  for a in plan.actividades if a.horas_estimadas - fijas.get(a.nombre, 0) > 1e-9]
    nuevo = plan.model_copy(update={"actividades": pendientes})
    assert validar(nuevo, hoy, "replanificacion").estado != Estado.INVIABLE
    replanificado = asignar(nuevo, mlp.predecir(nuevo, hoy), hoy, fijadas)
    completo = unir(plan, original, replanificado, fijadas)
    return {"fijadas": fijadas, "horario": completo, "mensaje": redactar(completo, llm_redactor)}


def comprobar_movimiento(plan, original, orden, hoy, espia, llm_redactor):
    salida = mover_y_replanificar(plan, original, orden, hoy, espia, llm_redactor)
    completo, fijadas = salida["horario"], salida["fijadas"]
    verificar(plan.model_dump(), completo, hoy)                          # fechas, disponibilidad, traslapes y resumen
    assert {s.dia for s in fijadas} == {orden.dia_destino} and fijadas[0].inicio == orden.hora_inicio
    assert all(f in completo.sesiones for f in fijadas)                  # la sesión movida quedó donde se pidió
    for a in original.actividades:                                       # las horas de cada actividad se conservan
        despues = next(x for x in completo.actividades if x.nombre == a.nombre)
        assert despues.horas_asignadas == pytest.approx(a.horas_asignadas)
    assert espia.consultas == 2                                          # el plan original y la replanificación pasaron por el MLP
    assert validar_texto(salida["mensaje"].texto, completo) == []
    return salida


def test_mover_una_sesion_y_replanificar_con_el_mlp(mlp):
    espia = MLPEspia(mlp)
    _, plan, salida = crear(espia)                                       # 1.ª consulta al MLP: plan con datos normales
    original = ResultadoAsignacion.model_validate(salida["horario"])
    origen = min(s.dia for s in original.sesiones if s.actividad == "Parcial de IA")
    llm = LLMSimulado({"accion": "mover_sesion", "actividad": "Parcial de IA", "dia_destino": sem("sábado"), "hora_inicio": "09:00",
                       "dia_origen": {"tipo": "fecha_exacta", "fecha": origen.isoformat()}})
    ctx = Contexto(hoy=HOY, actividades=[a.nombre for a in plan.actividades])
    r = interpretar(f"no me gustó la sesión del Parcial de IA del {origen.day} de {MESES[origen.month - 1]}, pásala al sábado a las 9 de la mañana", ctx, llm)
    assert r.tipo == "orden" and r.requiere_confirmacion and r.orden.accion == "mover_sesion"
    assert (r.orden.actividad, r.orden.dia_origen, r.orden.dia_destino, r.orden.hora_inicio) == ("Parcial de IA", origen, date(2026, 11, 7), time(9, 0))
    comprobar_movimiento(plan, original, r.orden, HOY, espia, LLMSimulado(TimeoutError()))


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


@pytest.mark.llm_real
def test_mover_una_sesion_con_llm_real(mlp):
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        pytest.skip("sin OPENAI_API_KEY en el .env")
    hoy, espia = date.today(), MLPEspia(mlp)
    plan = PlanEntrada(actividades=[act("Parcial de IA", 10, 8, "alta", "alta", inicio=hoy), act("Taller de redes", 6, 3, inicio=hoy),
                                    act("Lectura", 12, 2, "baja", "baja", inicio=hoy)],
                       disponibilidad=semana(hoy), duracion_sesion=90)
    salida = planificar(plan, hoy, espia, llm_real)
    original = ResultadoAsignacion.model_validate(salida["horario"])
    origen = min(s.dia for s in original.sesiones if s.actividad == "Parcial de IA")
    r = interpretar(f"no me gustó la sesión del Parcial de IA del {origen.day} de {MESES[origen.month - 1]}, pásala al sábado a las 9 de la mañana",
                    Contexto(hoy=hoy, actividades=[a.nombre for a in plan.actividades]), llm_real)
    assert r.tipo == "orden" and r.orden.accion == "mover_sesion", r
    assert r.orden.actividad == "Parcial de IA" and r.orden.dia_origen == origen and r.orden.hora_inicio == time(9, 0), r
    assert r.orden.dia_destino.weekday() == 5, r
    salida = comprobar_movimiento(plan, original, r.orden, hoy, espia, llm_real)
    print(salida["mensaje"].origen, salida["mensaje"].texto, sep="\n")
