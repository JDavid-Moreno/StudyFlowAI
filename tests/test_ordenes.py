"""Pruebas de la capa de órdenes. Ver StudyFlow_CapaOrdenes.ipynb, pasos 2.1, 3.1, 4.1, 5.1 y 7."""
import random
from collections import Counter
from datetime import date, datetime, time, timedelta

import pytest

from studyflow.asignacion import EstadoAsignacion, ResultadoAsignacion, Sesion, asignar
from studyflow.disponibilidad import BLOQUE_MIN, minutos
from studyflow.interprete import Orden
from studyflow.models import Actividad, DisponibilidadDia, Franja, PlanEntrada
from studyflow.ordenes import (CambioHoras, DESEO, Decision, PlanVigente, REALIDAD, Recorte, ResultadoOrden, _actividad, _asignar, _corte, _cubre,
                               _dias, _dur, _hora, _horas, _libres, _pasada, _preparar, _rangos_texto, _restar, _resumen, _sumar, _texto_decision,
                               _traslapan, aplicar, crear_plan, marcar_no_hecha, proponer_recorte, texto_recorte)
from studyflow.validador import validar

pytest.importorskip("keras")
from studyflow.mlp import PredictorMLP  # noqa: E402


@pytest.fixture(scope="module")
def mlp():
    return PredictorMLP()


HOY = date(2026, 11, 2)                 # lunes
AHORA = datetime(2026, 11, 2, 8, 0)     # antes de la primera franja del día


def fr(*pares) -> list[Franja]:
    """Franjas a partir de pares ("HH:MM", "HH:MM")."""
    return [Franja(inicio=i, fin=f) for i, f in pares]


def semana(inicio: date = HOY, n: int = 14) -> list[DisponibilidadDia]:
    """Entre semana de 18:00 a 21:00, sábado de 9:00 a 12:00 y de 14:00 a 17:00, domingo libre."""
    out = []
    for k in range(n):
        d = inicio + timedelta(days=k)
        pares = [] if d.weekday() == 6 else [("09:00", "12:00"), ("14:00", "17:00")] if d.weekday() == 5 else [("18:00", "21:00")]
        if pares:
            out.append(DisponibilidadDia(dia=d, franjas=fr(*pares)))
    return out


def act(nombre: str, dias: int, horas: float, prioridad: str = "media", dificultad: str = "media") -> Actividad:
    """Actividad que vence `dias` días después de HOY."""
    return Actividad(nombre=nombre, prioridad=prioridad, dificultad=dificultad, fecha_limite=HOY + timedelta(days=dias), horas_estimadas=horas)


def plan_base(**cambios) -> PlanEntrada:
    """El plan de siempre: parcial (lunes 9), taller (jueves 5) y lectura (jueves 12), sesiones de hasta 90 min."""
    p = PlanEntrada(actividades=[act("Parcial de IA", 7, 8, "alta", "alta"), act("Taller de redes", 3, 3), act("Lectura", 10, 2, "baja", "baja")],
                    disponibilidad=semana(), duracion_sesion=90)
    return p.model_copy(update=cambios)


def vigente_base(mlp) -> PlanVigente:
    """El plan de siempre ya creado a las 8:00 del lunes."""
    r = crear_plan(plan_base(), AHORA, mlp)
    assert r.tipo == "aplicada", r.mensaje
    return r.vigente


def de(v: PlanVigente, nombre: str) -> list[Sesion]:
    """Sesiones de una actividad."""
    return [s for s in v.horario.sesiones if s.actividad == nombre]


def revisar(v: PlanVigente, ahora: datetime, completo: bool = True) -> None:
    """Reglas que todo plan aplicado cumple: restricciones en lo futuro, sin traslapes, fijadas en el horario y resumen coherente."""
    p, r = v.plan, v.horario
    por_nombre = {a.nombre: a for a in p.actividades}
    franjas = {d.dia: d.franjas for d in p.disponibilidad}
    futuras = [s for s in r.sesiones if not _pasada(s, ahora)]
    for s in futuras:
        assert s.dia < por_nombre[s.actividad].fecha_limite, f"después de la entrega: {s}"
        assert BLOQUE_MIN <= _dur(s) <= p.duracion_sesion, f"duración inválida: {s}"
        assert _cubre(franjas.get(s.dia, []), minutos(s.inicio), minutos(s.fin)), f"fuera de disponibilidad: {s}"
    for x, y in zip(r.sesiones, r.sesiones[1:]):
        assert (x.dia, x.inicio) <= (y.dia, y.inicio) and not _traslapan(x, y), f"traslape o desorden: {x} / {y}"
    if p.max_horas_dia is not None:
        for d in {s.dia for s in futuras}:
            assert _horas([s for s in r.sesiones if s.dia == d]) <= p.max_horas_dia + 1e-9, f"supera el máximo diario el {d}"
    assert all(s in r.sesiones and not _pasada(s, ahora) for s in v.fijadas), "fijada perdida o pasada"
    assert [x.nombre for x in r.actividades] and {x.nombre for x in r.actividades} == set(por_nombre)
    for x in r.actividades:
        assert abs(x.horas_asignadas - _horas([s for s in r.sesiones if s.actividad == x.nombre])) < 1e-9
        assert (x.estado == EstadoAsignacion.ASIGNADA) == (x.horas_faltantes == 0)
        if completo and por_nombre[x.nombre].fecha_limite > ahora.date():
            assert x.estado == EstadoAsignacion.ASIGNADA, f"quedó incompleta: {x}"
    assert all(a.prioridad >= b.prioridad for a, b in zip(r.actividades, r.actividades[1:]))


# --- Paso 2: utilidades ----------------------------------------------------------------------------
def test_restar_franjas(mlp):
    """Restar un rango corta la franja por un borde, la parte en dos o no la toca."""
    f = fr(("18:00", "21:00"))
    assert _restar(f, minutos(time(19)), minutos(time(20))) == fr(("18:00", "19:00"), ("20:00", "21:00"))
    assert _restar(f, minutos(time(17)), minutos(time(19))) == fr(("19:00", "21:00"))
    assert _restar(f, minutos(time(20)), minutos(time(22))) == fr(("18:00", "20:00"))
    assert _restar(f, minutos(time(10)), minutos(time(12))) == f
    assert _restar(f, minutos(time(18)), minutos(time(21))) == []


