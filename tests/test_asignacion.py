"""Capa de asignación. Ver StudyFlow_CapaAsignacion.ipynb, pasos 2.1, 3.1, 4 y 5."""
import random
from datetime import date

import pytest

from conftest import INICIO, act, dia, dias, f, franjas, plan, plan_captura, pr, rechaza, verificar
from studyflow.asignacion import (EstadoAsignacion, Prediccion, Sesion, _a_sesiones, _ajustar_a_capacidad, _asignar, _bloques_dia, _completar,
                                  _corridas, _costo, _dividir_dias, _limpiar_cortas, _ordenar, _parejas, _repartir_en_dias, _repartir_tramos,
                                  _ubicar_unica, _valida, asignar)
from studyflow.disponibilidad import capacidad_dia, minutos
from studyflow.models import Actividad, PlanEntrada
from studyflow.validador import Estado, validar


UNIF = [1/3] * 3


def H(h, m=0):
    return h * 60 + m


# --- 2.1 Modelos ----------------------------------------------------------------------------------
def test_prediccion_valida():
    assert Prediccion(prioridad=0.87, perfil=[0.45, 0.35, 0.20]).prioridad == 0.87
    Prediccion(prioridad=0, perfil=[1, 0, 0])
    Prediccion(prioridad=1, perfil=[0.333333, 0.333333, 0.333334])
    Prediccion(prioridad=0.5, perfil=[0.2999999, 0.7, 0.0000001])      # softmax de Keras: suma 1 con error ínfimo


@pytest.mark.parametrize("prioridad, perfil, fragmento", [
    (1.2, [0.5, 0.3, 0.2], "less_than_equal"),
    (-0.1, [0.5, 0.3, 0.2], "greater_than_equal"),
    (0.5, [0.5, 0.5], "too_short"),
    (0.5, [0.4, 0.3, 0.2, 0.1], "too_long"),
    (0.5, [0.5, 0.5, 0.5], "sumen 1"),
    (0.5, [1.2, -0.2, 0.0], "sumen 1"),
])
def test_prediccion_rechaza(prioridad, perfil, fragmento):
    rechaza(lambda: Prediccion(prioridad=prioridad, perfil=perfil), fragmento)


def test_sesion():
    s = Sesion(actividad="Taller", dia="2026-10-26", inicio="18:00", fin="19:00")
    assert s.dia == date(2026, 10, 26) and s.fin.hour == 19


# --- 3.1 Funciones auxiliares ---------------------------------------------------------------------
def bl(*pares, tope=None, ocupadas=()):
    return _bloques_dia(dia("2026-10-26", *pares), tope, list(ocupadas))


def test_bloques_dia():
    assert bl(("18:00", "19:30")) == [H(18), H(18, 30), H(19)]
    assert bl(("18:00", "18:50")) == [H(18)]                                       # 50 min -> 1 bloque
    assert bl(("19:00", "20:00"), ("16:00", "17:00")) == [H(16), H(16, 30), H(19), H(19, 30)]   # franja más temprana primero
    assert bl(("18:00", "21:00"), tope=1) == [H(18), H(18, 30)]
    assert len(bl(("18:00", "21:00"), tope=2.75)) == 5                             # el tope también va a bloques
    ocup = [Sesion(actividad="x", dia="2026-10-26", inicio="18:00", fin="19:00")]
    assert bl(("18:00", "21:00"), ocupadas=ocup) == [H(19), H(19, 30), H(20), H(20, 30)]
    assert bl(("18:00", "21:00"), tope=2, ocupadas=ocup) == [H(19), H(19, 30)]     # lo ocupado cuenta para el tope
    assert bl(("18:00", "21:00"), tope=1, ocupadas=ocup) == []


@pytest.mark.parametrize("pares, tope", [
    ((("11:00", "14:00"), ("16:00", "17:30")), None), ((("11:00", "14:00"), ("16:00", "17:30")), 4),
    ((("18:00", "18:45"), ("19:00", "19:45")), None), ((("18:00", "18:20"),), None)])
def test_bloques_igual_que_validador(pares, tope):
    assert len(bl(*pares, tope=tope)) / 2 == capacidad_dia(dia("2026-10-26", *pares), tope)


