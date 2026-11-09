"""Capa de asignación determinista: modelos y algoritmo. Ver StudyFlow_CapaAsignacion.ipynb, pasos 2 y 3."""
from datetime import date, time
from enum import Enum
from math import ceil, floor

from pydantic import BaseModel, Field, field_validator

from studyflow.disponibilidad import BLOQUE_MIN, minutos
from studyflow.models import Actividad, DisponibilidadDia, Nivel, PlanEntrada

TRAMOS = 3
SESION_MIN = 60  # minutos: toda sesión dura al menos esto, salvo que no quede otra (media hora suelta, hueco de 30 min)


class Prediccion(BaseModel):
    prioridad: float = Field(ge=0, le=1)
    perfil: list[float] = Field(min_length=TRAMOS, max_length=TRAMOS)

    @field_validator("perfil")
    @classmethod
    def _perfil_valido(cls, v):
        if min(v) < 0 or abs(sum(v) - 1) > 1e-3:
            raise ValueError("el perfil debe tener proporciones >= 0 que sumen 1")
        return v


class Sesion(BaseModel):
    actividad: str
    dia: date
    inicio: time
    fin: time


class EstadoAsignacion(str, Enum):
    ASIGNADA = "asignada"
    PARCIAL = "parcial"
    NO_ASIGNADA = "no_asignada"


class ResumenActividad(BaseModel):
    nombre: str
    prioridad: float
    horas_solicitadas: float
    horas_asignadas: float
    horas_faltantes: float
    estado: EstadoAsignacion


class ResultadoAsignacion(BaseModel):
    sesiones: list[Sesion]
    actividades: list[ResumenActividad]