def test_sumar_y_cubrir_franjas(mlp):
    """Sumar une lo que se toca o se traslapa; cubrir mira la unión de las franjas."""
    f = fr(("18:00", "20:00"))
    assert _sumar(f, minutos(time(20)), minutos(time(22))) == fr(("18:00", "22:00"))
    assert _sumar(f, minutos(time(8)), minutos(time(9))) == fr(("08:00", "09:00"), ("18:00", "20:00"))
    assert _sumar(f, minutos(time(18, 30)), minutos(time(19))) == f
    junto = fr(("18:00", "19:00"), ("19:00", "20:00"))
    assert _cubre(junto, minutos(time(18, 30)), minutos(time(19, 30))) and not _cubre(junto, minutos(time(19, 30)), minutos(time(20, 30)))


def test_libres_y_textos(mlp):
    """Lo libre de un día descuenta lo ocupado y lo anterior a la hora actual."""
    ocupada = Sesion(actividad="X", dia=HOY, inicio=time(19), fin=time(19, 30))
    assert _libres(fr(("18:00", "21:00")), [ocupada], piso=minutos(time(18, 30))) == fr(("18:30", "19:00"), ("19:30", "21:00"))
    assert _rangos_texto(fr(("18:30", "19:00"), ("19:30", "21:00"))) == "18:30 a 19:00 y 19:30 a 21:00"
    assert _dias(HOY, HOY + timedelta(days=2)) == [HOY, HOY + timedelta(days=1), HOY + timedelta(days=2)] and _dias(HOY, None) == [HOY]


def test_pasada_y_corte(mlp):
    """Una sesión que ya empezó es pasada; hoy se planifica desde el siguiente bloque de 30 min."""
    s = Sesion(actividad="X", dia=HOY, inicio=time(18), fin=time(19))
    assert not _pasada(s, datetime(2026, 11, 2, 18, 0)) and _pasada(s, datetime(2026, 11, 2, 18, 1))
    assert _corte(datetime(2026, 11, 2, 18, 0)) == 18 * 60 and _corte(datetime(2026, 11, 2, 18, 1)) == 18 * 60 + 30
    assert _corte(datetime(2026, 11, 2, 18, 30, 5)) == 19 * 60 and _corte(datetime(2026, 11, 2, 23, 50)) == 24 * 60


def test_resumen(mlp):
    """El resumen sale de las sesiones; lo que falta por menos de medio bloque no cuenta como faltante."""
    p = plan_base()
    ss = [Sesion(actividad="Taller de redes", dia=HOY, inicio=time(18), fin=time(19, 30)),
          Sesion(actividad="Lectura", dia=HOY, inicio=time(19, 30), fin=time(21))]
    r = _resumen(p, ss, {"Lectura": 0.2, "Taller de redes": 0.6})
    est = {x.nombre: (x.horas_asignadas, x.horas_faltantes, x.estado.value) for x in r.actividades}
    assert est == {"Taller de redes": (1.5, 1.5, "parcial"), "Lectura": (1.5, 0.5, "parcial"), "Parcial de IA": (0.0, 8.0, "no_asignada")}
    assert [x.nombre for x in r.actividades] == ["Taller de redes", "Lectura", "Parcial de IA"]
    casi = _resumen(p.model_copy(update={"actividades": [act("Lectura", 10, 1.7)]}), ss[1:], {})
    assert casi.actividades[0].estado == EstadoAsignacion.ASIGNADA          # 1 h 42 min pedidas, 1 h 30 min puestas: falta menos de un bloque


# --- Paso 3: replanificación común ----------------------------------------------------------------
def test_crear_plan_factible(mlp):
    """El plan de siempre cabe: todo queda asignado antes de su entrega y dentro de la disponibilidad."""
    v = vigente_base(mlp)
    revisar(v, AHORA)
    assert v.fijadas == [] and len(v.horario.sesiones) > 0


def test_crear_plan_que_no_cabe_abre_decision(mlp):
    """Si lo pedido no cabe, no se crea nada: se abre una decisión con las tres opciones."""
    p = plan_base(actividades=[act("Parcial de IA", 7, 40, "alta", "alta"), act("Lectura", 10, 2)])
    r = crear_plan(p, AHORA, mlp)
    assert r.tipo == "decision" and r.vigente is None and r.decision.opciones == DESEO
    assert r.decision.faltan_horas > 0 and r.decision.fecha_critica == HOY + timedelta(days=7)
    assert "faltan" in r.mensaje and "cancelar el cambio" in r.mensaje


def test_crear_plan_sin_ventana_minima(mlp):
    """Una entrega con menos de 3 días con disponibilidad se rechaza: abrir tiempo o recortar no lo arreglan."""
    r = crear_plan(plan_base(actividades=[act("Quiz", 2, 1)]), AHORA, mlp)
    assert r.tipo == "rechazada" and "necesita al menos 3 días" in r.mensaje


def test_crear_plan_con_nombres_repetidos(mlp):
    """Dos actividades con el mismo nombre no se aceptan."""
    r = crear_plan(plan_base(actividades=[act("Lectura", 5, 1), act("lectura", 8, 1)]), AHORA, mlp)
    assert r.tipo == "rechazada" and "mismo nombre" in r.mensaje


def test_hoy_se_planifica_desde_ahora(mlp):
    """Si se crea el plan a las 19:10, hoy no queda ninguna sesión antes de las 19:30."""
    r = crear_plan(plan_base(), datetime(2026, 11, 2, 19, 10), mlp)
    assert r.tipo == "aplicada"
    assert all(s.inicio >= time(19, 30) for s in r.horario.sesiones if s.dia == HOY)
    revisar(r.vigente, datetime(2026, 11, 2, 19, 10))


