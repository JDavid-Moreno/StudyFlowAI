"""API con FastAPI: rutas, configuración, límites y un cambio a la vez por estudiante. Ver README, sección API."""
import os
import threading
import time as _reloj
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from studyflow.asignacion import Sesion
from studyflow.interprete import MAX_CHARS, Llamar
from studyflow.models import PlanEntrada
from studyflow.ordenes import Predictor
from studyflow.persistencia import conectar, existe_estudiante
from studyflow.servicio import Estado, Respuesta, Servicio

load_dotenv(find_dotenv(usecwd=True))


@dataclass
class Config:
    """Configuración leída del .env (con valores por defecto)."""

    ruta_db: str = field(default_factory=lambda: os.environ.get("STUDYFLOW_DB", "studyflow.db"))
    zona: str = field(default_factory=lambda: os.environ.get("STUDYFLOW_TZ", "America/Bogota"))
    origenes: list[str] = field(default_factory=lambda: [o.strip() for o in os.environ.get("STUDYFLOW_CORS", "http://localhost:5173").split(",") if o.strip()])
    peticiones_min: int = field(default_factory=lambda: int(os.environ.get("LIMITE_PETICIONES_MIN", 10)))
    estudiantes_ip_hora: int = field(default_factory=lambda: int(os.environ.get("LIMITE_ESTUDIANTES_IP_HORA", 20)))
    llm_dia: int = field(default_factory=lambda: int(os.environ.get("LIMITE_LLM_DIA", 500)))


# --- Cuerpos de las peticiones --------------------------------------------------------------------
class NuevoPlan(BaseModel):
    plan: PlanEntrada
    reemplazar: bool = False          # el front lo envía en true después de que el estudiante confirma el reemplazo


class Mensaje(BaseModel):
    texto: str = Field(min_length=1, max_length=MAX_CHARS)


class Confirmacion(BaseModel):
    aceptar: bool


class Mover(BaseModel):
    actividad: str = Field(min_length=1)
    dia_origen: date
    dia_destino: date
    hora_inicio: time | None = None   # sin hora: el primer hueco del destino
    hora_origen: time | None = None   # una hora dentro del bloque arrastrado (lo normal: su inicio); sin ella se mueven todas las de ese día

    @field_validator("hora_inicio")
    @classmethod
    def _en_punto_o_y_media(cls, h: time | None) -> time | None:
        if h is not None and (h.minute not in (0, 30) or h.second or h.microsecond):
            raise ValueError("la hora debe ser en punto o y media (bloques de 30 min)")
        return h


class Lugar(BaseModel):
    dia: date
    hora: time                        # cualquier hora dentro del bloque (lo normal: su inicio)


class Intercambio(BaseModel):
    a: Lugar
    b: Lugar


class NuevoEstudiante(BaseModel):
    estudiante_id: str


# --- Límites (en memoria: un solo proceso) --------------------------------------------------------
class Limitador:
    """Como mucho `maximo` usos por clave dentro de una ventana deslizante de `segundos`."""

    def __init__(self, maximo: int, segundos: float):
        self.maximo, self.segundos, self.usos, self.candado = maximo, segundos, defaultdict(deque), threading.Lock()

    def permitir(self, clave: str) -> bool:
        ahora = _reloj.monotonic()
        with self.candado:
            usos = self.usos[clave]
            while usos and ahora - usos[0] >= self.segundos:
                usos.popleft()
            if len(usos) >= self.maximo:
                return False
            usos.append(ahora)
            return True


class TopeLLM:
    """Envuelve la función del LLM y corta al llegar al tope diario; el intérprete y el redactor ya saben qué hacer si falla."""

    def __init__(self, llamar: Llamar, maximo: int, hoy):
        self.llamar, self.maximo, self.hoy = llamar, maximo, hoy
        self.dia, self.usadas, self.candado = None, 0, threading.Lock()

    def __call__(self, sistema, contenido, esquema):
        with self.candado:
            if self.dia != self.hoy():
                self.dia, self.usadas = self.hoy(), 0
            if self.usadas >= self.maximo:
                raise RuntimeError("se alcanzó el tope diario de llamadas al LLM")
            self.usadas += 1
        return self.llamar(sistema, contenido, esquema)


