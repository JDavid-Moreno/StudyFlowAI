"""Ayudas compartidas por las pruebas. Ver los notebooks de cada capa."""
import json
from datetime import date, timedelta

from pydantic import ValidationError

from studyflow.asignacion import EstadoAsignacion, Prediccion
from studyflow.disponibilidad import BLOQUE_MIN, minutos
from studyflow.interprete import Contexto
from studyflow.models import DisponibilidadDia, PlanEntrada


# --- Validación y asignación (INICIO: domingo 25 de octubre de 2026) ----------------------------
INICIO = date(2026, 10, 25)


def rechaza(fn, fragmento):
    try:
        fn()
    except ValidationError as e:
        assert fragmento in str(e), str(e)
        return
    raise AssertionError(f"debía rechazar ({fragmento})")


def franjas(*pares):
    return [{"inicio": i, "fin": f} for i, f in pares]


def dia(fecha, *pares):
    return DisponibilidadDia(dia=fecha, franjas=franjas(*pares))


def f(n, inicio=INICIO):
    return (inicio + timedelta(days=n)).isoformat()


def act(nombre, n_limite, horas, prioridad="media", dificultad="media", inicio=INICIO):
    return {"nombre": nombre, "prioridad": prioridad, "dificultad": dificultad, "fecha_limite": f(n_limite, inicio), "horas_estimadas": horas}


def dias(desde, hasta, pares=(("18:00", "21:00"),), inicio=INICIO):
    return [{"dia": f(n, inicio), "franjas": franjas(*pares)} for n in range(desde, hasta)]


def plan(actividades, disponibilidad, sesion=60, max_horas_dia=None):
    return {"actividades": actividades, "disponibilidad": disponibilidad, "duracion_sesion": sesion, "max_horas_dia": max_horas_dia}


def pr(prioridad, perfil):
    return Prediccion(prioridad=prioridad, perfil=perfil)


def verificar(plan_dict, r, fecha_inicio, ocupadas=()):
    """Reglas que toda asignación debe cumplir (StudyFlow_CapaAsignacion.ipynb, 5.1)."""
    p = PlanEntrada(**plan_dict)
    por_nombre = {a.nombre: a for a in p.actividades}
    franjas_de = {d.dia: d.franjas for d in p.disponibilidad}
    horas = dict.fromkeys(por_nombre, 0.0)
    for s in r.sesiones:
        a = por_nombre[s.actividad]
        ini, fin = minutos(s.inicio), minutos(s.fin)
        assert fecha_inicio <= s.dia < a.fecha_limite, f"fuera de fechas: {s}"
        assert BLOQUE_MIN <= fin - ini <= p.duracion_sesion and (fin - ini) % BLOQUE_MIN == 0, f"duración inválida: {s}"
        for b in range(ini, fin, BLOQUE_MIN):
            assert any(minutos(fr.inicio) <= b and b + BLOQUE_MIN <= minutos(fr.fin) for fr in franjas_de.get(s.dia, [])), f"fuera de disponibilidad: {s}"
        horas[s.actividad] += (fin - ini) / 60
    todas = sorted(list(r.sesiones) + list(ocupadas), key=lambda s: (s.dia, s.inicio))
    for x, y in zip(todas, todas[1:]):
        assert x.dia != y.dia or x.fin <= y.inicio, f"sesiones traslapadas: {x} / {y}"
    if p.max_horas_dia is not None:
        for d in {s.dia for s in todas}:
            total = sum((minutos(s.fin) - minutos(s.inicio)) / 60 for s in todas if s.dia == d)
            assert total <= p.max_horas_dia + 1e-9, f"supera el máximo diario el {d}"
    assert len(r.actividades) == len(p.actividades)
    for a in r.actividades:
        assert abs(horas[a.nombre] - a.horas_asignadas) < 1e-9, f"el resumen no coincide con las sesiones: {a.nombre}"
        assert (a.estado == EstadoAsignacion.ASIGNADA) == (a.horas_faltantes == 0)
        assert (a.estado == EstadoAsignacion.NO_ASIGNADA) == (a.horas_asignadas == 0)
    assert all(x.prioridad >= y.prioridad for x, y in zip(r.actividades, r.actividades[1:]))


# --- Capa del LLM (HOY: lunes 2 de noviembre de 2026) ---------------------------------------------
HOY = date(2026, 11, 2)
ACTIVIDADES = ["Parcial de IA", "Taller de bases de datos", "Lectura"]
CTX = Contexto(hoy=HOY, actividades=ACTIVIDADES)


def sem(d):
    return {"tipo": "dia_semana", "dia_semana": d}


class LLMSimulado:
    """Hace las veces de `llamar_llm`: devuelve una respuesta fija y guarda lo que le enviaron."""

    def __init__(self, respuesta):
        self.respuesta, self.llamadas = respuesta, []

    def __call__(self, sistema, contenido, esquema):
        self.llamadas.append((sistema, contenido, esquema))
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta if isinstance(self.respuesta, str) else json.dumps(self.respuesta, ensure_ascii=False)


# --- API (servicio y rutas) -----------------------------------------------------------------------
class PredictorFijo:
    """Hace las veces del MLP: la primera actividad del plan es la más prioritaria y todas reparten parejo."""

    def predecir(self, plan, fecha_inicio):
        n = len(plan.actividades)
        return [Prediccion(prioridad=1 - i / (n + 1), perfil=[1 / 3] * 3) for i in range(n)]


class LLMGuion:
    """Hace las veces de `llamar_llm` en un flujo: el intérprete recibe las órdenes del guion en orden y el redactor, un texto fijo."""

    def __init__(self, *ordenes, redactor="Tu horario quedó listo."):
        self.ordenes, self.redactor, self.llamadas, self.contenidos = list(ordenes), redactor, [], []

    def __call__(self, sistema, contenido, esquema):
        self.llamadas.append(esquema.__name__)
        self.contenidos.append(contenido)
        if esquema.__name__ == "Redaccion":
            return json.dumps({"mensaje": self.redactor}, ensure_ascii=False)
        return json.dumps(self.ordenes.pop(0), ensure_ascii=False)