# --- Paso 4: cada orden ----------------------------------------------------------------------------
def test_historial_no_se_toca(mlp):
    """Al replanificar días después, lo que ya empezó queda igual y no aparece nada nuevo en el pasado."""
    v = vigente_base(mlp)
    luego = datetime(2026, 11, 4, 12, 0)                        # miércoles al mediodía
    antes = [s for s in v.horario.sesiones if _pasada(s, luego)]
    r = aplicar(v, Orden(accion="cambiar_nivel", actividad="Lectura", prioridad="alta"), luego, mlp)
    assert r.tipo == "aplicada"
    assert [s for s in r.horario.sesiones if _pasada(s, luego)] == antes and antes
    revisar(r.vigente, luego)


def test_cambiar_horas(mlp):
    """Subir y bajar el total de una actividad cambia sus horas asignadas."""
    v = vigente_base(mlp)
    sube = aplicar(v, Orden(accion="cambiar_horas", actividad="Lectura", horas=4), AHORA, mlp)
    baja = aplicar(v, Orden(accion="cambiar_horas", actividad="Lectura", horas=1), AHORA, mlp)
    assert _horas(de(sube.vigente, "Lectura")) == 4 and _horas(de(baja.vigente, "Lectura")) == 1
    revisar(sube.vigente, AHORA)
    revisar(baja.vigente, AHORA)


def test_cambiar_horas_por_debajo_de_lo_hecho(mlp):
    """El nuevo total no puede ser menor que lo ya estudiado o fijado."""
    v = vigente_base(mlp)
    luego = datetime(2026, 11, 5, 12, 0)
    hecho = _horas([s for s in de(v, "Taller de redes") if _pasada(s, luego)])
    assert hecho > 0
    r = aplicar(v, Orden(accion="cambiar_horas", actividad="Taller de redes", horas=hecho - 0.5), luego, mlp)
    assert r.tipo == "rechazada" and "ya tiene" in r.mensaje


def test_cambiar_fecha_limite(mlp):
    """Adelantar la entrega deja todas las sesiones antes de la nueva fecha y libera la fijada que queda después."""
    v = vigente_base(mlp)
    sabado = date(2026, 11, 7)
    fijada = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=de(v, "Parcial de IA")[0].dia,
                              dia_destino=sabado, hora_inicio=time(9)), AHORA, mlp).vigente
    assert any(s.dia == sabado for s in fijada.fijadas)
    r = aplicar(fijada, Orden(accion="cambiar_fecha_limite", actividad="Parcial de IA", dia=sabado), AHORA, mlp)
    assert r.tipo in ("aplicada", "decision")
    if r.tipo == "aplicada":
        assert all(s.dia < sabado for s in de(r.vigente, "Parcial de IA")) and not r.vigente.fijadas
        revisar(r.vigente, AHORA)
    assert any("se liberó" in a for a in r.avisos)


def test_cambiar_nivel(mlp):
    """Subir la prioridad y la dificultad de la lectura sube la prioridad que le da el MLP."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="cambiar_nivel", actividad="Lectura", prioridad="alta", dificultad="alta"), AHORA, mlp)
    antes = {x.nombre: x.prioridad for x in v.horario.actividades}["Lectura"]
    despues = {x.nombre: x.prioridad for x in r.horario.actividades}["Lectura"]
    assert despues > antes and _actividad(r.vigente.plan, "Lectura").prioridad.value == "alta"
    revisar(r.vigente, AHORA)


def test_agregar_actividad(mlp):
    """Una actividad nueva queda asignada; un nombre repetido o una entrega sin ventana se rechazan."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="agregar_actividad", actividades=[act("Informe", 8, 3)]), AHORA, mlp)
    assert r.tipo == "aplicada" and _horas(de(r.vigente, "Informe")) == 3
    revisar(r.vigente, AHORA)
    assert aplicar(v, Orden(accion="agregar_actividad", actividades=[act("lectura", 8, 3)]), AHORA, mlp).tipo == "rechazada"
    sin_ventana = aplicar(v, Orden(accion="agregar_actividad", actividades=[act("Quiz", 2, 1)]), AHORA, mlp)
    assert sin_ventana.tipo == "rechazada" and "Quiz necesita" in sin_ventana.mensaje


def test_quitar_actividad(mlp):
    """Quitar una actividad borra sus sesiones y deja las demás exactamente donde estaban (4A)."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="quitar_actividad", actividad="Lectura"), AHORA, mlp)
    assert r.tipo == "aplicada" and de(r.vigente, "Lectura") == []
    assert r.horario.sesiones == [s for s in v.horario.sesiones if s.actividad != "Lectura"]
    revisar(r.vigente, AHORA)
    solo = crear_plan(plan_base(actividades=[act("Lectura", 10, 2)]), AHORA, mlp).vigente
    assert aplicar(solo, Orden(accion="quitar_actividad", actividad="Lectura"), AHORA, mlp).tipo == "rechazada"


def test_bloquear_dia_completo_y_rango(mlp):
    """Un bloqueo de día completo vacía el día; uno por horas solo esa parte."""
    v = vigente_base(mlp)
    martes, miercoles = date(2026, 11, 3), date(2026, 11, 4)
    r = aplicar(v, Orden(accion="bloquear_dia", dia=martes), AHORA, mlp)
    assert r.tipo == "aplicada" and all(s.dia != martes for s in r.horario.sesiones)
    revisar(r.vigente, AHORA)
    r = aplicar(v, Orden(accion="bloquear_dia", dia=miercoles, hora_inicio=time(18), hora_fin=time(19, 30)), AHORA, mlp)
    assert r.tipo == "aplicada" and all(s.inicio >= time(19, 30) for s in r.horario.sesiones if s.dia == miercoles)
    assert next(d for d in r.vigente.plan.disponibilidad if d.dia == miercoles).franjas == fr(("19:30", "21:00"))
    revisar(r.vigente, AHORA)


def test_bloquear_varios_dias(mlp):
    """Un rango bloquea todos sus días; si nada cabe, la decisión no ofrece cancelar."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="bloquear_dia", dia=date(2026, 11, 3), dia_fin=date(2026, 11, 6)), AHORA, mlp)
    assert r.tipo == "decision" and r.decision.opciones == REALIDAD
    assert all(d.dia not in _dias(date(2026, 11, 3), date(2026, 11, 6)) for d in r.decision.propuesto.plan.disponibilidad)
    r = aplicar(v, Orden(accion="bloquear_dia", dia=date(2026, 11, 10), dia_fin=date(2026, 11, 11)), AHORA, mlp)
    assert r.tipo == "aplicada" and all(s.dia not in (date(2026, 11, 10), date(2026, 11, 11)) for s in r.horario.sesiones)


