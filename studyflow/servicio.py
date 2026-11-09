"""Servicio: lo que hace la API en cada petición (persistencia, intérprete, capa de órdenes y redactor), sin HTTP. Ver README, sección API."""
import sqlite3
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel

from studyflow.asignacion import ResultadoAsignacion, Sesion
from studyflow.interprete import Contexto, Llamar, Opcion, Orden, OrdenValida, interpretar
from studyflow.models import PlanEntrada
from studyflow.ordenes import Decision, PlanVigente, Predictor, ResultadoOrden, _bloques, aplicar, crear_plan, marcar_no_hecha, \
    proponer_recorte, texto_recorte
from studyflow.persistencia import (cargar_plan, descartar_decision_pendiente, descartar_orden_pendiente, guardar_decision_pendiente,
                                    guardar_orden_pendiente, guardar_plan, leer_decision_pendiente, nuevo_estudiante, tomar_orden_pendiente)
from studyflow.redactor import redactar

AVISO_REEMPLAZO = "Ya tienes un plan. Si creas uno nuevo, el anterior y sus cambios se eliminarán. ¿Quieres continuar?"
SIN_PENDIENTE = "No tengo ningún cambio esperando tu confirmación (o pasaron más de 30 minutos). Pídemelo de nuevo."


class DecisionVista(BaseModel):
    """Lo que el front muestra de una decisión pendiente."""

    opciones: list[Opcion]
    faltan_horas: float
    fecha_critica: date


class Bloque(BaseModel):
    """Sesiones seguidas de la misma actividad en un día, como las muestra el calendario."""

    actividad: str
    dia: date
    inicio: time
    fin: time
    fijado: bool          # tiene al menos una sesión fijada


class Estado(BaseModel):
    """Lo que el front pinta: el plan, el horario completo (y agrupado en bloques), las sesiones fijadas y la decisión pendiente."""

    plan: PlanEntrada | None = None
    horario: ResultadoAsignacion | None = None
    bloques: list[Bloque] = []
    fijadas: list[Sesion] = []
    decision: DecisionVista | None = None


class Respuesta(BaseModel):
    """Respuesta de toda petición que puede cambiar algo."""

    tipo: Literal["confirmacion", "confirmar_reemplazo", "aplicada", "decision", "pregunta", "consulta", "cancelada", "descartada",
                  "rechazada", "aclaracion", "fuera_de_alcance", "sin_pendiente"]
    mensaje: str
    texto_horario: str | None = None    # el horario contado por el redactor (o su plantilla)
    avisos: list[str] = []
    estado: Estado