def test_ordenar():
    acts = [Actividad(nombre=n, prioridad="media", dificultad="media", fecha_limite=fl, horas_estimadas=1)
            for n, fl in [("C", "2026-11-03"), ("A", "2026-11-05"), ("B", "2026-11-03")]]
    preds = [Prediccion(prioridad=q, perfil=[1/3] * 3) for q in (0.5, 0.5, 0.9)]
    assert _ordenar(acts, preds) == [2, 0, 1]
    assert _ordenar(acts, [Prediccion(prioridad=0.5, perfil=[1/3] * 3)] * 3) == [2, 0, 1]   # empate: fecha y nombre


def test_repartir_tramos():
    assert _repartir_tramos(16, [0.45, 0.35, 0.20]) == [7, 6, 3]       # 3 h 30, 3 h, 1 h 30
    assert _repartir_tramos(8, [0.5, 0.3, 0.2]) == [4, 2, 2]
    assert _repartir_tramos(8, [1, 0, 0]) == [8, 0, 0]
    assert _repartir_tramos(1, [0.2, 0.5, 0.3]) == [0, 1, 0]
    assert _repartir_tramos(8, [1/3] * 3) == [3, 3, 2]                  # residuos iguales: el tramo más temprano primero
    rng = random.Random(0)
    for _ in range(5000):
        total = rng.randint(1, 200)
        w = [rng.random() for _ in range(3)]
        perfil = [x / sum(w) for x in w]
        r = _repartir_tramos(total, perfil)
        assert sum(r) == total and min(r) >= 0 and all(abs(x - q * total) < 1 for x, q in zip(r, perfil))


DS = [date(2026, 10, 25 + i) for i in range(7)]


def test_dividir_dias():
    assert [len(t) for t in _dividir_dias(DS)] == [3, 2, 2]
    assert [len(t) for t in _dividir_dias(DS[:6])] == [2, 2, 2]
    assert [len(t) for t in _dividir_dias(DS[:3])] == [1, 1, 1]
    assert [len(t) for t in _dividir_dias(DS[:2])] == [1, 1, 0]
    assert [len(t) for t in _dividir_dias(DS[:1])] == [1, 0, 0]
    assert [len(t) for t in _dividir_dias([])] == [0, 0, 0]
    assert _dividir_dias(DS)[1] == DS[3:5]                              # tramos consecutivos


def test_ajustar_a_capacidad():
    assert _ajustar_a_capacidad([7, 6, 3], [10, 10, 10]) == ([7, 6, 3], 0)
    assert _ajustar_a_capacidad([4, 2, 2], [2, 12, 12]) == ([2, 4, 2], 0)     # el excedente del primero va al segundo
    assert _ajustar_a_capacidad([2, 2, 4], [12, 12, 2]) == ([2, 4, 2], 0)     # el del último va al anterior
    assert _ajustar_a_capacidad([3, 3, 2], [12, 2, 12]) == ([3, 2, 3], 0)     # a igual distancia, al posterior
    assert _ajustar_a_capacidad([5, 0, 0], [2, 0, 3]) == ([2, 0, 3], 0)       # salta un tramo sin espacio
    assert _ajustar_a_capacidad([4, 2, 2], [3, 0, 0]) == ([3, 0, 0], 5)       # lo que no cabe queda sin ubicar
    assert _ajustar_a_capacidad([8, 0, 0], [0, 0, 0]) == ([0, 0, 0], 8)


def test_repartir_en_dias():
    d1, d2, d3 = DS[:3]
    assert _repartir_en_dias(7, {d1: 6, d2: 6}) == {d1: 4, d2: 3}
    assert _repartir_en_dias(3, {d1: 6, d2: 1, d3: 6}) == {d1: 1, d2: 1, d3: 1}
    assert _repartir_en_dias(5, {d1: 6, d2: 1}) == {d1: 4, d2: 1}             # el día pequeño se llena; el resto va al otro
    assert _repartir_en_dias(2, {d1: 6, d2: 6, d3: 6}) == {d1: 1, d2: 1, d3: 0}
    assert _repartir_en_dias(0, {}) == {}


def test_parejas():
    assert _parejas([H(18), H(18, 30), H(19)], 2) == [[H(18), H(18, 30)]]                   # el bloque suelto de las 19:00 queda libre
    assert _parejas([H(16), H(16, 30), H(17), H(17, 30), H(19), H(19, 30)], 2) == [[H(16), H(16, 30)], [H(17), H(17, 30)], [H(19), H(19, 30)]]
    assert _parejas([H(18), H(19)], 2) == []                                               # no son consecutivos
    assert _parejas([H(18), H(18, 30)], 1) == [[H(18)], [H(18, 30)]]
    assert _parejas([], 2) == []