def test_bloquear_sin_disponibilidad(mlp):
    """Bloquear un domingo (sin tiempo de estudio) no cambia nada."""
    r = aplicar(vigente_base(mlp), Orden(accion="bloquear_dia", dia=date(2026, 11, 8)), AHORA, mlp)
    assert r.tipo == "rechazada" and "nada que bloquear" in r.mensaje


def test_abrir_tiempo(mlp):
    """Abrir el domingo agrega esa franja; abrir lo que ya había no cambia nada."""
    v = vigente_base(mlp)
    domingo = date(2026, 11, 8)
    r = aplicar(v, Orden(accion="abrir_tiempo", dia=domingo, hora_inicio=time(9), hora_fin=time(13)), AHORA, mlp)
    assert r.tipo == "aplicada" and next(d for d in r.vigente.plan.disponibilidad if d.dia == domingo).franjas == fr(("09:00", "13:00"))
    revisar(r.vigente, AHORA)
    r = aplicar(v, Orden(accion="abrir_tiempo", dia=date(2026, 11, 3), hora_inicio=time(20), hora_fin=time(23)), AHORA, mlp)
    assert next(d for d in r.vigente.plan.disponibilidad if d.dia == date(2026, 11, 3)).franjas == fr(("18:00", "23:00"))
    repetido = aplicar(v, Orden(accion="abrir_tiempo", dia=date(2026, 11, 3), hora_inicio=time(18), hora_fin=time(20)), AHORA, mlp)
    assert repetido.tipo == "rechazada"


