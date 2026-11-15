"""Pruebas del servicio de la API: cada petición de punta a punta con un MLP y un LLM simulados. Ver README, sección API."""
from datetime import date, datetime, time, timedelta

import pytest

from conftest import LLMGuion, PredictorFijo, act, dias, f, plan
from studyflow.asignacion import Sesion
from studyflow.models import PlanEntrada
from studyflow.persistencia import conectar, tomar_orden_pendiente
from studyflow.servicio import AVISO_REEMPLAZO, Servicio

AHORA = datetime(2026, 11, 2, 8, 0)      # lunes, antes de la primera franja
HOY = AHORA.date()


def fx(n: int) -> dict:
    """Fecha n días después de hoy, como la devolvería el LLM."""
    return {"tipo": "fecha_exacta", "fecha": f(n, HOY)}


def plan_base(horas_parcial=8) -> PlanEntrada:
    """Parcial de IA (vence en 6 días) y Taller (en 5), con 18:00-21:00 los próximos 10 días."""
    return PlanEntrada(**plan([act("Parcial de IA", 6, horas_parcial, "alta", "alta", inicio=HOY), act("Taller", 5, 4, inicio=HOY)],
                              dias(0, 10, inicio=HOY)))


@pytest.fixture
def con():
    c = conectar(":memory:")
    yield c
    c.close()


def servicio(con, *ordenes, ahora=AHORA) -> tuple[Servicio, LLMGuion]:
    """Un servicio con el MLP fijo y un LLM que responde el guion."""
    llm = LLMGuion(*ordenes)
    return Servicio(con, PredictorFijo(), llm, ahora), llm


def con_plan(con) -> tuple[str, Servicio]:
    """Un estudiante con el plan base ya creado."""
    s, _ = servicio(con)
    e = s.nuevo_estudiante()
    assert s.crear_plan(e, plan_base(), reemplazar=False).tipo == "aplicada"
    return e, s


BLOQUEO_LARGO = {"accion": "bloquear_dia", "fecha": fx(1), "fecha_fin": fx(4)}       # deja el plan sin cabida


# --- Crear el plan --------------------------------------------------------------------------------
def test_crear_plan_guarda_y_redacta(con):
    """El plan del formulario se crea, se guarda y el redactor lo cuenta (una sola llamada al LLM)."""
    s, llm = servicio(con)
    e = s.nuevo_estudiante()
    r = s.crear_plan(e, plan_base(), reemplazar=False)
    assert r.tipo == "aplicada" and r.texto_horario == "Tu horario quedó listo."
    assert r.estado.plan == plan_base() and len(r.estado.horario.sesiones) > 0 and llm.llamadas == ["Redaccion"]
    assert s.estado(e) == r.estado


def test_crear_otro_plan_pide_confirmar_el_reemplazo(con):
    """Con un plan guardado, crear otro primero avisa; solo con reemplazar=True se reemplaza."""
    e, s = con_plan(con)
    nuevo = PlanEntrada(**plan([act("Lectura", 5, 3, inicio=HOY)], dias(0, 6, inicio=HOY)))
    aviso = s.crear_plan(e, nuevo, reemplazar=False)
    assert aviso.tipo == "confirmar_reemplazo" and aviso.mensaje == AVISO_REEMPLAZO and aviso.estado.plan == plan_base()
    r = s.crear_plan(e, nuevo, reemplazar=True)
    assert r.tipo == "aplicada" and r.estado.plan == nuevo


def test_plan_invalido_no_reemplaza_el_anterior(con):
    """Si el plan nuevo se rechaza, el anterior y lo que esperaba confirmación siguen intactos."""
    e, s = con_plan(con)
    s2, _ = servicio(con, BLOQUEO_LARGO)
    assert s2.mensaje(e, "bloquea del martes al viernes").tipo == "confirmacion"
    repetido = PlanEntrada(**plan([act("A", 5, 2, inicio=HOY), act("a", 5, 2, inicio=HOY)], dias(0, 6, inicio=HOY)))
    r = s.crear_plan(e, repetido, reemplazar=True)
    assert r.tipo == "rechazada" and r.estado.plan == plan_base() and tomar_orden_pendiente(con, e) is not None