class Servicio:
    """Una petición: la conexión, el MLP, la función del LLM y la hora del servidor."""

    def __init__(self, con: sqlite3.Connection, mlp: Predictor, llamar: Llamar, ahora: datetime):
        self.con, self.mlp, self.llamar, self.ahora = con, mlp, llamar, ahora

    # --- Lectura -----------------------------------------------------------------------------------
    def nuevo_estudiante(self) -> str:
        """Registra un estudiante anónimo y devuelve su identificador."""
        return nuevo_estudiante(self.con)

    def _cargar(self, e: str) -> tuple[PlanVigente | None, Decision | None]:
        """Plan vigente y decisión pendiente. Si el primer plan no cupo, el vigente es el propuesto de la decisión."""
        g, d = cargar_plan(self.con, e), leer_decision_pendiente(self.con, e)
        vigente = PlanVigente(plan=g.plan, horario=g.horario, fijadas=g.fijadas) if g else None
        return vigente or (d.propuesto if d else None), d

    def estado(self, e: str) -> Estado:
        """El estado guardado del estudiante."""
        g, d = cargar_plan(self.con, e), leer_decision_pendiente(self.con, e)
        vista = DecisionVista(opciones=d.opciones, faltan_horas=d.faltan_horas, fecha_critica=d.fecha_critica) if d else None
        if g is None:
            return Estado(decision=vista)
        bloques = [Bloque(actividad=b[0].actividad, dia=b[0].dia, inicio=b[0].inicio, fin=b[-1].fin, fijado=any(s in g.fijadas for s in b))
                   for b in _bloques(g.horario.sesiones)]
        return Estado(plan=g.plan, horario=g.horario, bloques=bloques, fijadas=g.fijadas, decision=vista)

    # --- Peticiones --------------------------------------------------------------------------------
    def crear_plan(self, e: str, plan: PlanEntrada, reemplazar: bool) -> Respuesta:
        """Plan nuevo desde el formulario; si ya hay uno, primero se pide confirmar el reemplazo."""
        if cargar_plan(self.con, e) is not None and not reemplazar:
            return Respuesta(tipo="confirmar_reemplazo", mensaje=AVISO_REEMPLAZO, estado=self.estado(e))
        r = crear_plan(plan, self.ahora, self.mlp)
        if r.tipo != "rechazada":
            descartar_orden_pendiente(self.con, e)      # hablaba del plan anterior; la decisión la reemplaza _guardar
        return self._guardar(e, r)

    def mensaje(self, e: str, texto: str) -> Respuesta:
        """Un mensaje del chat: se interpreta y, si cambia algo, queda esperando confirmación."""
        vigente, d = self._cargar(e)
        base = d.propuesto if d else vigente          # con una decisión abierta, las órdenes se aplican sobre el plan propuesto
        nombres = [a.nombre for a in base.plan.actividades] if base else []
        r = interpretar(texto, Contexto(hoy=self.ahora.date(), actividades=nombres, decision=d.opciones if d else None), self.llamar)
        if not isinstance(r, OrdenValida):
            return Respuesta(tipo=r.tipo, mensaje=r.mensaje, estado=self.estado(e))
        if not r.requiere_confirmacion or vigente is None or r.orden.accion == "crear_plan":
            return self._guardar(e, aplicar(vigente, r.orden, self.ahora, self.mlp, d))     # sin plan o desde el chat: se rechaza sin preguntar
        confirmacion = r.confirmacion
        if r.orden.accion == "responder_decision" and r.orden.opcion == "recortar":
            recorte = proponer_recorte(d, r.orden.protegidas, self.ahora, self.mlp)
            if not recorte.alcanza:
                return Respuesta(tipo="rechazada", estado=self.estado(e),
                                 mensaje="Recortando solo las actividades que no protegiste no alcanza. Protege menos actividades o abre más tiempo.")
            confirmacion = confirmacion.replace(" ¿Confirmas?", f" Quedaría así: {texto_recorte(recorte)} ¿Confirmas?")
        guardar_orden_pendiente(self.con, e, r.orden)
        return Respuesta(tipo="confirmacion", mensaje=confirmacion, estado=self.estado(e))

    def confirmar(self, e: str, aceptar: bool) -> Respuesta:
        """El estudiante confirma (se aplica) o no (se descarta) la orden que esperaba."""
        if not aceptar:
            descartar_orden_pendiente(self.con, e)
            return Respuesta(tipo="descartada", mensaje="Listo, no hice ningún cambio.", estado=self.estado(e))
        orden = tomar_orden_pendiente(self.con, e)
        if orden is None:
            return Respuesta(tipo="sin_pendiente", mensaje=SIN_PENDIENTE, estado=self.estado(e))
        vigente, d = self._cargar(e)
        return self._guardar(e, aplicar(vigente, orden, self.ahora, self.mlp, d))

    def mover(self, e: str, actividad: str, dia_origen: date, dia_destino: date, hora_inicio: time | None,
              hora_origen: time | None = None) -> Respuesta:
        """Mover desde el calendario (con `hora_origen`, solo ese bloque): el front ya pidió confirmación, así que se aplica de una vez."""
        vigente, d = self._cargar(e)
        orden = Orden(accion="mover_sesion", actividad=actividad, dia_origen=dia_origen, dia_destino=dia_destino, hora_inicio=hora_inicio,
                      hora_origen=hora_origen)
        return self._guardar(e, aplicar(vigente, orden, self.ahora, self.mlp, d))

    def intercambiar(self, e: str, dia_a: date, hora_a: time, dia_b: date, hora_b: time) -> Respuesta:
        """Intercambiar dos bloques desde el calendario (cada hora puede ser cualquiera dentro de su bloque); también se aplica de una vez."""
        vigente, d = self._cargar(e)
        orden = Orden(accion="intercambiar_sesiones", dia_origen=dia_a, hora_origen=hora_a, dia_destino=dia_b, hora_inicio=hora_b)
        return self._guardar(e, aplicar(vigente, orden, self.ahora, self.mlp, d))

    def no_hecha(self, e: str, sesion: Sesion) -> Respuesta:
        """Botón no la hice: sus horas vuelven a lo pendiente y se replanifica."""
        vigente, d = self._cargar(e)
        if vigente is None:
            return Respuesta(tipo="rechazada", mensaje="Todavía no tienes un plan.", estado=self.estado(e))
        return self._guardar(e, marcar_no_hecha(vigente, sesion, self.ahora, self.mlp, d))

    # --- Resultado de la capa de órdenes -----------------------------------------------------------
    def _guardar(self, e: str, r: ResultadoOrden) -> Respuesta:
        """Guarda lo que corresponde según el resultado y, si hay horario que mostrar, lo redacta."""
        texto = None
        if r.tipo == "aplicada":
            guardar_plan(self.con, e, r.vigente.plan, self.ahora.date(), r.vigente.horario, r.vigente.fijadas)
            texto = self._redactar(r.vigente.horario)
        elif r.tipo in ("decision", "pregunta"):
            guardar_decision_pendiente(self.con, e, r.decision)
        elif r.tipo == "cancelada":
            descartar_decision_pendiente(self.con, e)
        elif r.tipo == "consulta":
            texto = self._redactar(r.horario)
        return Respuesta(tipo=r.tipo, mensaje=r.mensaje, texto_horario=texto, avisos=r.avisos, estado=self.estado(e))

    def _redactar(self, horario: ResultadoAsignacion) -> str:
        """El horario de hoy en adelante, contado por el redactor (si el LLM falla o inventa algo, la plantilla)."""
        hoy = self.ahora.date()
        return redactar(horario.model_copy(update={"sesiones": [s for s in horario.sesiones if s.dia >= hoy]}), self.llamar).texto