def test_cambiar_limites(mlp):
    """Bajar el máximo diario se respeta; un máximo menor que la sesión se rechaza; también se puede quitar."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="cambiar_limites", max_horas_dia=2.5), AHORA, mlp)
    assert r.tipo == "aplicada" and r.vigente.plan.max_horas_dia == 2.5
    revisar(r.vigente, AHORA)
    malo = aplicar(v, Orden(accion="cambiar_limites", max_horas_dia=1), AHORA, mlp)
    assert malo.tipo == "rechazada" and "máximo diario" in malo.mensaje
    corto = aplicar(v, Orden(accion="cambiar_limites", duracion_sesion=60), AHORA, mlp)
    assert corto.tipo == "aplicada" and all(_dur(s) <= 60 for s in corto.horario.sesiones)
    sin = aplicar(r.vigente, Orden(accion="cambiar_limites", sin_maximo_diario=True), AHORA, mlp)
    assert sin.tipo == "aplicada" and sin.vigente.plan.max_horas_dia is None
    assert aplicar(v, Orden(accion="cambiar_limites", duracion_sesion=90), AHORA, mlp).tipo == "rechazada"


def test_mover_sesion_con_hora(mlp):
    """La sesión queda fija en el destino a la hora pedida y el resto se replanifica alrededor."""
    v = vigente_base(mlp)
    origen = de(v, "Parcial de IA")[0]
    r = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=origen.dia, dia_destino=date(2026, 11, 7),
                         hora_inicio=time(9)), AHORA, mlp)
    assert r.tipo == "aplicada"
    movida = [s for s in r.vigente.fijadas if s.actividad == "Parcial de IA"]
    assert movida and movida[0].dia == date(2026, 11, 7) and movida[0].inicio == time(9)
    assert _horas(de(r.vigente, "Parcial de IA")) == 8
    revisar(r.vigente, AHORA)


def test_mover_sesion_sin_hora(mlp):
    """Sin hora, la sesión va al primer hueco del destino donde quepa entera."""
    v = vigente_base(mlp)
    origen = de(v, "Lectura")[0]
    r = aplicar(v, Orden(accion="mover_sesion", actividad="Lectura", dia_origen=origen.dia, dia_destino=date(2026, 11, 7)), AHORA, mlp)
    assert r.tipo == "aplicada" and r.vigente.fijadas[0].inicio == time(9) and r.vigente.fijadas[0].dia == date(2026, 11, 7)
    revisar(r.vigente, AHORA)


def test_mover_a_un_lugar_ocupado_por_otra_actividad(mlp):
    """Si el destino lo ocupaba otra actividad, esa se desplaza al replanificar."""
    v = vigente_base(mlp)
    martes = date(2026, 11, 3)
    otra = next(s for s in v.horario.sesiones if s.dia == martes)
    origen = next(s for s in v.horario.sesiones if s.actividad != otra.actividad and s.dia != martes and s.dia < date(2026, 11, 5))
    r = aplicar(v, Orden(accion="mover_sesion", actividad=origen.actividad, dia_origen=origen.dia, dia_destino=martes,
                         hora_inicio=otra.inicio), AHORA, mlp)
    assert r.tipo == "aplicada" and any(s.dia == martes and s.inicio == otra.inicio and s.actividad == origen.actividad for s in r.horario.sesiones)
    revisar(r.vigente, AHORA)


def test_mover_sesion_que_no_cabe(mlp):
    """Fuera de la disponibilidad, después de la entrega o sin sesiones en el origen: no cambia nada y se explica."""
    v = vigente_base(mlp)
    origen = de(v, "Taller de redes")[0]
    fuera = aplicar(v, Orden(accion="mover_sesion", actividad="Taller de redes", dia_origen=origen.dia, dia_destino=date(2026, 11, 4),
                             hora_inicio=time(20, 30)), AHORA, mlp)
    assert fuera.tipo == "rechazada" and "tienes libre" in fuera.mensaje
    tarde = aplicar(v, Orden(accion="mover_sesion", actividad="Taller de redes", dia_origen=origen.dia, dia_destino=date(2026, 11, 7)), AHORA, mlp)
    assert tarde.tipo == "rechazada" and "la entrega es el jueves 5 de noviembre" in tarde.mensaje
    parcial = de(v, "Parcial de IA")[0]
    domingo = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=parcial.dia, dia_destino=date(2026, 11, 8)), AHORA, mlp)
    assert domingo.tipo == "rechazada" and "no tienes tiempo de estudio" in domingo.mensaje
    vacio = aplicar(v, Orden(accion="mover_sesion", dia_origen=date(2026, 11, 8), dia_destino=date(2026, 11, 7)), AHORA, mlp)
    assert vacio.tipo == "rechazada" and "No tienes sesiones" in vacio.mensaje


def test_mover_a_un_dia_pasado_o_al_mismo_dia_sin_hora(mlp):
    """Un destino que ya pasó, o el mismo día sin hora, se rechaza sin cambiar nada (así puede llegar una orden desde el calendario)."""
    v = vigente_base(mlp)
    origen = de(v, "Parcial de IA")[0]
    ayer = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=origen.dia,
                            dia_destino=AHORA.date() - timedelta(days=1)), AHORA, mlp)
    assert ayer.tipo == "rechazada" and "ya pasó" in ayer.mensaje
    mismo = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=origen.dia, dia_destino=origen.dia), AHORA, mlp)
    assert mismo.tipo == "rechazada" and "el mismo" in mismo.mensaje
    otra_hora = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=origen.dia, dia_destino=origen.dia,
                                 hora_inicio=time(19)), AHORA, mlp)
    assert otra_hora.tipo == "aplicada" and otra_hora.vigente.fijadas[0].inicio == time(19)
    revisar(otra_hora.vigente, AHORA)


def test_mover_un_solo_bloque(mlp):
    """Con hora de origen se mueve solo el bloque que la incluye: las sesiones seguidas de la misma actividad van juntas y lo demás del día se queda."""
    v = vigente_base(mlp)
    lunes = date(2026, 11, 2)                # parcial de 18:00 a 20:00 (dos sesiones seguidas) y taller de 20:00 a 21:00
    taller = aplicar(v, Orden(accion="mover_sesion", dia_origen=lunes, hora_origen=time(20), dia_destino=date(2026, 11, 4),
                              hora_inicio=time(18)), AHORA, mlp)
    assert taller.tipo == "aplicada" and [(s.actividad, s.inicio) for s in taller.vigente.fijadas] == [("Taller de redes", time(18))]
    revisar(taller.vigente, AHORA)
    parcial = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=lunes, hora_origen=time(19, 30),
                               dia_destino=date(2026, 11, 7), hora_inicio=time(14)), AHORA, mlp)
    assert parcial.tipo == "aplicada"
    assert [(s.inicio, s.fin) for s in parcial.vigente.fijadas] == [(time(14), time(15, 30)), (time(15, 30), time(16))]
    revisar(parcial.vigente, AHORA)
    nada = aplicar(v, Orden(accion="mover_sesion", actividad="Lectura", dia_origen=lunes, hora_origen=time(18),
                            dia_destino=date(2026, 11, 7)), AHORA, mlp)
    assert nada.tipo == "rechazada" and "a las 18:00" in nada.mensaje


def test_intercambiar_dos_bloques(mlp):
    """Cada bloque empieza donde empezaba el otro, con su misma duración; los dos quedan fijados y nada más se mueve."""
    v = vigente_base(mlp)
    martes, miercoles = date(2026, 11, 3), date(2026, 11, 4)
    a = [s for s in v.horario.sesiones if s.dia == martes and s.actividad == "Parcial de IA"]          # 18:00-19:30
    b = [s for s in v.horario.sesiones if s.dia == miercoles and s.actividad == "Taller de redes"]     # 19:30-20:30
    r = aplicar(v, Orden(accion="intercambiar_sesiones", dia_origen=martes, hora_origen=time(18), dia_destino=miercoles,
                         hora_inicio=time(19, 30)), AHORA, mlp)
    assert r.tipo == "aplicada" and "Parcial de IA pasa al miércoles 4 de noviembre a las 19:30" in r.mensaje
    assert sorted((s.actividad, s.dia, s.inicio, s.fin) for s in r.vigente.fijadas) == [
        ("Parcial de IA", miercoles, time(19, 30), time(21)), ("Taller de redes", martes, time(18), time(19))]
    assert [s for s in r.horario.sesiones if s not in r.vigente.fijadas] == [s for s in v.horario.sesiones if s not in a + b]
    assert r.horario.actividades == v.horario.actividades
    revisar(r.vigente, AHORA)


def test_intercambio_que_no_se_puede(mlp):
    """Choca con otra sesión, se sale del tiempo de estudio, pasa la fecha límite, es la misma actividad, pasa el máximo diario o hay una decisión abierta."""
    v = vigente_base(mlp)
    martes = date(2026, 11, 3)       # parcial 18:00-19:30, taller 19:30-20:30, lectura 20:30-21:00
    swap = lambda base, d1, h1, d2, h2, decision=None: aplicar(base, Orden(accion="intercambiar_sesiones", dia_origen=d1, hora_origen=h1,
                                                                            dia_destino=d2, hora_inicio=h2), AHORA, mlp, decision)
    choque = swap(v, martes, time(18), martes, time(19, 30))
    assert choque.tipo == "rechazada" and "chocaría con Lectura de las 20:30" in choque.mensaje
    fuera = swap(v, date(2026, 11, 5), time(18), martes, time(20, 30))
    assert fuera.tipo == "rechazada" and "se sale de tu tiempo de estudio" in fuera.mensaje
    tarde = swap(v, date(2026, 11, 2), time(20), date(2026, 11, 7), time(9))
    assert tarde.tipo == "rechazada" and "Taller de redes vence el jueves 5 de noviembre" in tarde.mensaje
    misma = swap(v, date(2026, 11, 2), time(18), martes, time(18))
    assert misma.tipo == "rechazada" and "son de Parcial de IA" in misma.mensaje
    vacio = swap(v, date(2026, 11, 8), time(10), martes, time(18))
    assert vacio.tipo == "rechazada" and "Elige dos sesiones" in vacio.mensaje
    tope = v.model_copy(update={"plan": v.plan.model_copy(update={"max_horas_dia": 2.5})})
    maximo = swap(tope, date(2026, 11, 2), time(18), date(2026, 11, 6), time(19))
    assert maximo.tipo == "rechazada" and "pasaría tu máximo" in maximo.mensaje
    abierta = Decision(propuesto=v, opciones=["abrir_tiempo", "recortar"], faltan_horas=1, fecha_critica=date(2026, 11, 9))
    assert "Primero resuelve" in swap(v, martes, time(20, 30), date(2026, 11, 2), time(20), abierta).mensaje


def test_la_fijada_sobrevive_a_otras_ordenes(mlp):
    """Una sesión movida sigue en su lugar después de otras órdenes que replanifican."""
    v = vigente_base(mlp)
    origen = de(v, "Parcial de IA")[0]
    v = aplicar(v, Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=origen.dia, dia_destino=date(2026, 11, 7),
                         hora_inicio=time(14)), AHORA, mlp).vigente
    fijas = list(v.fijadas)
    for o in [Orden(accion="cambiar_nivel", actividad="Lectura", prioridad="alta"), Orden(accion="cambiar_horas", actividad="Taller de redes", horas=4),
              Orden(accion="agregar_actividad", actividades=[act("Informe", 9, 2)])]:
        r = aplicar(v, o, AHORA, mlp)
        assert r.tipo == "aplicada" and all(s in r.horario.sesiones for s in fijas), o.accion
        v = r.vigente
        revisar(v, AHORA)


def test_consultar_y_sin_plan(mlp):
    """Consultar filtra el horario sin cambiarlo; sin plan, toda orden pide crearlo primero."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="consultar_horario", dia=date(2026, 11, 3)), AHORA, mlp)
    assert r.tipo == "consulta" and r.vigente is None and all(s.dia == date(2026, 11, 3) for s in r.horario.sesiones)
    r = aplicar(v, Orden(accion="consultar_horario", actividad="Lectura"), AHORA, mlp)
    assert {s.actividad for s in r.horario.sesiones} == {"Lectura"}
    assert aplicar(None, Orden(accion="cambiar_horas", actividad="Lectura", horas=3), AHORA, mlp).tipo == "rechazada"


