"""Capa de órdenes: aplica cada orden confirmada al plan vigente. Ver StudyFlow_CapaOrdenes.ipynb, pasos 2 a 5."""
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from studyflow.asignacion import EstadoAsignacion, Prediccion, ResultadoAsignacion, ResumenActividad, Sesion, asignar
from studyflow.disponibilidad import BLOQUE_MIN, minutos
from studyflow.interprete import Opcion, Orden
from studyflow.llm import duracion_texto, fecha_texto
from studyflow.models import Actividad, DisponibilidadDia, Franja, PlanEntrada
from studyflow.redactor import filtrar
from studyflow.validador import validar

MEDIO_BLOQUE = BLOQUE_MIN / 60 / 2      # menos de esto pendiente no alcanza para un bloque: se da por cumplido
REALIDAD: list[Opcion] = ["abrir_tiempo", "recortar"]               # un bloqueo o una sesión no hecha ya ocurrieron: no se cancelan
DESEO: list[Opcion] = ["abrir_tiempo", "recortar", "cancelar"]      # un cambio pedido sí se puede cancelar


class Predictor(Protocol):
    """Lo único que la capa necesita del MLP (en producción, `PredictorMLP`)."""

    def predecir(self, plan: PlanEntrada, fecha_inicio: date) -> list[Prediccion]: ...


class PlanVigente(BaseModel):
    """Lo que la capa necesita de un estudiante: su plan, el horario completo (pasado y futuro) y las sesiones que fijó."""

    plan: PlanEntrada
    horario: ResultadoAsignacion
    fijadas: list[Sesion] = []


class Decision(BaseModel):
    """Un cambio que no cabe en el tiempo disponible y espera que el estudiante elija cómo resolverlo."""

    propuesto: PlanVigente          # el plan con el cambio ya aplicado; su horario sigue siendo el vigente
    opciones: list[Opcion]
    faltan_horas: float
    fecha_critica: date


class CambioHoras(BaseModel):
    """Horas totales de una actividad antes y después de un recorte (0 = se quita)."""

    actividad: str
    antes: float
    despues: float


class Recorte(BaseModel):
    """Propuesta de recorte: cuántas horas pierde cada actividad para que el plan quepa."""

    cambios: list[CambioHoras]
    alcanza: bool


class ResultadoOrden(BaseModel):
    """Lo que devuelve la capa: qué pasó, qué guardar y qué mostrar."""

    tipo: Literal["aplicada", "decision", "rechazada", "consulta", "pregunta", "cancelada"]
    mensaje: str
    vigente: PlanVigente | None = None              # aplicada: el plan nuevo que se guarda
    decision: Decision | None = None                # decision / pregunta: la decisión que queda pendiente
    horario: ResultadoAsignacion | None = None      # aplicada: el horario completo; consulta: la vista pedida
    avisos: list[str] = []


# --- Utilidades de tiempo y franjas ---------------------------------------------------------------
def _hora(m: int) -> time:
    """Minutos del día -> hora."""
    return time(m // 60, m % 60)


def _dur(s: Sesion) -> int:
    """Duración de una sesión en minutos."""
    return minutos(s.fin) - minutos(s.inicio)


def _horas(sesiones) -> float:
    """Horas totales de un grupo de sesiones."""
    return sum(map(_dur, sesiones)) / 60


def _h(horas: float) -> str:
    """Horas en texto ("1 h 30 min")."""
    return duracion_texto(round(horas * 60))


def _pasada(s: Sesion, ahora: datetime) -> bool:
    """Una sesión que ya empezó cuenta como hecha y no se replanifica."""
    return datetime.combine(s.dia, s.inicio) < ahora


def _corte(ahora: datetime) -> int:
    """Primer minuto planificable de hoy: la hora actual redondeada al siguiente bloque de 30 min."""
    m = ahora.hour * 60 + ahora.minute + (1 if ahora.second or ahora.microsecond else 0)
    return min(ceil(m / BLOQUE_MIN) * BLOQUE_MIN, 24 * 60)


def _traslapan(a: Sesion, b: Sesion) -> bool:
    """Dos sesiones del mismo día que comparten algún minuto."""
    return a.dia == b.dia and minutos(a.inicio) < minutos(b.fin) and minutos(b.inicio) < minutos(a.fin)


def _restar(franjas: list[Franja], ini: int, fin: int) -> list[Franja]:
    """Quita el rango [ini, fin) de las franjas, partiéndolas si hace falta."""
    out = []
    for f in sorted(franjas, key=lambda f: f.inicio):
        a, b = minutos(f.inicio), minutos(f.fin)
        if fin <= a or b <= ini:
            out.append(f)
            continue
        if a < ini:
            out.append(Franja(inicio=_hora(a), fin=_hora(ini)))
        if fin < b:
            out.append(Franja(inicio=_hora(fin), fin=_hora(b)))
    return out


def _unir(rangos: list[tuple[int, int]]) -> list[list[int]]:
    """Une rangos [a, b) que se tocan o se traslapan."""
    unidos = []
    for a, b in sorted(rangos):
        if unidos and a <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], b)
        else:
            unidos.append([a, b])
    return unidos