def test_primer_plan_que_no_cabe_se_resuelve_con_la_decision(con):
    """Si el primer plan no cabe, no se guarda: queda la decisión, y al recortar se guarda el plan recortado."""
    s, _ = servicio(con, {"accion": "responder_decision", "opcion": "recortar", "protegidas": []})
    e = s.nuevo_estudiante()
    r = s.crear_plan(e, plan_base(horas_parcial=20), reemplazar=False)
    assert r.tipo == "decision" and r.estado.plan is None and "cancelar" in r.estado.decision.opciones
    confirmar = s.mensaje(e, "recorta lo que haga falta")
    assert confirmar.tipo == "confirmacion" and "Quedaría así" in confirmar.mensaje
    hecho = s.confirmar(e, aceptar=True)
    assert hecho.tipo == "aplicada" and hecho.estado.plan is not None and hecho.estado.decision is None


# --- Mensajes del chat ----------------------------------------------------------------------------
def test_un_cambio_espera_confirmacion_y_luego_se_aplica(con):
    """Bloquear un día pide confirmación sin cambiar nada; al confirmar se aplica y se redacta."""
    e, _ = con_plan(con)
    s, llm = servicio(con, {"accion": "bloquear_dia", "fecha": fx(1)})
    antes = s.estado(e)
    r = s.mensaje(e, "el martes no puedo")
    assert r.tipo == "confirmacion" and r.mensaje.endswith("¿Confirmas?") and r.estado == antes
    hecho = s.confirmar(e, aceptar=True)
    assert hecho.tipo == "aplicada" and hecho.texto_horario
    assert not any(x.dia == HOY + timedelta(days=1) for x in hecho.estado.horario.sesiones)
    assert llm.llamadas == ["OrdenLLM", "Redaccion"]


def test_no_confirmar_descarta_la_orden(con):
    """Si el estudiante no confirma, la orden se descarta y ya no hay nada que confirmar."""
    e, _ = con_plan(con)
    s, _ = servicio(con, {"accion": "bloquear_dia", "fecha": fx(1)})
    s.mensaje(e, "el martes no puedo")
    assert s.confirmar(e, aceptar=False).tipo == "descartada"
    assert s.confirmar(e, aceptar=True).tipo == "sin_pendiente"


def test_consulta_no_pide_confirmacion(con):
    """Consultar no cambia nada: responde de una vez con el redactor."""
    e, _ = con_plan(con)
    s, llm = servicio(con, {"accion": "consultar_horario", "fecha": fx(0)})
    r = s.mensaje(e, "¿qué tengo hoy?")
    assert r.tipo == "consulta" and r.texto_horario and llm.llamadas == ["OrdenLLM", "Redaccion"]


def test_fuera_de_alcance_y_aclaracion(con):
    """Un saludo o una orden incompleta se contestan sin guardar nada."""
    e, _ = con_plan(con)
    s, _ = servicio(con, {"accion": "fuera_de_alcance", "motivo": "saludo"}, {"accion": "cambiar_horas", "actividad": "Taller"})
    assert s.mensaje(e, "hola").tipo == "fuera_de_alcance"
    r = s.mensaje(e, "cambia las horas del taller")
    assert r.tipo == "aclaracion" and tomar_orden_pendiente(con, e) is None


def test_sin_plan_o_crear_desde_el_chat_se_rechaza_sin_preguntar(con):
    """Sin plan, una orden se rechaza de una vez; y el plan no se crea desde el chat, sino con el formulario."""
    s, _ = servicio(con, {"accion": "bloquear_dia", "fecha": fx(1)},
                    {"accion": "crear_plan", "actividades": [{"nombre": "Taller", "horas": 3, "fecha_limite": fx(5)}]})
    e = s.nuevo_estudiante()
    assert s.mensaje(e, "el martes no puedo").tipo == "rechazada"
    assert s.mensaje(e, "hazme un plan con el taller").tipo == "rechazada"
    assert tomar_orden_pendiente(con, e) is None


def test_decision_pendiente_se_guarda_y_se_resuelve_abriendo_tiempo(con):
    """Un bloqueo que no cabe deja una decisión; abrir tiempo pregunta cuándo y, al confirmar el tiempo nuevo, se aplica y la decisión desaparece."""
    e, _ = con_plan(con)
    s, _ = servicio(con, BLOQUEO_LARGO, {"accion": "responder_decision", "opcion": "abrir_tiempo"},
                    {"accion": "abrir_tiempo", "fecha": fx(1), "fecha_fin": fx(2), "hora_inicio": "08:00", "hora_fin": "14:00"})
    s.mensaje(e, "no puedo de martes a viernes")
    d = s.confirmar(e, aceptar=True)
    assert d.tipo == "decision" and d.estado.decision.opciones == ["abrir_tiempo", "recortar"] and d.estado.plan == plan_base()
    assert s.mensaje(e, "abro más tiempo").tipo == "pregunta"
    assert s.mensaje(e, "el martes y el miércoles puedo de 8 a 14").tipo == "confirmacion"
    r = s.confirmar(e, aceptar=True)
    assert r.tipo == "aplicada" and r.estado.decision is None