def test_marcar_no_hecha(mlp):
    """Una sesión pasada que no se hizo devuelve sus horas a lo pendiente y se vuelve a ubicar en el futuro."""
    v = vigente_base(mlp)
    luego = datetime(2026, 11, 4, 12, 0)
    pasada = next(s for s in v.horario.sesiones if _pasada(s, luego))
    r = marcar_no_hecha(v, pasada, luego, mlp)
    assert r.tipo == "aplicada" and pasada not in r.horario.sesiones
    assert _horas(de(r.vigente, pasada.actividad)) == _horas(de(v, pasada.actividad))
    assert any("volvieron a lo pendiente" in a for a in r.avisos)
    revisar(r.vigente, luego)
    futura = next(s for s in v.horario.sesiones if not _pasada(s, luego))
    assert marcar_no_hecha(v, futura, luego, mlp).tipo == "rechazada"


# --- Paso 5: decisiones ----------------------------------------------------------------------------
BLOQUEO_SEMANA = Orden(accion="bloquear_dia", dia=date(2026, 11, 3), dia_fin=date(2026, 11, 6))   # de martes a viernes


def decision_bloqueo(mlp) -> tuple[PlanVigente, Decision]:
    """El plan de siempre con la semana bloqueada: el parcial ya no cabe."""
    v = vigente_base(mlp)
    r = aplicar(v, BLOQUEO_SEMANA, AHORA, mlp)
    assert r.tipo == "decision"
    return v, r.decision


def test_decision_del_bloqueo(mlp):
    """La decisión guarda el plan con el bloqueo, el horario vigente intacto, lo que falta y la fecha crítica."""
    v, d = decision_bloqueo(mlp)
    assert d.propuesto.horario == v.horario and d.opciones == REALIDAD
    assert d.faltan_horas > 0 and d.fecha_critica <= date(2026, 11, 9)
    assert "cancelar" not in _texto_decision(d) and "recortar horas" in _texto_decision(d)


def test_recortar_protegiendo_el_parcial(mlp):
    """Con el parcial protegido, se recorta lo demás que vence antes de la fecha crítica y el plan se aplica."""
    v, d = decision_bloqueo(mlp)
    rec = proponer_recorte(d, ["Parcial de IA"], AHORA, mlp)
    assert rec.alcanza and rec.cambios and all(c.actividad != "Parcial de IA" and c.despues < c.antes for c in rec.cambios)
    r = aplicar(v, Orden(accion="responder_decision", opcion="recortar", protegidas=["Parcial de IA"]), AHORA, mlp, d)
    assert r.tipo == "aplicada" and r.avisos[0].startswith("Recorté: ")
    assert _actividad(r.vigente.plan, "Parcial de IA").horas_estimadas == 8 and _horas(de(r.vigente, "Parcial de IA")) == 8
    assert all(s.dia not in _dias(date(2026, 11, 3), date(2026, 11, 6)) for s in r.horario.sesiones)
    revisar(r.vigente, AHORA)


def test_recortar_sin_proteger(mlp):
    """Sin protegidas, el recorte empieza por la actividad de menor prioridad del MLP."""
    v, d = decision_bloqueo(mlp)
    rec = proponer_recorte(d, [], AHORA, mlp)
    assert rec.alcanza
    r = aplicar(v, Orden(accion="responder_decision", opcion="recortar"), AHORA, mlp, d)
    assert r.tipo == "aplicada"
    revisar(r.vigente, AHORA)


def test_recortar_no_alcanza(mlp):
    """Si todo lo que vence antes está protegido, no hay de dónde recortar y la decisión sigue pendiente."""
    v, d = decision_bloqueo(mlp)
    r = aplicar(v, Orden(accion="responder_decision", opcion="recortar", protegidas=["Parcial de IA", "Taller de redes"]), AHORA, mlp, d)
    assert r.tipo == "rechazada" and r.decision == d and "no alcanza" in r.mensaje