def _sumar(franjas: list[Franja], ini: int, fin: int) -> list[Franja]:
    """Agrega el rango [ini, fin) a las franjas del día."""
    return [Franja(inicio=_hora(a), fin=_hora(b)) for a, b in _unir([(minutos(f.inicio), minutos(f.fin)) for f in franjas] + [(ini, fin)])]


def _cubre(franjas: list[Franja], ini: int, fin: int) -> bool:
    """Indica si el rango [ini, fin) cabe entero dentro de la disponibilidad del día."""
    return any(a <= ini and fin <= b for a, b in _unir([(minutos(f.inicio), minutos(f.fin)) for f in franjas]))


def _libres(franjas: list[Franja], ocupadas: list[Sesion], piso: int = 0) -> list[Franja]:
    """Franjas del día sin lo ocupado y sin lo anterior a `piso`."""
    libres = _restar(franjas, 0, piso) if piso else list(franjas)
    for s in ocupadas:
        libres = _restar(libres, minutos(s.inicio), minutos(s.fin))
    return libres


def _rangos_texto(franjas: list[Franja]) -> str:
    """Franjas en texto ("18:00 a 21:00 y 22:00 a 23:00")."""
    partes = [f"{f.inicio:%H:%M} a {f.fin:%H:%M}" for f in franjas]
    return ", ".join(partes[:-1]) + " y " + partes[-1] if len(partes) > 1 else partes[0]


def _dias(dia: date, ultimo: date | None) -> list[date]:
    """Días de una orden, del primero al último (inclusivo)."""
    return [dia + timedelta(days=k) for k in range(((ultimo or dia) - dia).days + 1)]


def _con_franjas(plan: PlanEntrada, nuevas: dict[date, list[Franja]]) -> PlanEntrada:
    """Copia del plan con la disponibilidad de algunos días reemplazada (los días sin franjas se quitan)."""
    por_dia = {d.dia: d.franjas for d in plan.disponibilidad} | nuevas
    return plan.model_copy(update={"disponibilidad": [DisponibilidadDia(dia=d, franjas=f) for d, f in sorted(por_dia.items()) if f]})


def _actividad(plan: PlanEntrada, nombre: str | None) -> Actividad | None:
    """La actividad del plan con ese nombre, o None."""
    return next((a for a in plan.actividades if a.nombre == nombre), None)


def _con_actividad(plan: PlanEntrada, nombre: str, **cambios) -> PlanEntrada:
    """Copia del plan con una actividad modificada."""
    return plan.model_copy(update={"actividades": [a.model_copy(update=cambios) if a.nombre == nombre else a for a in plan.actividades]})


def _resumen(plan: PlanEntrada, sesiones: list[Sesion], prioridades: dict[str, float]) -> ResultadoAsignacion:
    """Horario completo: las sesiones ordenadas y el resumen por actividad recalculado a partir de ellas."""
    sesiones = sorted(sesiones, key=lambda s: (s.dia, s.inicio))
    filas = []
    for a in plan.actividades:
        asignadas = _horas([s for s in sesiones if s.actividad == a.nombre])
        faltan = a.horas_estimadas - asignadas
        faltan = 0.0 if faltan < MEDIO_BLOQUE - 1e-9 else round(faltan, 6)
        estado = (EstadoAsignacion.ASIGNADA if faltan == 0 else
                  EstadoAsignacion.NO_ASIGNADA if asignadas == 0 else EstadoAsignacion.PARCIAL)
        filas.append(ResumenActividad(nombre=a.nombre, prioridad=prioridades.get(a.nombre, 0.0), horas_solicitadas=a.horas_estimadas,
                                      horas_asignadas=asignadas, horas_faltantes=faltan, estado=estado))
    return ResultadoAsignacion(sesiones=sesiones, actividades=sorted(filas, key=lambda x: -x.prioridad))


def _rechazo(mensaje: str, decision: Decision | None = None) -> ResultadoOrden:
    """Nada cambia; el estudiante recibe el porqué (si había una decisión pendiente, sigue pendiente)."""
    return ResultadoOrden(tipo="rechazada", mensaje=mensaje, decision=decision)