def test_recorte_que_no_alcanza_se_rechaza_antes_de_confirmar(con):
    """Si protegiendo esas actividades no alcanza, se dice de una vez y la decisión sigue abierta."""
    e, _ = con_plan(con)
    s, _ = servicio(con, BLOQUEO_LARGO, {"accion": "responder_decision", "opcion": "recortar", "protegidas": ["Parcial de IA"]})
    s.mensaje(e, "no puedo de martes a viernes")
    s.confirmar(e, aceptar=True)
    r = s.mensaje(e, "recorta, pero no toques el parcial")
    assert r.tipo == "rechazada" and "no alcanza" in r.mensaje and r.estado.decision is not None


# --- Calendario y botón no la hice ----------------------------------------------------------------
def test_mover_desde_el_calendario(con):
    """Mover se aplica de una vez (el front ya confirmó) y la sesión queda fijada; a un día pasado se rechaza."""
    e, s = con_plan(con)
    taller = next(x for x in s.estado(e).horario.sesiones if x.actividad == "Taller" and x.dia > HOY)
    r = s.mover(e, "Taller", taller.dia, HOY, time(19))
    assert r.tipo == "aplicada" and any(x.actividad == "Taller" and x.dia == HOY and x.inicio == time(19) for x in r.estado.fijadas)
    pasado = s.mover(e, "Taller", HOY, HOY - timedelta(days=1), None)
    assert pasado.tipo == "rechazada" and "ya pasó" in pasado.mensaje


def test_mover_un_solo_bloque_desde_el_calendario(con):
    """Con hora de origen se mueve el bloque entero que la incluye (las dos sesiones seguidas del parcial del lunes); en el calendario se une con lo que quede pegado."""
    e, s = con_plan(con)
    r = s.mover(e, "Parcial de IA", HOY, HOY + timedelta(days=5), time(19), hora_origen=time(19))
    assert r.tipo == "aplicada" and [(x.inicio, x.fin) for x in r.estado.fijadas] == [(time(19), time(20)), (time(20), time(21))]
    sabado = [b for b in r.estado.bloques if b.dia == HOY + timedelta(days=5)]
    assert [(b.actividad, b.inicio, b.fin, b.fijado) for b in sabado] == [("Parcial de IA", time(18), time(21), True)]


def test_intercambiar_desde_el_calendario(con):
    """El parcial del jueves y el taller del lunes cambian de lugar; si el taller pasara su fecha límite, se rechaza."""
    e, s = con_plan(con)
    jueves = HOY + timedelta(days=3)
    r = s.intercambiar(e, jueves, time(18), HOY, time(20))
    assert r.tipo == "aplicada" and len(r.estado.fijadas) == 2
    assert any(b.actividad == "Taller" and b.dia == jueves and (b.inicio, b.fin) == (time(18), time(19)) for b in r.estado.bloques)
    assert any(b.actividad == "Parcial de IA" and b.dia == HOY and (b.inicio, b.fin) == (time(18), time(21)) for b in r.estado.bloques)   # se une con el parcial que ya estaba
    tarde = s.intercambiar(e, jueves, time(18), HOY + timedelta(days=5), time(18))
    assert tarde.tipo == "rechazada" and "vence" in tarde.mensaje


def test_no_hecha_devuelve_las_horas(con):
    """La sesión de hoy que no se hizo sale del horario y sus horas se replanifican."""
    e, s = con_plan(con)
    hecha = s.estado(e).horario.sesiones[0]
    noche, _ = servicio(con, ahora=datetime.combine(HOY, time(22)))
    r = noche.no_hecha(e, hecha)
    assert r.tipo == "aplicada" and hecha not in r.estado.horario.sesiones
    assert noche.no_hecha(e, Sesion(actividad="Taller", dia=HOY, inicio=time(6), fin=time(7))).tipo == "rechazada"


def test_el_redactor_cuenta_solo_de_hoy_en_adelante(con):
    """Al día siguiente, al redactor ya no le llegan las sesiones que pasaron."""
    e, _ = con_plan(con)
    manana, llm = servicio(con, {"accion": "consultar_horario"}, ahora=datetime.combine(HOY + timedelta(days=1), time(8)))
    assert manana.mensaje(e, "¿qué tengo?").tipo == "consulta"
    assert llm.llamadas == ["OrdenLLM", "Redaccion"] and "lunes 2 de noviembre" not in llm.contenidos[-1] \
        and "martes 3 de noviembre" in llm.contenidos[-1]