def test_abrir_tiempo_resuelve_la_decision(mlp):
    """Elegir abrir tiempo pregunta cuánto; la orden siguiente se suma al bloqueo y, si alcanza, se aplica todo junto."""
    v, d = decision_bloqueo(mlp)
    r = aplicar(v, Orden(accion="responder_decision", opcion="abrir_tiempo"), AHORA, mlp, d)
    assert r.tipo == "pregunta" and r.decision == d and "Dime qué días" in r.mensaje
    poco = aplicar(v, Orden(accion="abrir_tiempo", dia=date(2026, 11, 8), hora_inicio=time(9), hora_fin=time(9, 30)), AHORA, mlp, d)
    assert poco.tipo == "decision" and poco.decision.faltan_horas < d.faltan_horas                 # sigue faltando, pero menos
    r = aplicar(v, Orden(accion="abrir_tiempo", dia=date(2026, 11, 8), hora_inicio=time(9), hora_fin=time(17)), AHORA, mlp, poco.decision)
    assert r.tipo == "aplicada" and any(s.dia == date(2026, 11, 8) for s in r.horario.sesiones)
    assert all(s.dia not in _dias(date(2026, 11, 3), date(2026, 11, 6)) for s in r.horario.sesiones)   # el bloqueo sigue
    revisar(r.vigente, AHORA)


def test_cancelar_solo_un_cambio_pedido(mlp):
    """Un bloqueo no se cancela; un cambio de horas que no cabe sí, y el horario queda como estaba."""
    v, d = decision_bloqueo(mlp)
    assert aplicar(v, Orden(accion="responder_decision", opcion="cancelar"), AHORA, mlp, d).tipo == "rechazada"
    r = aplicar(v, Orden(accion="cambiar_horas", actividad="Parcial de IA", horas=40), AHORA, mlp)
    assert r.tipo == "decision" and r.decision.opciones == DESEO
    c = aplicar(v, Orden(accion="responder_decision", opcion="cancelar"), AHORA, mlp, r.decision)
    assert c.tipo == "cancelada" and c.vigente is None and "sigue como estaba" in c.mensaje


def test_bloqueo_sobre_una_decision_quita_cancelar(mlp):
    """Si llega un bloqueo mientras se decide un cambio pedido, ya no se puede cancelar."""
    v = vigente_base(mlp)
    r = aplicar(v, Orden(accion="cambiar_horas", actividad="Parcial de IA", horas=20), AHORA, mlp)
    assert r.tipo == "decision" and "cancelar" in r.decision.opciones
    r2 = aplicar(v, Orden(accion="bloquear_dia", dia=date(2026, 11, 10)), AHORA, mlp, r.decision)
    assert r2.tipo == "decision" and r2.decision.opciones == REALIDAD


def test_sin_decision_y_rechazo_que_la_conserva(mlp):
    """Responder sin decisión pendiente no hace nada; un rechazo mientras hay una decisión la mantiene."""
    v, d = decision_bloqueo(mlp)
    assert aplicar(v, Orden(accion="responder_decision", opcion="recortar"), AHORA, mlp).tipo == "rechazada"
    r = aplicar(v, Orden(accion="cambiar_horas", actividad="Cálculo", horas=3), AHORA, mlp, d)
    assert r.tipo == "rechazada" and r.decision == d


def test_texto_recorte(mlp):
    """El recorte se describe con las horas antes y después."""
    rec = Recorte(cambios=[CambioHoras(actividad="Lectura", antes=2, despues=0.5), CambioHoras(actividad="Taller", antes=3, despues=0)], alcanza=True)
    assert texto_recorte(rec) == "Lectura pasa de 2 h a 30 min; Taller se quita (3 h)."


NIV = ["baja", "media", "alta"]
OPCIONES_FRANJAS = [[("18:00", "21:00")], [("07:00", "09:30"), ("19:00", "22:00")], [("14:00", "18:30")], [("09:00", "12:00"), ("14:00", "17:00")], []]


def plan_aleatorio(rng: random.Random) -> PlanEntrada:
    """Plan al azar desde HOY: 1 a 5 actividades, 3 semanas de franjas variadas, sesión y tope al azar."""
    horizonte = 21
    disp = [DisponibilidadDia(dia=HOY + timedelta(days=k), franjas=fr(*rng.choice(OPCIONES_FRANJAS))) for k in range(horizonte)]
    acts = [act(f"Actividad {i}", rng.randint(4, horizonte - 1), rng.choice([1, 2, 3.5, 5, 8]), rng.choice(NIV), rng.choice(NIV))
            for i in range(rng.randint(1, 5))]
    return PlanEntrada(actividades=acts, disponibilidad=[d for d in disp if d.franjas] or disp[:1], duracion_sesion=rng.choice([60, 90, 120]),
                       max_horas_dia=rng.choice([None, None, 3, 4]))


def orden_aleatoria(rng: random.Random, v: PlanVigente, ahora: datetime, n: int) -> Orden:
    """Una orden al azar que tendría sentido para ese plan (las fechas, como las resolvería el intérprete: desde hoy)."""
    hoy = ahora.date()
    nombres = [a.nombre for a in v.plan.actividades]
    futuras = [s for s in v.horario.sesiones if not _pasada(s, ahora)]
    dia = lambda: hoy + timedelta(days=rng.randint(0, 12))
    hora = lambda: time(rng.choice([7, 9, 14, 18, 19, 20]), rng.choice([0, 30]))
    tipo = rng.choice(["horas", "fecha", "nivel", "agregar", "quitar", "bloquear", "bloquear_horas", "abrir", "limites", "mover", "mover"])
    if tipo == "horas":
        return Orden(accion="cambiar_horas", actividad=rng.choice(nombres), horas=rng.choice([1, 2.5, 4, 6, 10]))
    if tipo == "fecha":
        return Orden(accion="cambiar_fecha_limite", actividad=rng.choice(nombres), dia=hoy + timedelta(days=rng.randint(1, 18)))
    if tipo == "nivel":
        return Orden(accion="cambiar_nivel", actividad=rng.choice(nombres), prioridad=rng.choice(NIV), dificultad=rng.choice(NIV + [None]))
    if tipo == "agregar":
        return Orden(accion="agregar_actividad", actividades=[Actividad(nombre=f"Nueva {n}", prioridad=rng.choice(NIV), dificultad=rng.choice(NIV),
                                                                        fecha_limite=hoy + timedelta(days=rng.randint(3, 15)), horas_estimadas=rng.choice([1, 2, 4]))])
    if tipo == "quitar":
        return Orden(accion="quitar_actividad", actividad=rng.choice(nombres))
    if tipo == "bloquear":
        d = dia()
        return Orden(accion="bloquear_dia", dia=d, dia_fin=d + timedelta(days=rng.choice([0, 0, 1, 3])))
    if tipo == "bloquear_horas":
        h = hora()
        return Orden(accion="bloquear_dia", dia=dia(), hora_inicio=h, hora_fin=_hora(min(minutos(h) + rng.choice([60, 90, 150]), 23 * 60)))
    if tipo == "abrir":
        h = hora()
        d = dia()
        return Orden(accion="abrir_tiempo", dia=d, dia_fin=d + timedelta(days=rng.choice([0, 2])), hora_inicio=h, hora_fin=_hora(min(minutos(h) + rng.choice([60, 120, 180]), 23 * 60)))
    if tipo == "limites":
        return Orden(accion="cambiar_limites", **rng.choice([{"max_horas_dia": rng.choice([2, 3, 5])}, {"sin_maximo_diario": True}, {"duracion_sesion": rng.choice([30, 60, 120])}]))
    s = rng.choice(futuras) if futuras else None
    origen = s.dia if s else dia()
    return Orden(accion="mover_sesion", actividad=s.actividad if s and rng.random() < 0.7 else None, dia_origen=origen, dia_destino=dia(),
                 hora_inicio=hora() if rng.random() < 0.5 else None)