# --- Aplicación -----------------------------------------------------------------------------------
def crear_app(config: Config | None = None, mlp: Predictor | None = None, llamar: Llamar | None = None) -> FastAPI:
    """Arma la API. En las pruebas se le pasan un MLP y un LLM simulados; en producción se cargan al arrancar."""
    config = config or Config()
    zona = ZoneInfo(config.zona)
    ahora = lambda: datetime.now(zona).replace(tzinfo=None, microsecond=0)     # hora de Bogotá, sin zona (como la usa el flujo)
    dependencias = {"mlp": mlp, "llamar": None}
    por_estudiante = Limitador(config.peticiones_min, 60)
    por_ip = Limitador(config.estudiantes_ip_hora, 3600)
    candados, candado_global = defaultdict(threading.Lock), threading.Lock()

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        if dependencias["mlp"] is None:
            from studyflow.mlp import PredictorMLP
            dependencias["mlp"] = PredictorMLP()        # TensorFlow tarda en cargar: una sola vez al arrancar
        base = llamar
        if base is None:
            from studyflow.llm import llamar_llm
            base = llamar_llm
        dependencias["llamar"] = TopeLLM(base, config.llm_dia, lambda: ahora().date())
        conectar(config.ruta_db).close()                 # crea las tablas si faltan
        yield

    app = FastAPI(title="StudyFlow AI", version="1.0", lifespan=ciclo)
    app.add_middleware(CORSMiddleware, allow_origins=config.origenes, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Estudiante-Id"])

    @contextmanager
    def servicio(estudiante_id: str | None, limitar: bool = True):
        """Abre la conexión de esta petición, revisa el identificador y el límite, y no deja que dos cambios del mismo estudiante se crucen."""
        con = conectar(config.ruta_db)
        try:
            if estudiante_id is not None and not existe_estudiante(con, estudiante_id):
                raise HTTPException(404, "Identificador de estudiante desconocido: pide uno nuevo con POST /estudiantes.")
            if limitar and not por_estudiante.permitir(estudiante_id):
                raise HTTPException(429, "Demasiadas peticiones seguidas. Espera un momento.")
            with candado_global:
                candado = candados[estudiante_id]
            with candado:
                yield Servicio(con, dependencias["mlp"], dependencias["llamar"], ahora())
        finally:
            con.close()

    @app.get("/salud")
    def salud() -> dict:
        return {"estado": "ok", "hora": ahora().isoformat()}

    @app.post("/estudiantes", status_code=201)
    def nuevo_estudiante(request: Request) -> NuevoEstudiante:
        if not por_ip.permitir(request.client.host if request.client else "desconocida"):
            raise HTTPException(429, "Se crearon demasiados estudiantes desde esta conexión. Intenta más tarde.")
        con = conectar(config.ruta_db)
        try:
            return NuevoEstudiante(estudiante_id=Servicio(con, None, None, ahora()).nuevo_estudiante())
        finally:
            con.close()

    @app.get("/estado")
    def estado(x_estudiante_id: str = Header()) -> Estado:
        with servicio(x_estudiante_id, limitar=False) as s:
            return s.estado(x_estudiante_id)

    @app.post("/plan")
    def plan(cuerpo: NuevoPlan, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.crear_plan(x_estudiante_id, cuerpo.plan, cuerpo.reemplazar)

    @app.post("/mensaje")
    def mensaje(cuerpo: Mensaje, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.mensaje(x_estudiante_id, cuerpo.texto)

    @app.post("/confirmacion")
    def confirmacion(cuerpo: Confirmacion, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.confirmar(x_estudiante_id, cuerpo.aceptar)

    @app.post("/sesiones/mover")
    def mover(cuerpo: Mover, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.mover(x_estudiante_id, cuerpo.actividad, cuerpo.dia_origen, cuerpo.dia_destino, cuerpo.hora_inicio, cuerpo.hora_origen)

    @app.post("/sesiones/intercambiar")
    def intercambiar(cuerpo: Intercambio, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.intercambiar(x_estudiante_id, cuerpo.a.dia, cuerpo.a.hora, cuerpo.b.dia, cuerpo.b.hora)

    @app.post("/sesiones/no-hecha")
    def no_hecha(cuerpo: Sesion, x_estudiante_id: str = Header()) -> Respuesta:
        with servicio(x_estudiante_id) as s:
            return s.no_hecha(x_estudiante_id, cuerpo)

    return app


app = crear_app()
