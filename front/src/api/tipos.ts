// Tipos que reflejan los modelos de la API (studyflow/models.py, asignacion.py, servicio.py y api.py).
// Fechas como "YYYY-MM-DD" y horas como "HH:MM" (la API devuelve "HH:MM:SS"; se aceptan ambas).

export type Nivel = 'alta' | 'media' | 'baja';
export type DuracionSesion = 30 | 60 | 90 | 120 | 150 | 180 | 210 | 240;

export interface Actividad {
  nombre: string;
  prioridad: Nivel;
  dificultad: Nivel;
  fecha_limite: string;
  horas_estimadas: number;
}

export interface Franja {
  inicio: string;
  fin: string;
}

export interface DisponibilidadDia {
  dia: string;
  franjas: Franja[];
}

export interface PlanEntrada {
  actividades: Actividad[];
  disponibilidad: DisponibilidadDia[];
  max_horas_dia: number | null;
  duracion_sesion: DuracionSesion;
}

export interface Sesion {
  actividad: string;
  dia: string;
  inicio: string;
  fin: string;
}

export type EstadoAsignacion = 'asignada' | 'parcial' | 'no_asignada';

export interface ResumenActividad {
  nombre: string;
  prioridad: number;
  horas_solicitadas: number;
  horas_asignadas: number;
  horas_faltantes: number;
  estado: EstadoAsignacion;
}

export interface ResultadoAsignacion {
  sesiones: Sesion[];
  actividades: ResumenActividad[];
}

/** Sesiones seguidas de la misma actividad en un día, como las muestra el calendario. */
export interface Bloque extends Sesion {
  fijado: boolean;
}

export type Opcion = 'abrir_tiempo' | 'recortar' | 'cancelar';

export interface DecisionVista {
  opciones: Opcion[];
  faltan_horas: number;
  fecha_critica: string;
}

export interface Estado {
  plan: PlanEntrada | null;
  horario: ResultadoAsignacion | null;
  bloques: Bloque[];
  fijadas: Sesion[];
  decision: DecisionVista | null;
}

export type TipoRespuesta =
  | 'confirmacion'
  | 'confirmar_reemplazo'
  | 'aplicada'
  | 'decision'
  | 'pregunta'
  | 'consulta'
  | 'cancelada'
  | 'descartada'
  | 'rechazada'
  | 'aclaracion'
  | 'fuera_de_alcance'
  | 'sin_pendiente';

export interface Respuesta {
  tipo: TipoRespuesta;
  mensaje: string;
  texto_horario: string | null;
  avisos: string[];
  estado: Estado;
}

export interface MoverCuerpo {
  actividad: string;
  dia_origen: string;
  dia_destino: string;
  /** Sin hora: el primer hueco libre del día destino. */
  hora_inicio?: string | null;
  /** Una hora dentro del bloque que se mueve (lo normal: su inicio). */
  hora_origen?: string | null;
}

export interface Lugar {
  dia: string;
  hora: string;
}

/** Contrato que cumple tanto el cliente simulado como el real. */
export interface ClienteApi {
  crearEstudiante(): Promise<string>;
  estado(): Promise<Estado>;
  crearPlan(plan: PlanEntrada, reemplazar: boolean): Promise<Respuesta>;
  mensaje(texto: string): Promise<Respuesta>;
  confirmar(aceptar: boolean): Promise<Respuesta>;
  mover(cuerpo: MoverCuerpo): Promise<Respuesta>;
  intercambiar(a: Lugar, b: Lugar): Promise<Respuesta>;
  noHecha(sesion: Sesion): Promise<Respuesta>;
}

/** Error de protocolo de la API: 404 (identificador desconocido), 422 (cuerpo inválido) o 429 (límites). */
export class ErrorApi extends Error {
  constructor(
    public readonly status: number,
    mensaje: string,
  ) {
    super(mensaje);
    this.name = 'ErrorApi';
  }
}