def responder_al_azar(rng: random.Random, v: PlanVigente, d: Decision, ahora: datetime, mlp) -> ResultadoOrden:
    """Resuelve una decisión como lo haría un estudiante: recorta (protegiendo algo o no), abre mucho tiempo o cancela."""
    elegida = rng.choice(d.opciones)
    if elegida == "abrir_tiempo":
        return aplicar(v, Orden(accion="abrir_tiempo", dia=ahora.date(), dia_fin=d.fecha_critica - timedelta(days=1),
                                hora_inicio=time(6), hora_fin=time(23)), ahora, mlp, d)
    protegidas = rng.sample([a.nombre for a in d.propuesto.plan.actividades], k=rng.randint(0, 1))
    return aplicar(v, Orden(accion="responder_decision", opcion=elegida, protegidas=protegidas), ahora, mlp, d)


def test_secuencias_aleatorias(mlp):
    """Planes y secuencias de órdenes al azar, con el reloj avanzando: ningún plan aplicado viola una regla ni pierde su historial."""
    rng = random.Random(5)
    tipos, creados = Counter(), 0
    for _ in range(60):
        ahora = AHORA + timedelta(hours=rng.randint(0, 12))
        r = crear_plan(plan_aleatorio(rng), ahora, mlp)
        tipos["crear: " + r.tipo] += 1
        if r.tipo != "aplicada":
            continue
        creados += 1
        v, decision = r.vigente, None
        for n in range(8):
            ahora += timedelta(hours=rng.choice([0, 0, 3, 10, 26]))
            if rng.random() < 0.1 and [s for s in v.horario.sesiones if _pasada(s, ahora)]:
                r = marcar_no_hecha(v, rng.choice([s for s in v.horario.sesiones if _pasada(s, ahora)]), ahora, mlp, decision)
            elif decision is not None:
                r = responder_al_azar(rng, v, decision, ahora, mlp)
            else:
                o = orden_aleatoria(rng, v, ahora, n)
                r = aplicar(v, o, ahora, mlp, decision)
            tipos[r.tipo] += 1
            if r.tipo == "aplicada":
                clave = lambda s: (s.actividad, s.dia, s.inicio, s.fin)
                antes = {clave(s) for s in v.horario.sesiones if _pasada(s, ahora) and s.actividad in {a.nombre for a in r.vigente.plan.actividades}}
                despues = {clave(s) for s in r.horario.sesiones}
                assert len(antes - despues) <= 1, "se perdió historial"      # solo la sesión marcada como no hecha puede salir
                revisar(r.vigente, ahora)
                v, decision = r.vigente, None
            elif r.tipo in ("decision", "pregunta"):
                decision = r.decision
                assert decision.faltan_horas > 0
            elif r.tipo == "cancelada":
                decision = None
            elif r.tipo == "rechazada":
                assert r.mensaje and r.vigente is None and r.decision == decision
    print(f"{creados} planes creados; resultados: {dict(tipos)}")
    assert creados > 20 and tipos["aplicada"] > 100 and tipos["decision"] > 0 and tipos["rechazada"] > 0


def contar_reintentos(mlp, n: int = 300, semilla: int = 11) -> Counter:
    """En planes nuevos que el validador acepta, cuántas veces el orden del MLP deja algo incompleto y si el reintento lo resuelve."""
    rng, cuenta = random.Random(semilla), Counter()
    incompletas = lambda r: sum(x.estado != EstadoAsignacion.ASIGNADA for x in r.actividades)
    for _ in range(n):
        plan = plan_aleatorio(rng)
        p = _preparar(PlanVigente(plan=plan, horario=ResultadoAsignacion(sesiones=[], actividades=[])), plan, [], AHORA)
        if not p.pendientes or validar(p.capacidad, HOY, "nuevo").errores:
            continue
        cuenta["planes que caben según el validador"] += 1
        preds = mlp.predecir(p.capacidad, HOY)
        if incompletas(asignar(p.asignable, preds, HOY, p.ocupadas)):
            cuenta["con el orden del MLP queda algo incompleto"] += 1
            cuenta["el reintento lo resuelve" if incompletas(_asignar(p, preds, HOY)) == 0 else "sigue incompleto: se abre una decisión"] += 1
    return cuenta


def test_el_reintento_resuelve_lo_que_el_orden_del_mlp_deja_incompleto(mlp):
    """En planes que el validador acepta, el orden del MLP a veces deja algo incompleto y el reintento siempre lo resuelve."""
    cuenta = contar_reintentos(mlp)
    assert cuenta["planes que caben según el validador"] > 100
    assert cuenta["con el orden del MLP queda algo incompleto"] > 0
    assert cuenta["el reintento lo resuelve"] == cuenta["con el orden del MLP queda algo incompleto"]