class Preparado(BaseModel):
    """Todo lo que la replanificación calcula antes de llamar al validador, al MLP y a la asignación."""

    historial: list[Sesion]          # sesiones que ya empezaron: no se tocan
    fijadas: list[Sesion]            # sesiones futuras que el estudiante fijó y siguen siendo válidas
    soltadas: list[Sesion]           # fijadas que ya no caben en el plan y se liberan
    pendientes: list[Actividad]      # actividades con horas por planificar (horas_estimadas = lo que falta)
    hechas: dict[str, float]         # horas ya cumplidas o fijadas por actividad
    asignable: PlanEntrada | None    # plan para la asignación: lo pendiente, desde ahora
    capacidad: PlanEntrada | None    # el mismo, sin el tiempo ocupado: para el validador y el MLP
    ocupadas: list[Sesion]           # lo que la asignación debe respetar: fijadas e historial de hoy


def _fijadas_validas(fijadas: list[Sesion], plan: PlanEntrada) -> tuple[list[Sesion], list[Sesion]]:
    """Separa las sesiones fijadas que siguen cumpliendo el plan de las que hay que liberar."""
    por_nombre = {a.nombre: a for a in plan.actividades}
    franjas = {d.dia: d.franjas for d in plan.disponibilidad}
    ok, fuera = [], []
    for s in sorted(fijadas, key=lambda s: (s.dia, s.inicio)):
        a = por_nombre.get(s.actividad)
        valida = (a is not None and s.dia < a.fecha_limite and _dur(s) <= plan.duracion_sesion
                  and _cubre(franjas.get(s.dia, []), minutos(s.inicio), minutos(s.fin)) and not any(_traslapan(s, x) for x in ok))
        (ok if valida else fuera).append(s)
    if plan.max_horas_dia is not None:
        for d in sorted({s.dia for s in ok}):
            del_dia = [s for s in ok if s.dia == d]
            if _horas(del_dia) > plan.max_horas_dia + 1e-9:
                fuera += del_dia
                ok = [s for s in ok if s.dia != d]
    return ok, [s for s in fuera if s.actividad in por_nombre]


def _preparar(base: PlanVigente, plan: PlanEntrada, fijadas: list[Sesion], ahora: datetime) -> Preparado:
    """Separa lo hecho de lo pendiente y arma los dos planes de trabajo (B1, B3 y B5)."""
    hoy = ahora.date()
    nombres = {a.nombre for a in plan.actividades}
    historial = [s for s in base.horario.sesiones if _pasada(s, ahora) and s.actividad in nombres]
    fijadas, soltadas = _fijadas_validas([s for s in fijadas if not _pasada(s, ahora)], plan)
    hechas = {a.nombre: _horas([s for s in historial + fijadas if s.actividad == a.nombre]) for a in plan.actividades}
    pendientes = [a.model_copy(update={"horas_estimadas": round(a.horas_estimadas - hechas[a.nombre], 6)}) for a in plan.actividades
                  if a.fecha_limite > hoy and a.horas_estimadas - hechas[a.nombre] >= MEDIO_BLOQUE - 1e-9]
    ocupadas = fijadas + [s for s in historial if s.dia == hoy]
    asignable = capacidad = None
    if pendientes:
        disp, libre = [], []
        for d in plan.disponibilidad:
            if d.dia < hoy:
                continue
            frs = _restar(d.franjas, 0, _corte(ahora)) if d.dia == hoy else list(d.franjas)
            disp.append(DisponibilidadDia(dia=d.dia, franjas=frs))
            libre.append(DisponibilidadDia(dia=d.dia, franjas=_libres(frs, [s for s in ocupadas if s.dia == d.dia])))
        vacio = [DisponibilidadDia(dia=hoy, franjas=[])]
        asignable = plan.model_copy(update={"actividades": pendientes, "disponibilidad": disp or vacio})
        capacidad = plan.model_copy(update={"actividades": pendientes, "disponibilidad": libre or vacio})
    return Preparado(historial=historial, fijadas=fijadas, soltadas=soltadas, pendientes=pendientes, hechas=hechas,
                     asignable=asignable, capacidad=capacidad, ocupadas=ocupadas)


def _texto_error(e) -> str:
    """Mensaje para el estudiante a partir de un error del validador que no se resuelve abriendo tiempo ni recortando."""
    d = e.datos
    if e.codigo == "R2_VENTANA_MINIMA":
        return (f"{d['actividad']} necesita al menos {d['minimo']} días con tiempo de estudio antes de su entrega "
                f"y tiene {d['dias_con_disponibilidad']}. Cambia su fecha límite o abre tiempo antes.")
    if e.codigo == "R3A_SESION_MAYOR_MAXIMO":
        return f"Cada sesión puede durar hasta {d['duracion_sesion_min']} min, más que tu máximo diario ({_h(d['max_horas_dia'])}). Ajusta uno de los dos."
    return e.mensaje + "."


