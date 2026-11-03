"""Validación de coherencia del plan (R1-R7). Ver StudyFlow_CapaValidacion.ipynb, paso 4."""
from collections import Counter
from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from studyflow.disponibilidad import capacidad_dia, capacidad_entre, dias_con_capacidad
from studyflow.models import Nivel, PlanEntrada

MARGEN_HORAS = 0.0
VENTANA_MINIMA_DIAS = 3
_ORDEN_PRIORIDAD = {Nivel.BAJA: 0, Nivel.MEDIA: 1, Nivel.ALTA: 2}


class Estado(str, Enum):
    FACTIBLE = "factible"
    CON_ADVERTENCIAS = "factible_con_advertencias"
    INVIABLE = "inviable"


class Hallazgo(BaseModel):
    codigo: str
    mensaje: str
    datos: dict[str, Any] = Field(default_factory=dict)


class Resultado(BaseModel):
    estado: Estado
    errores: list[Hallazgo]
    advertencias: list[Hallazgo]


def _sugerir_recorte(vencen, deficit):
    candidatas = sorted(vencen, key=lambda a: (_ORDEN_PRIORIDAD[a.prioridad], -a.horas_estimadas))
    elegidas, recortado = [], 0.0
    for a in candidatas:
        if recortado >= deficit:
            break
        elegidas.append({"nombre": a.nombre, "horas": a.horas_estimadas})
        recortado += a.horas_estimadas
    return elegidas


def validar(plan: PlanEntrada, fecha_inicio: date, modo: Literal["nuevo", "replanificacion"] = "nuevo") -> Resultado:
    errores: list[Hallazgo] = []
    advertencias: list[Hallazgo] = []
    disp, tope = plan.disponibilidad, plan.max_horas_dia
    vigentes = [a for a in plan.actividades if a.fecha_limite >= fecha_inicio]

    if tope is not None and plan.duracion_sesion / 60 > tope:  # R3a
        errores.append(Hallazgo(
            codigo="R3A_SESION_MAYOR_MAXIMO",
            mensaje=f"La sesión máxima ({int(plan.duracion_sesion)} min) supera el máximo diario ({tope:g} h)",
            datos={"duracion_sesion_min": int(plan.duracion_sesion), "max_horas_dia": tope}))

    for a in plan.actividades:  # R4
        if a.fecha_limite < fecha_inicio:
            errores.append(Hallazgo(
                codigo="R4_FECHA_PASADA",
                mensaje=f"'{a.nombre}' vence el {a.fecha_limite}, antes del inicio del plan ({fecha_inicio})",
                datos={"actividad": a.nombre, "fecha_limite": a.fecha_limite.isoformat()}))

    pasados = sorted(d.dia for d in disp if d.dia < fecha_inicio)  # R5
    if pasados:
        advertencias.append(Hallazgo(
            codigo="R5_DISPONIBILIDAD_PASADA",
            mensaje=f"Se ignoran {len(pasados)} día(s) de disponibilidad anteriores al inicio del plan",
            datos={"dias": [d.isoformat() for d in pasados]}))

    repetidos = sorted(n for n, c in Counter(a.nombre.casefold() for a in plan.actividades).items() if c > 1)  # R6
    if repetidos:
        advertencias.append(Hallazgo(
            codigo="R6_NOMBRE_DUPLICADO",
            mensaje=f"Hay actividades con el mismo nombre: {', '.join(repetidos)}",
            datos={"nombres": repetidos}))

    for d in sorted(disp, key=lambda d: d.dia):  # R3b
        if d.dia >= fecha_inicio and d.franjas and capacidad_dia(d) == 0:
            advertencias.append(Hallazgo(
                codigo="R3B_DIA_SIN_CAPACIDAD",
                mensaje=f"El {d.dia} no aporta horas: todas sus franjas duran menos de 30 min",
                datos={"dia": d.dia.isoformat()}))

    for a in vigentes:  # R2
        n = dias_con_capacidad(disp, tope, fecha_inicio, a.fecha_limite)
        if n < VENTANA_MINIMA_DIAS:
            hallazgo = Hallazgo(
                codigo="R2_VENTANA_MINIMA",
                mensaje=f"'{a.nombre}' tiene {n} día(s) con disponibilidad antes de la entrega; el mínimo es {VENTANA_MINIMA_DIAS}",
                datos={"actividad": a.nombre, "dias_con_disponibilidad": n, "minimo": VENTANA_MINIMA_DIAS})
            (errores if modo == "nuevo" else advertencias).append(hallazgo)

    for limite in sorted({a.fecha_limite for a in vigentes}):  # R1 (cubre R7)
        vencen = [a for a in vigentes if a.fecha_limite <= limite]
        carga = sum(a.horas_estimadas for a in vencen)
        capacidad = capacidad_entre(disp, tope, fecha_inicio, limite)
        deficit = carga - (capacidad + MARGEN_HORAS)
        if deficit > 1e-9:
            errores.append(Hallazgo(
                codigo="R1_CAPACIDAD_INSUFICIENTE",
                mensaje=f"Hasta el {limite} hay {carga:g} h de trabajo y solo {capacidad:g} h disponibles (faltan {deficit:g} h)",
                datos={"fecha_critica": limite.isoformat(), "horas_requeridas": carga, "horas_disponibles": capacidad,
                       "deficit_horas": round(deficit, 4), "sugerencia_recorte": _sugerir_recorte(vencen, deficit)}))

    estado = Estado.INVIABLE if errores else Estado.CON_ADVERTENCIAS if advertencias else Estado.FACTIBLE
    return Resultado(estado=estado, errores=errores, advertencias=advertencias)