def test_completar():
    d1, d2 = DS[:2]
    libres, tomados = {d1: [H(19), H(21)], d2: [H(18)]}, {d1: [H(18), H(18, 30)]}
    assert _completar(1, [d1, d2], libres, tomados) == 0
    assert tomados[d1] == [H(18), H(18, 30), H(19)] and libres[d1] == [H(21)]               # pegado a lo ya tomado
    libres, tomados = {d1: [H(21)], d2: [H(18)]}, {d1: [H(18), H(18, 30)]}
    assert _completar(1, [d1, d2], libres, tomados) == 0
    assert tomados[d2] == [H(18)]                                                          # sin vecinos libres: el día menos cargado
    libres, tomados = {d1: [H(21)], d2: [H(18)]}, {}
    assert _completar(3, [d1, d2], libres, tomados) == 1                                   # lo que no cabe se devuelve
    assert _completar(0, [], {}, {}) == 0


def ses(bloques, mx):
    return [(s.inicio.strftime("%H:%M"), s.fin.strftime("%H:%M")) for s in _a_sesiones("x", DS[0], bloques, mx)]


def test_a_sesiones():
    assert ses([H(18), H(18, 30), H(19)], 2) == [("18:00", "19:00"), ("19:00", "19:30")]
    assert ses([H(18), H(18, 30), H(19), H(19, 30)], 8) == [("18:00", "20:00")]
    assert ses([H(16), H(16, 30), H(19)], 8) == [("16:00", "17:00"), ("19:00", "19:30")]    # no cruza el hueco entre franjas
    assert ses([H(18)], 1) == [("18:00", "18:30")]
    assert ses([], 2) == []
    assert ses([H(18 + i // 2, 30 * (i % 2)) for i in range(7)], 6) == [("18:00", "20:00"), ("20:00", "21:30")]   # 7 bloques: 4 + 3, no 6 + 1
    assert ses([H(18 + i // 2, 30 * (i % 2)) for i in range(4)], 3) == [("18:00", "19:00"), ("19:00", "20:00")]   # 4 bloques con máximo 3: 2 + 2


def test_corridas():
    assert _corridas([H(18), H(18, 30), H(20)]) == [[H(18), H(18, 30)], [H(20)]]
    assert _corridas([H(18)]) == [[H(18)]]
    assert _corridas([]) == []


def corrida(ini, n):
    return [H(ini) + 30 * i for i in range(n)]


def test_ubicar_unica():
    d1, d2, d3, d4 = DS[:4]
    libres = {d1: corrida(18, 3), d2: corrida(18, 4), d3: corrida(18, 4)}
    assert _ubicar_unica(2, DS[:3], libres, dict.fromkeys(DS[:3], 0), [1, 0, 0]) == (d1, [H(18), H(18, 30)])       # el tramo que prefiere el perfil
    assert _ubicar_unica(2, DS[:3], libres, dict.fromkeys(DS[:3], 0), [0, 1, 0]) == (d2, [H(18), H(18, 30)])
    assert _ubicar_unica(5, DS[:3], libres, dict.fromkeys(DS[:3], 0), [1, 0, 0]) is None                            # ningún hueco seguido alcanza
    assert _ubicar_unica(4, DS[:3], libres, dict.fromkeys(DS[:3], 0), [1, 0, 0]) == (d2, corrida(18, 4))           # sin hueco en su tramo: el vecino
    libres = {d1: corrida(18, 4), d2: corrida(18, 5), d3: corrida(18, 4), d4: corrida(18, 4)}
    assert _ubicar_unica(3, DS[:4], libres, dict.fromkeys(DS[:4], 0), [1, 0, 0]) == (d2, corrida(18, 3))           # d1 dejaría media hora suelta
    assert _ubicar_unica(3, DS[:4], libres, {d1: 0, d2: 4, d3: 0, d4: 0}, [1, 0, 0]) == (d1, corrida(18, 3))       # el día menos cargado va primero
    libres = {d1: corrida(18, 2) + corrida(20, 4)}
    assert _ubicar_unica(3, DS[:1], libres, {d1: 0}, [1, 0, 0]) == (d1, corrida(20, 3))                            # un hueco seguido, aunque no sea el primero
    assert _ubicar_unica(1, [], {}, {}, [1, 0, 0]) is None


def test_valida():
    assert all(_valida(n, 6) for n in (0, 2, 3, 6, 7, 11))      # 7 con máximo 6 son 4 + 3; 13 son 5 + 4 + 4
    assert not _valida(1, 6)
    assert _valida(4, 2) and not _valida(3, 2) and not _valida(5, 2)          # con máximo de 1 h no hay sesiones de 1,5 h


def test_limpiar_cortas():
    d1, d2 = DS[:2]
    base = lambda: {d1: corrida(18, 3), d2: corrida(18, 2)}
    # hay espacio en el otro hueco: el bloque suelto se va con las demás horas de su actividad
    r = _limpiar_cortas({0: {d1: [H(18), H(18, 30)], d2: [H(18)]}}, base(), set(), 6)
    assert r[0] == {d1: [H(18), H(18, 30), H(19)], d2: []}
    # hay espacio en el hueco del bloque suelto: se trae un bloque de la sesión larga
    r = _limpiar_cortas({0: {d1: [H(18), H(18, 30), H(19)], d2: [H(18)]}}, base(), set(), 6)
    assert r[0] == {d1: [H(18), H(18, 30)], d2: [H(18), H(18, 30)]}
    # no hay espacio: se cambia el bloque por uno de otra actividad que en los dos huecos queda en sesiones de al menos 1 h
    llenos = {d1: corrida(18, 6), d2: corrida(18, 4)}
    r = _limpiar_cortas({0: {d1: corrida(18, 3), d2: [H(18)]}, 1: {d1: [H(19, 30), H(20), H(20, 30)], d2: [H(18, 30), H(19), H(19, 30)]}}, llenos, set(), 6)
    assert r[0] == {d1: corrida(18, 4), d2: []} and r[1] == {d1: [H(20), H(20, 30)], d2: corrida(18, 4)}
    # las actividades de una sola sesión no se mueven
    r = _limpiar_cortas({0: {d1: [H(18), H(18, 30)], d2: [H(18)]}}, base(), {0}, 6)
    assert r[0] == {d1: [H(18), H(18, 30)], d2: [H(18)]}
    # con sesiones de máximo 1 h no se forman sesiones de 1 h 30
    r = _limpiar_cortas({0: {d1: [H(18), H(18, 30)], d2: [H(18)]}}, base(), set(), 2)
    assert r[0] == {d1: [H(18), H(18, 30)], d2: [H(18)]}
    # una actividad de media hora no se toca
    r = _limpiar_cortas({0: {d1: [H(18)]}}, base(), set(), 6)
    assert r[0] == {d1: [H(18)]}


def test_asignar_exige_una_prediccion_por_actividad():
    plan_min = PlanEntrada(actividades=[dict(nombre="T", prioridad="alta", dificultad="alta", fecha_limite="2026-11-05", horas_estimadas=2)],
                           disponibilidad=[{"dia": "2026-11-01", "franjas": franjas(("18:00", "21:00"))}], duracion_sesion=60)
    with pytest.raises(ValueError, match="una predicción por actividad"):
        asignar(plan_min, [], date(2026, 11, 1))


def test_sesion_minima_y_respaldo():
    """Con piezas de 1 h sobra una media hora; con `respaldo` se prueba sin las reglas y con bloques de 30 min y se queda lo que ubica más."""
    d = plan([act("A0", 4, 2), act("A1", 3, 2.5)],
             [{"dia": f(n), "franjas": franjas(("18:00", fin))} for n, fin in enumerate(["19:00", "20:00", "19:00", "19:00"])], sesion=90)
    p, preds = PlanEntrada(**d), [pr(0.2, [0.5, 1/3, 1/6]), pr(0.0, UNIF)]
    assert sum(a.horas_faltantes for a in _asignar(p, preds, INICIO, [], 2).actividades) == 0.5
    assert sum(a.horas_faltantes for a in asignar(p, preds, INICIO, respaldo=False).actividades) == 0.5
    assert sum(a.horas_faltantes for a in asignar(p, preds, INICIO).actividades) == 0


def cortas(r):
    return [s for s in r.sesiones if minutos(s.fin) - minutos(s.inicio) < 60]


PERFILES_CAPTURA = [UNIF, [0.5, 0.3, 0.2], [0.2, 0.3, 0.5], [0.2, 0.6, 0.2]]
PRIORIDADES_CAPTURA = [(0.9, 0.5, 0.5, 0.5, 0.5), (0.2, 0.9, 0.5, 0.4, 0.8), (0.6, 0.3, 0.9, 0.7, 0.1)]


def test_plan_ajustado_sin_sesiones_de_30_min():
    """34 h en 34,5 h libres, con huecos que dejan medias horas: nada de sesiones de 30 min y las de media/baja quedan en una sesión."""
    d = plan_captura()
    p = PlanEntrada(**d)
    assert validar(p, INICIO).estado == Estado.FACTIBLE
    for perfil in PERFILES_CAPTURA:
        for prioridades in PRIORIDADES_CAPTURA:
            r = asignar(p, [pr(x, perfil) for x in prioridades], INICIO, respaldo=False)
            if (perfil, prioridades) == (PERFILES_CAPTURA[1], PRIORIDADES_CAPTURA[2]):
                assert sum(a.horas_faltantes for a in r.actividades) == 1      # este orden deja algo sin ubicar: lo resuelve la capa de órdenes
                continue
            verificar(d, r, INICIO)
            assert all(a.estado == EstadoAsignacion.ASIGNADA for a in r.actividades) and cortas(r) == []
            for nombre in ("lab FDSI", "Quiz ARSW"):
                assert len([s for s in r.sesiones if s.actividad == nombre]) == 1, (perfil, prioridades, nombre)


def test_costo():
    d = plan([act("Lab", 6, 2), act("Informe", 6, 4)], dias(0, 10), sesion=180)
    p = PlanEntrada(**d)
    bien = asignar(p, [pr(0.5, UNIF), pr(0.4, UNIF)], INICIO)
    assert _costo(p, bien, 6) == (0, 0, 0)
    partida = bien.model_copy(update={"sesiones": [s for s in bien.sesiones if s.actividad != "Lab"] + [
        Sesion(actividad="Lab", dia=f(0), inicio="18:00", fin="19:00"), Sesion(actividad="Lab", dia=f(1), inicio="18:00", fin="18:30")]})
    assert _costo(p, partida, 6) == (0, 1, 1)                                  # una sesión de 30 min y una actividad de una sola sesión partida
    sin_ubicar = bien.model_copy(update={"actividades": [x.model_copy(update={"horas_faltantes": 1.5}) for x in bien.actividades]})
    assert _costo(p, sin_ubicar, 6)[0] == 3


# --- 4. Ejemplo completo --------------------------------------------------------------------------
def test_ejemplo_completo():
    d = plan([act("Parcial de IA", 6, 8, "alta", "alta"), act("Taller", 5, 4)], dias(0, 10))
    ejemplo = PlanEntrada(**d)
    assert validar(ejemplo, INICIO).estado == Estado.FACTIBLE
    r = asignar(ejemplo, [pr(0.87, [0.45, 0.35, 0.20]), pr(0.55, [0.30, 0.40, 0.30])], INICIO)
    verificar(d, r, INICIO)
    assert all(a.estado == EstadoAsignacion.ASIGNADA for a in r.actividades)


# --- 5.2 Casos ------------------------------------------------------------------------------------
def hh(h):
    return f"{h:g}"


def esp(estados, horas_dia=None, sesiones=None):
    return {"estados": estados, "horas_dia": horas_dia, "sesiones": sesiones}


OC = Sesion(actividad="Fijada", dia=f(0), inicio="18:00", fin="19:00")

CASOS = [
    {"nombre": "Reparto del ejemplo (8 h)",
     "entrada": plan([act("Parcial", 6, 8, "alta", "alta")], dias(0, 10)), "pred": [pr(0.87, [0.45, 0.35, 0.20])],
     "esperado": esp({"Parcial": "asignada"}, {0: 2, 1: 2, 2: 2, 3: 1, 4: 1})},
    {"nombre": "Manda la prioridad, no el orden de la lista",
     "entrada": plan([act("Taller B", 3, 3), act("Informe A", 3, 3)], dias(0, 3, (("18:00", "20:00"),))), "pred": [pr(0.9, UNIF), pr(0.4, UNIF)],
     "esperado": esp({"Taller B": "asignada", "Informe A": "asignada"}, {0: 2, 1: 2, 2: 2},
                     [(t, n, i, e) for n in range(3) for t, i, e in (("Taller B", "18:00", "19:00"), ("Informe A", "19:00", "20:00"))])},
    {"nombre": "Primer tramo sin espacio",
     "entrada": plan([act("Proyecto", 6, 4)], dias(0, 2, (("18:00", "18:30"),)) + dias(2, 10)), "pred": [pr(0.7, [0.5, 0.3, 0.2])],
     "esperado": esp({"Proyecto": "asignada"}, {2: 2, 3: 1, 4: 1})},
    {"nombre": "Último tramo sin espacio",
     "entrada": plan([act("Proyecto", 6, 4)], dias(0, 4) + dias(4, 6, (("18:00", "18:30"),)) + dias(6, 10)), "pred": [pr(0.7, [0.2, 0.3, 0.5])],
     "esperado": esp({"Proyecto": "asignada"}, {0: 1, 2: 2, 3: 1})},
    {"nombre": "Tramo del medio sin espacio",
     "entrada": plan([act("Proyecto", 6, 4)], dias(0, 2) + dias(2, 4, (("18:00", "18:30"),)) + dias(4, 10)), "pred": [pr(0.7, [0.3, 0.4, 0.3])],
     "esperado": esp({"Proyecto": "asignada"}, {0: 1, 4: 2, 5: 1})},
    {"nombre": "Solo cabe una parte",
     "entrada": plan([act("Tesis", 3, 6)], dias(0, 3, (("18:00", "19:00"),))), "pred": [pr(0.8, UNIF)],
     "esperado": esp({"Tesis": "parcial (faltan 3 h)"}, {0: 1, 1: 1, 2: 1})},
    {"nombre": "Sin espacio para la de menor prioridad",
     "entrada": plan([act("Examen", 3, 3), act("Lectura", 3, 2)], dias(0, 3, (("18:00", "19:00"),))), "pred": [pr(0.9, UNIF), pr(0.3, UNIF)],
     "esperado": esp({"Examen": "asignada", "Lectura": "no_asignada (faltan 2 h)"}, {0: 1, 1: 1, 2: 1})},
    {"nombre": "Máximo diario de 1 h",
     "entrada": plan([act("Proyecto", 5, 4)], dias(0, 10), max_horas_dia=1), "pred": [pr(0.6, UNIF)],
     "esperado": esp({"Proyecto": "asignada"}, {0: 1, 1: 1, 2: 1, 4: 1})},
    {"nombre": "Sesiones de máximo 90 min",
     "entrada": plan([act("Informe", 3, 4)], dias(0, 10, (("18:00", "22:00"),)), sesion=90), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Informe": "asignada"}, {0: 4}, [("Informe", 0, "18:00", "19:30"), ("Informe", 0, "19:30", "21:00"), ("Informe", 0, "21:00", "22:00")])},
    {"nombre": "Dos franjas en el mismo día",
     "entrada": plan([act("Informe", 3, 2)], dias(0, 10, (("16:00", "17:30"), ("19:00", "20:30"))), sesion=90), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Informe": "asignada"}, {0: 2}, [("Informe", 0, "16:00", "17:00"), ("Informe", 0, "19:00", "20:00")])},
    {"nombre": "Franja que no es múltiplo de 30 min",
     "entrada": plan([act("Informe", 3, 2)], dias(0, 10, (("18:00", "19:50"),)), sesion=120), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Informe": "asignada"}, {0: 1, 1: 1}, [("Informe", 0, "18:00", "19:00"), ("Informe", 1, "18:00", "19:00")])},
    {"nombre": "Replanificación: lo ocupado se respeta", "ocupadas": [OC],
     "entrada": plan([act("Taller", 3, 2)], dias(0, 10)), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Taller": "asignada"}, {0: 2}, [("Taller", 0, "19:00", "20:00"), ("Taller", 0, "20:00", "21:00")])},
    {"nombre": "Lo ocupado cuenta para el máximo diario", "ocupadas": [OC],
     "entrada": plan([act("Taller", 3, 2)], dias(0, 10), max_horas_dia=2), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Taller": "asignada"}, {0: 1, 1: 1}, [("Taller", 0, "19:00", "20:00"), ("Taller", 1, "18:00", "19:00")])},
    {"nombre": "Ventana de 2 días (replanificación)",
     "entrada": plan([act("Quiz", 2, 3)], dias(0, 10)), "pred": [pr(0.6, [0.4, 0.3, 0.3])],
     "esperado": esp({"Quiz": "asignada"}, {0: 1, 1: 2})},
    {"nombre": "Fecha límite vencida",
     "entrada": plan([act("Vieja", 0, 2), act("Taller", 5, 2)], dias(0, 10)), "pred": [pr(0.9, UNIF), pr(0.5, UNIF)],
     "esperado": esp({"Vieja": "no_asignada (faltan 2 h)", "Taller": "asignada"}, {0: 1, 2: 1})},
    {"nombre": "Disponibilidad anterior al inicio",
     "entrada": plan([act("Taller", 6, 2)], dias(-2, 10)), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Taller": "asignada"}, {0: 1, 2: 1})},
    {"nombre": "Horas que no son múltiplo de 30 min",
     "entrada": plan([act("Taller", 3, 2.2)], dias(0, 10)), "pred": [pr(0.5, [1, 0, 0])],
     "esperado": esp({"Taller": "asignada"}, {0: 2})},
    {"nombre": "Sesiones de al menos 1 h",
     "entrada": plan([act("Informe", 6, 2.5)], dias(0, 10), sesion=120), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Informe": "asignada"}, {0: 1.5, 2: 1}, [("Informe", 0, "18:00", "19:30"), ("Informe", 2, "18:00", "19:00")])},
    {"nombre": "Media hora solo si la actividad es de media hora",
     "entrada": plan([act("Lectura", 3, 0.5)], dias(0, 10)), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Lectura": "asignada"}, {0: 0.5}, [("Lectura", 0, "18:00", "18:30")])},
    {"nombre": "Sesión máxima de 30 min",
     "entrada": plan([act("Informe", 6, 1)], dias(0, 10), sesion=30), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Informe": "asignada"}, {0: 0.5, 2: 0.5}, [("Informe", 0, "18:00", "18:30"), ("Informe", 2, "18:00", "18:30")])},
    {"nombre": "Con piezas de 1 h no cabe todo: solo la media hora que hace falta",
     "entrada": plan([act("A0", 4, 2), act("A1", 3, 2.5)],
                     [{"dia": f(n), "franjas": franjas(("18:00", fin))} for n, fin in enumerate(["19:00", "20:00", "19:00", "19:00"])], sesion=90),
     "pred": [pr(0.2, [0.5, 1/3, 1/6]), pr(0.0, UNIF)],
     "esperado": esp({"A0": "asignada", "A1": "asignada"}, {0: 1, 1: 2, 2: 0.5, 3: 1},
                     [("A1", 0, "18:00", "19:00"), ("A0", 1, "18:00", "19:00"), ("A1", 1, "19:00", "20:00"), ("A1", 2, "18:00", "18:30"),
                      ("A0", 3, "18:00", "19:00")])},
    {"nombre": "Prioridad media que cabe en una sesión: una sola sesión",
     "entrada": plan([act("Lab", 6, 2)], dias(0, 10), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Lab": "asignada"}, {0: 2}, [("Lab", 0, "18:00", "20:00")])},
    {"nombre": "Prioridad baja que cabe en una sesión: una sola sesión",
     "entrada": plan([act("Quiz", 6, 3, "baja", "baja")], dias(0, 10), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Quiz": "asignada"}, {0: 3}, [("Quiz", 0, "18:00", "21:00")])},
    {"nombre": "Sesión única: manda el perfil, no el primer día",
     "entrada": plan([act("Lab", 6, 2)], dias(0, 10), sesion=180), "pred": [pr(0.5, [0, 0, 1])],
     "esperado": esp({"Lab": "asignada"}, {4: 2}, [("Lab", 4, "18:00", "20:00")])},
    {"nombre": "Prioridad alta se reparte aunque quepa en una sesión",
     "entrada": plan([act("Parcial", 6, 2, "alta", "alta")], dias(0, 10), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Parcial": "asignada"}, {0: 1, 2: 1}, [("Parcial", 0, "18:00", "19:00"), ("Parcial", 2, "18:00", "19:00")])},
    {"nombre": "Prioridad media que no cabe en una sesión se reparte",
     "entrada": plan([act("Informe", 6, 4)], dias(0, 10), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Informe": "asignada"}, {0: 1, 1: 1, 2: 1, 4: 1})},
    {"nombre": "Sin un hueco seguido, la sesión única se parte",
     "entrada": plan([act("Lab", 6, 2)], dias(0, 10, (("18:00", "19:00"),)), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Lab": "asignada"}, {0: 1, 2: 1}, [("Lab", 0, "18:00", "19:00"), ("Lab", 2, "18:00", "19:00")])},
    {"nombre": "Las de una sola sesión se ubican antes (necesitan un hueco seguido)",
     "entrada": plan([act("Parcial", 3, 3, "alta", "alta"), act("Quiz", 3, 3)], dias(0, 4), sesion=180), "pred": [pr(0.7, UNIF), pr(0.3, UNIF)],
     "esperado": esp({"Parcial": "asignada", "Quiz": "asignada"}, {0: 3, 1: 1, 2: 2},
                     [("Quiz", 0, "18:00", "21:00"), ("Parcial", 1, "18:00", "19:00"), ("Parcial", 2, "18:00", "20:00")])},
    {"nombre": "Sesión única: a igual carga, el hueco que no deja media hora suelta",
     "entrada": plan([act("Lab", 6, 2)], dias(0, 1, (("18:00", "21:30"),)) + dias(1, 10, (("18:00", "21:00"),)), sesion=180), "pred": [pr(0.5, UNIF)],
     "esperado": esp({"Lab": "asignada"}, {1: 2}, [("Lab", 1, "18:00", "20:00")])},
]


def ejecutar(caso):
    p = PlanEntrada(**caso["entrada"])
    ocupadas = caso.get("ocupadas", [])
    r = asignar(p, caso["pred"], INICIO, ocupadas)
    verificar(caso["entrada"], r, INICIO, ocupadas)
    estados = {a.nombre: a.estado.value + ("" if a.estado.value == "asignada" else f" (faltan {hh(a.horas_faltantes)} h)") for a in r.actividades}
    horas_dia = {}
    for s in r.sesiones:
        n = (s.dia - INICIO).days
        horas_dia[n] = horas_dia.get(n, 0) + (minutos(s.fin) - minutos(s.inicio)) / 60
    sesiones = [(s.actividad, (s.dia - INICIO).days, f"{s.inicio:%H:%M}", f"{s.fin:%H:%M}") for s in r.sesiones]
    return esp(estados, dict(sorted(horas_dia.items())), sesiones)


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_casos(caso):
    esperado, lograda = caso["esperado"], ejecutar(caso)
    assert lograda["estados"] == esperado["estados"]
    assert lograda["horas_dia"] == esperado["horas_dia"]
    if esperado["sesiones"] is not None:
        assert lograda["sesiones"] == esperado["sesiones"]


# --- 5.3 Planes aleatorios ------------------------------------------------------------------------
NIV = ["baja", "media", "alta"]
FRANJAS = [(("18:00", "21:00"),), (("08:00", "10:00"), ("14:00", "16:30")), (("19:00", "20:30"),), (("16:00", "17:50"), ("19:00", "21:00"))]


def test_planes_aleatorios():
    rng = random.Random(7)

    def plan_aleatorio():
        actividades = [act(f"A{i}", rng.randint(3, 14), rng.choice([1, 1.5, 2, 3, 4, 5, 6]), rng.choice(NIV), rng.choice(NIV))
                       for i in range(rng.randint(1, 5))]
        disponibilidad = [{"dia": f(n), "franjas": franjas(*rng.choice(FRANJAS))} for n in range(16) if rng.random() < 0.85]
        return plan(actividades, disponibilidad, rng.choice([60, 90, 120]), rng.choice([None, 3, 4]))

    def prediccion_aleatoria():
        w = [rng.random() for _ in range(3)]
        return pr(rng.random(), [x / sum(w) for x in w])

    factibles = 0
    for _ in range(2000):
        d = plan_aleatorio()
        if not d["disponibilidad"]:
            continue
        p = PlanEntrada(**d)
        if validar(p, INICIO).estado == Estado.INVIABLE:
            continue
        factibles += 1
        verificar(d, asignar(p, [prediccion_aleatoria() for _ in p.actividades], INICIO), INICIO)
    assert factibles == 1732    # mismo número que en el notebook