def _texto_decision(d: Decision) -> str:
    """El mensaje que presenta la decisión y sus opciones."""
    nombres = {"abrir_tiempo": "abrir más tiempo (más horas otros días o un máximo diario mayor)",
               "recortar": "recortar horas (dime qué actividades quieres mantener intactas)",
               "cancelar": "cancelar el cambio"}
    opciones = [nombres[o] for o in d.opciones]
    lista = ", ".join(opciones[:-1]) + " o " + opciones[-1] if len(opciones) > 1 else opciones[0]
    return f"Con este cambio no alcanza el tiempo: faltan {_h(d.faltan_horas)} antes del {fecha_texto(d.fecha_critica)}. Puedes {lista}."


def _asignar(p: Preparado, predicciones: list[Prediccion], hoy: date) -> ResultadoAsignacion:
    """Asigna en el orden del MLP; si una actividad queda incompleta, la adelanta y reintenta; al final prueba por fecha límite."""
    nombres = [a.nombre for a in p.pendientes]
    incompletas = lambda r: [x.nombre for x in r.actividades if x.estado != EstadoAsignacion.ASIGNADA]
    adelantadas: list[str] = []
    for _ in range(len(nombres)):
        orden = [Prediccion(prioridad=(x.prioridad + 1) / 2 if n in adelantadas else x.prioridad / 2, perfil=x.perfil)
                 for n, x in zip(nombres, predicciones)]
        r = asignar(p.asignable, orden, hoy, p.ocupadas)
        nuevas = [n for n in incompletas(r) if n not in adelantadas]
        if not nuevas:
            break
        adelantadas += nuevas
    if incompletas(r):
        fechas = sorted(range(len(nombres)), key=lambda i: (p.pendientes[i].fecha_limite, -predicciones[i].prioridad))
        rango = {i: 1 - k / len(nombres) for k, i in enumerate(fechas)}
        por_fecha = asignar(p.asignable, [Prediccion(prioridad=rango[i], perfil=x.perfil) for i, x in enumerate(predicciones)], hoy, p.ocupadas)
        if len(incompletas(por_fecha)) < len(incompletas(r)):
            r = por_fecha
    mlp = dict(zip(nombres, (x.prioridad for x in predicciones)))          # el resumen muestra la prioridad que dio el MLP
    return r.model_copy(update={"actividades": [x.model_copy(update={"prioridad": mlp[x.nombre]}) for x in r.actividades]})


def _replanificar(base: PlanVigente, plan: PlanEntrada, fijadas: list[Sesion], ahora: datetime, mlp: Predictor,
                  opciones: list[Opcion], nuevas: frozenset[str] = frozenset(), avisos: list[str] | None = None) -> ResultadoOrden:
    """Vuelve a calcular lo pendiente con validador -> MLP -> asignación; si no cabe todo, abre una decisión (R1)."""
    try:
        plan = PlanEntrada.model_validate(plan.model_dump())
    except ValidationError:
        return _rechazo("Ese cambio deja el plan sin actividades o sin disponibilidad; crea un plan nuevo en su lugar.")
    hoy = ahora.date()
    p = _preparar(base, plan, fijadas, ahora)
    avisos = list(avisos or []) + [f"La sesión fijada de {s.actividad} del {fecha_texto(s.dia)} a las {s.inicio:%H:%M} "
                                   "se liberó porque ya no cabe en tu plan." for s in p.soltadas]
    prioridades = {x.nombre: x.prioridad for x in base.horario.actividades}

    if p.pendientes:
        v = validar(p.capacidad, hoy, "replanificacion")
        errores = list(v.errores)
        recientes = [a for a in p.pendientes if a.nombre in nuevas]
        if recientes:
            errores += [e for e in validar(p.capacidad.model_copy(update={"actividades": recientes}), hoy, "nuevo").errores
                        if e.codigo == "R2_VENTANA_MINIMA"]
        otros = [e for e in errores if e.codigo != "R1_CAPACIDAD_INSUFICIENTE"]
        if otros:
            return _rechazo(" ".join(dict.fromkeys(_texto_error(e) for e in otros)))
        faltan = fecha = None
        if errores:
            faltan, fecha = errores[0].datos["deficit_horas"], date.fromisoformat(errores[0].datos["fecha_critica"])
        else:
            r = _asignar(p, mlp.predecir(p.capacidad, hoy), hoy)
            incompletas = {x.nombre: x.horas_faltantes for x in r.actividades if x.estado != EstadoAsignacion.ASIGNADA}
            if incompletas:
                faltan = sum(incompletas.values())
                fecha = min(a.fecha_limite for a in p.pendientes if a.nombre in incompletas)
            nuevas_sesiones = r.sesiones
            prioridades |= {x.nombre: x.prioridad for x in r.actividades}
        if faltan is not None:
            d = Decision(propuesto=PlanVigente(plan=plan, horario=base.horario, fijadas=fijadas),
                         opciones=opciones, faltan_horas=faltan, fecha_critica=fecha)
            return ResultadoOrden(tipo="decision", mensaje=_texto_decision(d), decision=d, avisos=avisos)
    else:
        nuevas_sesiones = []

    horario = _resumen(plan, p.historial + p.fijadas + nuevas_sesiones, prioridades)
    vigente = PlanVigente(plan=plan, horario=horario, fijadas=p.fijadas)
    return ResultadoOrden(tipo="aplicada", mensaje="Listo, tu horario quedó actualizado.", vigente=vigente, horario=horario, avisos=avisos)


