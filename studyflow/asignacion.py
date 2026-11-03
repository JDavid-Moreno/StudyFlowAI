"""Capa de asignación determinista: modelos y algoritmo. Ver StudyFlow_CapaAsignacion.ipynb, pasos 2 y 3."""
from datetime import date, time
from enum import Enum
from math import floor

from pydantic import BaseModel, Field, field_validator

from studyflow.disponibilidad import BLOQUE_MIN, minutos
from studyflow.models import Actividad, DisponibilidadDia, PlanEntrada

TRAMOS = 3


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


def _a_sesiones(nombre: str, dia: date, bloques: list[int], max_bloques: int):
    """Une bloques consecutivos en sesiones de a lo sumo `max_bloques` bloques."""
    sesiones, inicio, n = [], 0, 0
    for b in bloques + [None]:
        if n and (b is None or b != inicio + n * BLOQUE_MIN or n == max_bloques):
            sesiones.append(Sesion(actividad=nombre, dia=dia, inicio=_hora(inicio), fin=_hora(inicio + n * BLOQUE_MIN)))
            n = 0
        if b is not None:
            if n == 0:
                inicio = b
            n += 1
    return sesiones


def asignar(plan: PlanEntrada, predicciones: list[Prediccion], fecha_inicio: date,
            ocupadas: list[Sesion] | None = None) -> ResultadoAsignacion:
    if len(predicciones) != len(plan.actividades):
        raise ValueError("debe haber una predicción por actividad, en el mismo orden")
    ocupadas = ocupadas or []
    libres = {d.dia: _bloques_dia(d, plan.max_horas_dia, [s for s in ocupadas if s.dia == d.dia])
              for d in plan.disponibilidad if d.dia >= fecha_inicio}
    max_bloques = plan.duracion_sesion // BLOQUE_MIN
    sesiones, resumen = [], []

    for i in _ordenar(plan.actividades, predicciones):
        a, p = plan.actividades[i], predicciones[i]
        total = max(1, int(a.horas_estimadas * 60 / BLOQUE_MIN + 0.5))
        tramos = _dividir_dias(sorted(d for d, b in libres.items() if b and d < a.fecha_limite))
        caben = [sum(len(libres[d]) for d in t) for t in tramos]
        puestos, sin_ubicar = _ajustar_a_capacidad(_repartir_tramos(total, p.perfil), caben)

        for tramo, n in zip(tramos, puestos):
            for dia, k in _repartir_en_dias(n, {d: len(libres[d]) for d in tramo}).items():
                tomados, libres[dia] = libres[dia][:k], libres[dia][k:]
                sesiones += _a_sesiones(a.nombre, dia, tomados, max_bloques)

        asignados = total - sin_ubicar
        estado = (EstadoAsignacion.ASIGNADA if sin_ubicar == 0 else
                  EstadoAsignacion.NO_ASIGNADA if asignados == 0 else EstadoAsignacion.PARCIAL)
        resumen.append(ResumenActividad(
            nombre=a.nombre, prioridad=p.prioridad, horas_solicitadas=a.horas_estimadas,
            horas_asignadas=asignados * BLOQUE_MIN / 60, horas_faltantes=sin_ubicar * BLOQUE_MIN / 60, estado=estado))

    sesiones.sort(key=lambda s: (s.dia, s.inicio))
    return ResultadoAsignacion(sesiones=sesiones, actividades=resumen)