def _hora(minutos: int):
    return time(minutos // 60, minutos % 60)


def _bloques_dia(dia: DisponibilidadDia, tope: float | None, ocupadas: list[Sesion]):
    """Inicio (minutos del día) de los bloques libres, del más temprano al más tardío."""
    bloques = []
    for f in sorted(dia.franjas, key=lambda f: f.inicio):
        ini, fin = minutos(f.inicio), minutos(f.fin)
        bloques += range(ini, ini + (fin - ini) // BLOQUE_MIN * BLOQUE_MIN, BLOQUE_MIN)
    libres = [b for b in bloques
              if not any(minutos(s.inicio) < b + BLOQUE_MIN and b < minutos(s.fin) for s in ocupadas)]
    if tope is not None:
        usados = sum((minutos(s.fin) - minutos(s.inicio)) // BLOQUE_MIN for s in ocupadas)
        libres = libres[:max(int(tope * 60) // BLOQUE_MIN - usados, 0)]
    return libres


def _ordenar(actividades: list[Actividad], predicciones: list[Prediccion]):
    """Índices de las actividades de mayor a menor prioridad (empate: fecha límite y nombre)."""
    return sorted(range(len(actividades)),
                  key=lambda i: (-predicciones[i].prioridad, actividades[i].fecha_limite, actividades[i].nombre))


def _repartir_tramos(total: int, perfil: list[float]) -> list[int]:
    """Bloques por tramo con el método del mayor residuo; la suma es exactamente `total`."""
    suma = sum(perfil)
    ideal = [p / suma * total for p in perfil]
    base = [floor(x + 1e-9) for x in ideal]
    por_dar = sorted(range(TRAMOS), key=lambda i: (-(ideal[i] - base[i]), i))
    for i in por_dar[:total - sum(base)]:
        base[i] += 1
    return base


def _dividir_dias(dias: list[date]):
    """Parte los días en 3 tramos consecutivos; los primeros reciben el sobrante."""
    n, resto = divmod(len(dias), TRAMOS)
    tramos, i = [], 0
    for t in range(TRAMOS):
        k = n + (t < resto)
        tramos.append(dias[i:i + k])
        i += k
    return tramos


def _ajustar_a_capacidad(quieren: list[int], caben: list[int]):
    """Recorta cada tramo a su capacidad y pasa el excedente al vecino más cercano con espacio
    (a igual distancia, el posterior). Devuelve (bloques por tramo, bloques sin ubicar)."""
    puestos = [min(q, c) for q, c in zip(quieren, caben)]
    sobras = [q - p for q, p in zip(quieren, puestos)]
    sin_ubicar = 0
    for i, sobra in enumerate(sobras):
        for j in sorted(range(TRAMOS), key=lambda j: (abs(j - i), -j)):
            dar = min(sobra, caben[j] - puestos[j])
            puestos[j] += dar
            sobra -= dar
        sin_ubicar += sobra
    return puestos, sin_ubicar


def _repartir_en_dias(cantidad: int, libres: dict[date, int]):
    """Reparte `cantidad` bloques parejo entre los días, sin pasar el espacio libre de cada uno."""
    puestos = dict.fromkeys(libres, 0)
    for _ in range(cantidad):
        dia = min((d for d in libres if puestos[d] < libres[d]), key=lambda d: (puestos[d], d))
        puestos[dia] += 1
    return puestos


def _parejas(bloques: list[int], tam: int):
    """Grupos de `tam` bloques seguidos, del más temprano al más tardío; un bloque que no completa grupo queda libre."""
    grupos, corrida = [], []
    for b in bloques + [None]:
        if corrida and (b is None or b != corrida[-1] + BLOQUE_MIN):
            grupos += [corrida[i:i + tam] for i in range(0, len(corrida) - tam + 1, tam)]
            corrida = []
        if b is not None:
            corrida.append(b)
    return grupos


def _a_sesiones(nombre: str, dia: date, bloques: list[int], max_bloques: int):
    """Une bloques consecutivos en sesiones; si una corrida pasa de `max_bloques`, la parte en sesiones parejas
    (7 bloques con máximo 6 dan 4 + 3, no 6 + 1)."""
    corridas = []
    for b in bloques:
        if corridas and b == corridas[-1][-1] + BLOQUE_MIN:
            corridas[-1].append(b)
        else:
            corridas.append([b])
    sesiones = []
    for c in corridas:
        k = ceil(len(c) / max_bloques)
        base, extra, i = *divmod(len(c), k), 0
        for j in range(k):
            n = base + (j < extra)
            sesiones.append(Sesion(actividad=nombre, dia=dia, inicio=_hora(c[i]), fin=_hora(c[i] + n * BLOQUE_MIN)))
            i += n
    return sesiones


def _completar(faltan: int, ventana: list[date], libres: dict[date, list[int]], tomados: dict[date, list[int]]):
    """Ubica `faltan` bloques sueltos: pegados a lo ya tomado (el día menos cargado de la actividad) para no abrir una
    sesión de 30 min aparte y, si no hay, en cualquier hueco de la ventana. Devuelve los que no cupieron."""
    while faltan:
        propios = {d: set(tomados.get(d, ())) for d in ventana}
        pegados = [(len(propios[d]), d, b) for d in ventana for b in libres[d]
                   if b - BLOQUE_MIN in propios[d] or b + BLOQUE_MIN in propios[d]]
        sueltos = [(len(propios[d]), d, libres[d][0]) for d in ventana if libres[d]]
        elegido = min(pegados or sueltos, default=None)
        if elegido is None:
            break
        _, dia, bloque = elegido
        libres[dia].remove(bloque)
        tomados.setdefault(dia, []).append(bloque)
        faltan -= 1
    return faltan


def _corridas(bloques: list[int]):
    """Grupos de bloques seguidos (un hueco sin bloques corta el grupo)."""
    grupos: list[list[int]] = []
    for b in bloques:
        if grupos and b == grupos[-1][-1] + BLOQUE_MIN:
            grupos[-1].append(b)
        else:
            grupos.append([b])
    return grupos


def _ubicar_unica(total: int, ventana: list[date], libres: dict[date, list[int]], carga: dict[date, int], perfil: list[float]):
    """Hueco seguido de `total` bloques para la actividad completa: en el tramo que prefiere su perfil (o el vecino más
    cercano), el día menos cargado; a igual carga, el hueco que deja un resto par (así no queda media hora suelta) y,
    después, el más ajustado. Devuelve (día, bloques) o None si ningún día tiene un hueco así de largo."""
    tramos = _dividir_dias(ventana)
    preferido = _repartir_tramos(1, perfil).index(1)
    for t in sorted(range(TRAMOS), key=lambda j: (abs(j - preferido), -j)):
        opciones = [(carga[d], (len(c) - total) % 2, len(c), d, c[0]) for d in tramos[t] for c in _corridas(libres[d]) if len(c) >= total]
        if opciones:
            _, _, _, dia, inicio = min(opciones)
            return dia, [b for b in libres[dia] if inicio <= b < inicio + total * BLOQUE_MIN]
    return None


def _valida(n: int, max_bloques: int) -> bool:
    """Un grupo de `n` bloques seguidos de una actividad queda en sesiones de al menos 1 h (0 = no hay grupo)."""
    return n == 0 or (n >= SESION_MIN // BLOQUE_MIN and n // ceil(n / max_bloques) >= SESION_MIN // BLOQUE_MIN)


def _limpiar_cortas(colocado: dict[int, dict[date, list[int]]], libres0: dict[date, list[int]], fijas: set[int], max_bloques: int):
    """Quita las sesiones de media hora de las actividades que se pueden mover. Trabaja con cuántos bloques tiene cada
    actividad en cada hueco seguido del día (dentro de un hueco el orden da igual) y, para cada grupo que quedó corto, prueba:
      1. traer a ese hueco un bloque de otra sesión de la misma actividad, si el hueco tiene espacio;
      2. llevar el bloque a otro hueco donde la actividad ya estudia: si hay espacio, directo; si no, cambiándolo por un
         bloque de otra actividad que en los dos huecos quede en sesiones de al menos 1 h.
    Nunca cambia cuántas horas tiene cada actividad ni cuántas se estudian cada día."""
    huecos = [(d, c) for d in sorted(libres0) for c in _corridas(libres0[d])]
    hueco_de = {(d, b): k for k, (d, c) in enumerate(huecos) for b in c}
    x: dict[int, dict[int, int]] = {i: {} for i in colocado}
    for i, por_dia in colocado.items():
        for d, bloques in por_dia.items():
            for b in bloques:
                x[i][hueco_de[d, b]] = x[i].get(hueco_de[d, b], 0) + 1
    libre = [len(c) - sum(xi.get(k, 0) for xi in x.values()) for k, (_, c) in enumerate(huecos)]
    moviles = [i for i in sorted(x) if i not in fijas and sum(x[i].values()) > 1]
    tocados: set[int] = set()

    def mover(i: int, de: int, a: int):
        x[i][de] -= 1
        if not x[i][de]:
            del x[i][de]
        x[i][a] = x[i].get(a, 0) + 1
        tocados.update((de, a))

    def arreglar(i: int, k: int) -> bool:
        xi = x[i]
        if libre[k] and _valida(xi[k] + 1, max_bloques):
            traibles = [k3 for k3 in xi if k3 != k and _valida(xi[k3] - 1, max_bloques)]
            if traibles:
                k3 = min(traibles, key=lambda k3: (xi[k3] != 1, -xi[k3], k3))
                mover(i, k3, k)
                libre[k3] += 1
                libre[k] -= 1
                return True
        for k2 in sorted((k2 for k2 in xi if k2 != k and _valida(xi[k2] + 1, max_bloques)),
                         key=lambda k2: (xi[k2] != 1, k2)):
            if libre[k2]:
                mover(i, k, k2)
                libre[k] += 1
                libre[k2] -= 1
                return True
            for j in moviles:
                if j != i and k in x[j] and k2 in x[j] and _valida(x[j][k2] - 1, max_bloques) and _valida(x[j][k] + 1, max_bloques):
                    mover(i, k, k2)
                    mover(j, k2, k)
                    return True
        return False

    avanzo = True
    while avanzo:
        avanzo = False
        for i in moviles:
            for k in sorted(x[i]):
                if k in x[i] and not _valida(x[i][k], max_bloques) and arreglar(i, k):
                    avanzo = True

    for k in sorted(tocados):                                  # solo se vuelven a acomodar los huecos que cambiaron
        d, c = huecos[k]
        antes = {i: [b for b in colocado[i].get(d, []) if hueco_de[d, b] == k] for i in colocado}
        orden = sorted((i for i in x if k in x[i]), key=lambda i: (antes[i][0] if antes[i] else c[-1] + 1, i))
        for i in colocado:
            if d in colocado[i]:
                colocado[i][d] = [b for b in colocado[i][d] if hueco_de[d, b] != k]
        sitio = 0
        for i in orden:
            colocado[i].setdefault(d, []).extend(c[sitio:sitio + x[i][k]])
            sitio += x[i][k]
    return colocado


def _costo(plan: PlanEntrada, r: ResultadoAsignacion, max_bloques: int):
    """Con qué tan mal quedó un horario, de lo más grave a lo menos: horas sin ubicar, sesiones de menos de 1 h (salvo en
    actividades de media hora) y actividades de una sola sesión que quedaron partidas."""
    faltan = sum(x.horas_faltantes for x in r.actividades)
    cortas = partidas = 0
    for a in plan.actividades:
        propias = [s for s in r.sesiones if s.actividad == a.nombre]
        bloques = max(1, int(a.horas_estimadas * 60 / BLOQUE_MIN + 0.5))
        if bloques > 1:
            cortas += sum(minutos(s.fin) - minutos(s.inicio) < SESION_MIN for s in propias)
        if a.prioridad != Nivel.ALTA and bloques <= max_bloques and len(propias) > 1:
            partidas += 1
    return faltan, cortas, partidas


def asignar(plan: PlanEntrada, predicciones: list[Prediccion], fecha_inicio: date,
            ocupadas: list[Sesion] | None = None, respaldo: bool = True) -> ResultadoAsignacion:
    """Prueba dos órdenes de trabajo (por prioridad, y con las actividades de una sola sesión primero, que son las que más
    necesitan un hueco seguido) y se queda con el que deje el mejor horario. Si aun así queda algo sin ubicar y
    `respaldo` es True, prueba también sin las reglas de sesión única y de limpieza, y con bloques de 30 min, y se queda
    con lo que ubique más horas."""
    if len(predicciones) != len(plan.actividades):
        raise ValueError("debe haber una predicción por actividad, en el mismo orden")
    ocupadas = ocupadas or []
    max_bloques = plan.duracion_sesion // BLOQUE_MIN
    unidad = min(SESION_MIN // BLOQUE_MIN, max_bloques)  # bloques por pieza (2 = sesiones de 1 h o más)

    def costo(r: ResultadoAsignacion):
        return _costo(plan, r, max_bloques)

    candidatos = [_asignar(plan, predicciones, fecha_inicio, ocupadas, unidad, primero_unicas=pu) for pu in (False, True)]
    mejor = min(candidatos, key=costo)                         # a igual costo, el orden por prioridad
    if respaldo and costo(mejor)[0] > 0:
        extras = [_asignar(plan, predicciones, fecha_inicio, ocupadas, unidad, reglas=False)]
        if unidad > 1:
            extras += [_asignar(plan, predicciones, fecha_inicio, ocupadas, 1, reglas=False)]
            extras += [_asignar(plan, predicciones, fecha_inicio, ocupadas, 1, primero_unicas=pu) for pu in (False, True)]
        mejor = min([mejor] + extras, key=costo)
    return mejor


def _asignar(plan: PlanEntrada, predicciones: list[Prediccion], fecha_inicio: date,
             ocupadas: list[Sesion], unidad: int, primero_unicas: bool = False, reglas: bool = True) -> ResultadoAsignacion:
    libres = {d.dia: _bloques_dia(d, plan.max_horas_dia, [s for s in ocupadas if s.dia == d.dia])
              for d in plan.disponibilidad if d.dia >= fecha_inicio}
    libres0 = {d: list(b) for d, b in libres.items()}
    max_bloques = plan.duracion_sesion // BLOQUE_MIN
    colocado: dict[int, dict[date, list[int]]] = {}
    fijas: set[int] = set()                                    # actividades que quedaron en una sola sesión: no se mueven
    faltantes: dict[int, int] = {}
    totales: dict[int, int] = {}

    def es_unica(i: int):                                      # prioridad media o baja y cabe en una sesión
        a = plan.actividades[i]
        return reglas and a.prioridad != Nivel.ALTA and max(1, int(a.horas_estimadas * 60 / BLOQUE_MIN + 0.5)) <= max_bloques

    orden = _ordenar(plan.actividades, predicciones)
    if primero_unicas:
        orden.sort(key=lambda i: not es_unica(i))              # estable: dentro de cada grupo sigue el orden por prioridad

    for i in orden:
        a, p = plan.actividades[i], predicciones[i]
        total = max(1, int(a.horas_estimadas * 60 / BLOQUE_MIN + 0.5))
        totales[i] = total
        ventana = sorted(d for d, b in libres.items() if b and d < a.fecha_limite)

        unica = None
        if es_unica(i):                                        # una sola sesión
            carga = {d: len(libres0[d]) - len(libres[d]) for d in libres}
            unica = _ubicar_unica(total, ventana, libres, carga, p.perfil)
        if unica is not None:
            dia, bloques = unica
            colocado[i] = {dia: bloques}
            libres[dia] = [b for b in libres[dia] if b not in set(bloques)]
            fijas.add(i)
            faltantes[i] = 0
            continue

        tramos = _dividir_dias(ventana)
        piezas = {d: _parejas(libres[d], unidad) for d in ventana}
        enteras, resto = divmod(total, unidad)
        caben = [sum(len(piezas[d]) for d in t) for t in tramos]
        puestos, sin_ubicar = _ajustar_a_capacidad(_repartir_tramos(enteras, p.perfil), caben)

        tomados: dict[date, list[int]] = {}
        for tramo, n in zip(tramos, puestos):
            for dia, k in _repartir_en_dias(n, {d: len(piezas[d]) for d in tramo}).items():
                if k:
                    tomados[dia] = [b for pieza in piezas[dia][:k] for b in pieza]
                    libres[dia] = [b for b in libres[dia] if b not in set(tomados[dia])]
        faltantes[i] = _completar(sin_ubicar * unidad + resto, ventana, libres, tomados)
        colocado[i] = tomados

    if reglas and max_bloques >= SESION_MIN // BLOQUE_MIN:
        colocado = _limpiar_cortas(colocado, libres0, fijas, max_bloques)

    sesiones, resumen = [], []
    for i in _ordenar(plan.actividades, predicciones):
        a, p = plan.actividades[i], predicciones[i]
        for dia, bloques in sorted(colocado[i].items()):
            if bloques:
                sesiones += _a_sesiones(a.nombre, dia, sorted(bloques), max_bloques)
        faltan = faltantes[i]
        asignados = totales[i] - faltan
        estado = (EstadoAsignacion.ASIGNADA if faltan == 0 else
                  EstadoAsignacion.NO_ASIGNADA if asignados == 0 else EstadoAsignacion.PARCIAL)
        resumen.append(ResumenActividad(
            nombre=a.nombre, prioridad=p.prioridad, horas_solicitadas=a.horas_estimadas,
            horas_asignadas=asignados * BLOQUE_MIN / 60, horas_faltantes=faltan * BLOQUE_MIN / 60, estado=estado))

    sesiones.sort(key=lambda s: (s.dia, s.inicio))
    return ResultadoAsignacion(sesiones=sesiones, actividades=resumen)