def crear_plan(plan: PlanEntrada, ahora: datetime, mlp: Predictor) -> ResultadoOrden:
    """Primer horario con el formulario completo (actividades, disponibilidad y límites)."""
    nombres = [a.nombre.casefold() for a in plan.actividades]
    if len(nombres) != len(set(nombres)):
        return _rechazo("Hay actividades con el mismo nombre; cada una debe tener un nombre distinto.")
    vacio = PlanVigente(plan=plan, horario=ResultadoAsignacion(sesiones=[], actividades=[]))
    return _replanificar(vacio, plan, [], ahora, mlp, DESEO, nuevas=frozenset(a.nombre for a in plan.actividades))


def _sin_cancelar(opciones: list[Opcion] | None) -> list[Opcion]:
    """Opciones para un cambio que ya ocurrió: no se puede cancelar."""
    return [o for o in (opciones or DESEO) if o != "cancelar"]


def _no_existe(nombre: str | None, plan: PlanEntrada) -> ResultadoOrden:
    """Rechazo para una actividad que no está en el plan."""
    return _rechazo(f"No encontré {nombre} en tu plan. Tus actividades: {', '.join(a.nombre for a in plan.actividades)}.")


def _cambiar_horas(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Nuevo total de horas: lo ya hecho o fijado se descuenta y se replanifica el resto."""
    a = _actividad(base.plan, o.actividad)
    if a is None:
        return _no_existe(o.actividad, base.plan)
    hechas = _preparar(base, base.plan, base.fijadas, ahora).hechas[a.nombre]
    if o.horas < hechas - 1e-9:
        return _rechazo(f"{a.nombre} ya tiene {_h(hechas)} estudiadas o fijadas; el nuevo total no puede ser menor.")
    return _replanificar(base, _con_actividad(base.plan, a.nombre, horas_estimadas=o.horas), base.fijadas, ahora, mlp, opciones or DESEO)


def _cambiar_fecha(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Nueva fecha límite; las sesiones fijadas que queden después se liberan."""
    a = _actividad(base.plan, o.actividad)
    if a is None:
        return _no_existe(o.actividad, base.plan)
    return _replanificar(base, _con_actividad(base.plan, a.nombre, fecha_limite=o.dia), base.fijadas, ahora, mlp, opciones or DESEO)


def _cambiar_nivel(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Nueva prioridad y/o dificultad: cambian las entradas del MLP y se replanifica."""
    a = _actividad(base.plan, o.actividad)
    if a is None:
        return _no_existe(o.actividad, base.plan)
    cambios = {k: v for k, v in (("prioridad", o.prioridad), ("dificultad", o.dificultad)) if v is not None}
    return _replanificar(base, _con_actividad(base.plan, a.nombre, **cambios), base.fijadas, ahora, mlp, opciones or DESEO)


def _agregar(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Suma actividades y replanifica todo lo no fijado (3A); las nuevas deben tener su ventana mínima."""
    existentes = {a.nombre.casefold() for a in base.plan.actividades}
    repetidas = [a.nombre for a in o.actividades if a.nombre.casefold() in existentes]
    if repetidas:
        return _rechazo(f"Ya tienes una actividad llamada {repetidas[0]}. Usa otro nombre.")
    plan = base.plan.model_copy(update={"actividades": base.plan.actividades + o.actividades})
    return _replanificar(base, plan, base.fijadas, ahora, mlp, opciones or DESEO, nuevas=frozenset(a.nombre for a in o.actividades))


def _quitar(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Quita la actividad y sus sesiones (también las pasadas) sin mover las demás (4A)."""
    a = _actividad(base.plan, o.actividad)
    if a is None:
        return _no_existe(o.actividad, base.plan)
    if len(base.plan.actividades) == 1:
        return _rechazo(f"{a.nombre} es tu única actividad. Si ya terminaste, crea un plan nuevo.")
    plan = base.plan.model_copy(update={"actividades": [x for x in base.plan.actividades if x.nombre != a.nombre]})
    sesiones = [s for s in base.horario.sesiones if s.actividad != a.nombre]
    horario = _resumen(plan, sesiones, {x.nombre: x.prioridad for x in base.horario.actividades})
    fijadas = [s for s in base.fijadas if s.actividad != a.nombre]
    vigente = PlanVigente(plan=plan, horario=horario, fijadas=fijadas)
    if opciones:      # había una decisión pendiente: quitar una actividad puede resolverla, así que se revisa
        return _replanificar(vigente, plan, fijadas, ahora, mlp, opciones)
    return ResultadoOrden(tipo="aplicada", mensaje=f"Listo, quité {a.nombre} y sus sesiones.", vigente=vigente, horario=horario)


def _bloquear(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Quita disponibilidad (uno o varios días, completos o un rango de horas) y replanifica; no se puede cancelar."""
    por_dia = {d.dia: d.franjas for d in base.plan.disponibilidad}
    nuevas = {}
    for d in _dias(o.dia, o.dia_fin):
        if d in por_dia:
            nuevas[d] = [] if o.hora_inicio is None else _restar(por_dia[d], minutos(o.hora_inicio), minutos(o.hora_fin))
    if all(nuevas[d] == por_dia[d] for d in nuevas):
        return _rechazo("En ese horario no tenías tiempo de estudio, así que no hay nada que bloquear.")
    plan = _con_franjas(base.plan, nuevas)
    if not plan.disponibilidad:
        return _rechazo("Con ese bloqueo no te queda ningún día con tiempo de estudio. Abre tiempo en otros días o crea un plan nuevo.")
    return _replanificar(base, plan, base.fijadas, ahora, mlp, _sin_cancelar(opciones))


def _abrir(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Agrega disponibilidad en uno o varios días (se une con la que ya había) y replanifica."""
    por_dia = {d.dia: d.franjas for d in base.plan.disponibilidad}
    nuevas = {d: _sumar(por_dia.get(d, []), minutos(o.hora_inicio), minutos(o.hora_fin)) for d in _dias(o.dia, o.dia_fin)}
    if all(nuevas[d] == por_dia.get(d) for d in nuevas):
        return _rechazo("Ya tenías ese tiempo disponible.", decision=None)
    return _replanificar(base, _con_franjas(base.plan, nuevas), base.fijadas, ahora, mlp, opciones or DESEO)


def _limites(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Nuevo máximo de horas por día y/o duración máxima de sesión; las fijadas que ya no cumplan se liberan."""
    maximo = None if o.sin_maximo_diario else (o.max_horas_dia if o.max_horas_dia is not None else base.plan.max_horas_dia)
    duracion = o.duracion_sesion or base.plan.duracion_sesion
    if (maximo, duracion) == (base.plan.max_horas_dia, base.plan.duracion_sesion):
        return _rechazo("Tus límites ya eran esos.")
    plan = base.plan.model_copy(update={"max_horas_dia": maximo, "duracion_sesion": duracion})
    return _replanificar(base, plan, base.fijadas, ahora, mlp, opciones or DESEO)


def _mover(base: PlanVigente, o: Orden, ahora: datetime, mlp: Predictor, opciones) -> ResultadoOrden:
    """Fija las sesiones del día de origen en el destino (a la hora pedida o en el primer hueco) y replanifica el resto."""
    plan, destino = base.plan, o.dia_destino
    movidas = sorted([s for s in base.horario.sesiones if s.dia == o.dia_origen and not _pasada(s, ahora)
                      and o.actividad in (None, s.actividad)], key=lambda s: s.inicio)
    de = f" de {o.actividad}" if o.actividad else ""
    if not movidas:
        return _rechazo(f"No tienes sesiones{de} pendientes el {fecha_texto(o.dia_origen)}.")
    limite = min(_actividad(plan, s.actividad).fecha_limite for s in movidas)
    if destino >= limite:
        return _rechazo(f"El {fecha_texto(destino)} ya no sirve: la entrega es el {fecha_texto(limite)}. Elige un día anterior.")
    franjas = next((d.franjas for d in plan.disponibilidad if d.dia == destino), [])
    if not franjas:
        return _rechazo(f"El {fecha_texto(destino)} no tienes tiempo de estudio. Ábrelo primero, por ejemplo: ese día puedo de 18:00 a 21:00.")
    resto = [s for s in base.fijadas if s not in movidas]
    ocupadas = [s for s in resto if s.dia == destino] + [s for s in base.horario.sesiones if s.dia == destino and _pasada(s, ahora)]
    total = sum(map(_dur, movidas))
    if plan.max_horas_dia is not None and _horas(ocupadas) + total / 60 > plan.max_horas_dia + 1e-9:
        return _rechazo(f"Con eso el {fecha_texto(destino)} pasaría tu máximo de {_h(plan.max_horas_dia)} de estudio.")
    piso = _corte(ahora) if destino == ahora.date() else 0

    def colocar(inicio: int) -> list[Sesion] | None:
        """Las sesiones movidas una tras otra desde `inicio`, o None si no caben ahí."""
        out, cursor = [], inicio
        for s in movidas:
            nueva = Sesion(actividad=s.actividad, dia=destino, inicio=_hora(cursor), fin=_hora(cursor + _dur(s)))
            if cursor < piso or cursor + _dur(s) >= 24 * 60 or not _cubre(franjas, cursor, cursor + _dur(s)) \
                    or any(_traslapan(nueva, x) for x in ocupadas):
                return None
            out.append(nueva)
            cursor += _dur(s)
        return out

    candidatos = [minutos(o.hora_inicio)] if o.hora_inicio else \
        sorted({m for f in franjas for m in range(minutos(f.inicio), minutos(f.fin), BLOQUE_MIN)})
    fijas = next((x for x in map(colocar, candidatos) if x), None)
    if fijas is None:
        libres = _libres(franjas, ocupadas, piso)
        donde = f" a las {o.hora_inicio:%H:%M}" if o.hora_inicio else ""
        return _rechazo(f"No cabe{'n' if len(movidas) > 1 else ''} {_h(total / 60)}{donde} el {fecha_texto(destino)}. "
                        + (f"Ese día tienes libre de {_rangos_texto(libres)}." if libres else "Ese día ya no tienes tiempo libre."))
    return _replanificar(base, plan, resto + fijas, ahora, mlp, opciones or DESEO)


ORDENES = {"cambiar_horas": _cambiar_horas, "cambiar_fecha_limite": _cambiar_fecha, "cambiar_nivel": _cambiar_nivel,
           "agregar_actividad": _agregar, "quitar_actividad": _quitar, "bloquear_dia": _bloquear, "abrir_tiempo": _abrir,
           "cambiar_limites": _limites, "mover_sesion": _mover}


def aplicar(vigente: PlanVigente | None, orden: Orden, ahora: datetime, mlp: Predictor, decision: Decision | None = None) -> ResultadoOrden:
    """Aplica una orden ya confirmada. Con una decisión pendiente, la orden se suma al cambio propuesto."""
    if orden.accion == "crear_plan":
        return _rechazo("El plan nuevo se crea con el formulario, con tus actividades y tu disponibilidad.")
    if vigente is None:
        return _rechazo("Todavía no tienes un plan. Créalo primero con tus actividades y tu disponibilidad.")
    if orden.accion == "consultar_horario":
        return ResultadoOrden(tipo="consulta", mensaje="", horario=filtrar(vigente.horario, dia=orden.dia, actividad=orden.actividad))
    if orden.accion == "responder_decision":
        return _responder(decision, orden, ahora, mlp)
    base = decision.propuesto if decision else vigente
    resultado = ORDENES[orden.accion](base, orden, ahora, mlp, decision.opciones if decision else None)
    if decision and resultado.tipo == "rechazada":
        resultado.decision = decision          # lo rechazado no cierra la decisión que estaba pendiente
    return resultado


def marcar_no_hecha(vigente: PlanVigente, sesion: Sesion, ahora: datetime, mlp: Predictor, decision: Decision | None = None) -> ResultadoOrden:
    """Botón no la hice (B4): la sesión pasada sale del historial, sus horas vuelven a lo pendiente y se replanifica."""
    base = decision.propuesto if decision else vigente
    if sesion not in base.horario.sesiones or not _pasada(sesion, ahora):
        return _rechazo("Solo puedes marcar como no hecha una sesión de tu horario que ya pasó.", decision)
    horario = base.horario.model_copy(update={"sesiones": [s for s in base.horario.sesiones if s != sesion]})
    base = base.model_copy(update={"horario": horario, "fijadas": [s for s in base.fijadas if s != sesion]})
    return _replanificar(base, base.plan, base.fijadas, ahora, mlp, _sin_cancelar(decision.opciones if decision else None),
                         avisos=[f"Las {_h(_dur(sesion) / 60)} de {sesion.actividad} volvieron a lo pendiente."])


MAX_VUELTAS_RECORTE = 40


def _con_recorte(plan: PlanEntrada, recorte: Recorte) -> PlanEntrada:
    """Copia del plan con las horas recortadas; una actividad que queda en 0 h se quita."""
    nuevas = {c.actividad: c.despues for c in recorte.cambios}
    actividades = [a.model_copy(update={"horas_estimadas": nuevas[a.nombre]}) if a.nombre in nuevas else a
                   for a in plan.actividades if nuevas.get(a.nombre, 1) > 0]
    return plan.model_copy(update={"actividades": actividades})


def proponer_recorte(decision: Decision, protegidas: list[str], ahora: datetime, mlp: Predictor) -> Recorte:
    """Cuántas horas quitar a cada actividad no protegida para que el plan quepa, empezando por la de menor prioridad del MLP."""
    base = decision.propuesto
    originales = {a.nombre: a.horas_estimadas for a in base.plan.actividades}
    recorte = Recorte(cambios=[], alcanza=False)
    for _ in range(MAX_VUELTAS_RECORTE):
        plan = _con_recorte(base.plan, recorte)
        r = _replanificar(base, plan, base.fijadas, ahora, mlp, decision.opciones)
        if r.tipo == "aplicada":
            recorte.alcanza = True
            return recorte
        if r.tipo != "decision":
            return recorte
        p = _preparar(base, plan, base.fijadas, ahora)
        prioridad = dict(zip([a.nombre for a in p.pendientes], [x.prioridad for x in mlp.predecir(p.capacidad, ahora.date())]))
        candidatas = sorted([a for a in p.pendientes if a.nombre not in protegidas and a.fecha_limite <= r.decision.fecha_critica],
                            key=lambda a: (prioridad[a.nombre], -a.horas_estimadas))
        if not candidatas:
            return recorte
        falta = ceil(r.decision.faltan_horas / MEDIO_BLOQUE / 2 - 1e-9) * MEDIO_BLOQUE * 2      # en bloques de 30 min
        cambios, quitadas = {c.actividad: c for c in recorte.cambios}, 0
        for a in candidatas:
            if falta <= 1e-9:
                break
            quita = min(a.horas_estimadas, falta)
            queda = a.horas_estimadas - quita
            if queda < MEDIO_BLOQUE:
                quita, queda = a.horas_estimadas, 0.0
            falta -= quita
            total = round(p.hechas[a.nombre] + queda, 6)
            if total < MEDIO_BLOQUE:
                if len(plan.actividades) - quitadas <= 1:
                    return recorte          # no se puede quitar la última actividad del plan
                quitadas, total = quitadas + 1, 0.0
            cambios[a.nombre] = CambioHoras(actividad=a.nombre, antes=originales[a.nombre], despues=total)
        recorte = Recorte(cambios=list(cambios.values()), alcanza=False)
    return recorte


def texto_recorte(recorte: Recorte) -> str:
    """El recorte en palabras, para mostrarlo en la confirmación."""
    partes = [f"{c.actividad} pasa de {_h(c.antes)} a {_h(c.despues)}" if c.despues > 0 else f"{c.actividad} se quita ({_h(c.antes)})"
              for c in recorte.cambios]
    return "; ".join(partes) + "."


def _responder(decision: Decision | None, o: Orden, ahora: datetime, mlp: Predictor) -> ResultadoOrden:
    """Aplica la opción elegida para la decisión pendiente."""
    if decision is None:
        return _rechazo("No hay ninguna decisión pendiente en este momento.")
    if o.opcion not in decision.opciones:
        return _rechazo("Esa opción no está disponible para este cambio.", decision)
    if o.opcion == "cancelar":
        return ResultadoOrden(tipo="cancelada", mensaje="Listo, cancelé el cambio: tu horario sigue como estaba.")
    if o.opcion == "abrir_tiempo":
        maximo = decision.propuesto.plan.max_horas_dia
        subir = f", o sube tu máximo diario (ahora es de {_h(maximo)})" if maximo is not None else ""
        return ResultadoOrden(tipo="pregunta", decision=decision,
                              mensaje=f"Dime qué días y de qué hora a qué hora puedes estudiar de más, por ejemplo: el miércoles puedo de 20:00 a 22:00{subir}. "
                                      f"Faltan {_h(decision.faltan_horas)} antes del {fecha_texto(decision.fecha_critica)}.")
    recorte = proponer_recorte(decision, o.protegidas, ahora, mlp)
    if not recorte.alcanza:
        return _rechazo("Recortando solo las actividades que no protegiste no alcanza. Protege menos actividades o abre más tiempo.", decision)
    base = decision.propuesto
    r = _replanificar(base, _con_recorte(base.plan, recorte), base.fijadas, ahora, mlp, decision.opciones)
    r.avisos = ["Recorté: " + texto_recorte(recorte)] + r.avisos
    return r
