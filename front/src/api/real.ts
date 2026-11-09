// Cliente REAL: habla con la API de FastAPI (studyflow/api.py).
// El identificador del estudiante se crea con POST /estudiantes la primera vez que hace falta,
// se guarda en el navegador y viaja en el encabezado X-Estudiante-Id.

import type { ClienteApi, Estado, Lugar, MoverCuerpo, PlanEntrada, Respuesta, Sesion } from './tipos';
import { ErrorApi } from './tipos';

const URL_BASE: string = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const CLAVE_ID = 'studyflow.estudiante_id';

// --- Identificador en el navegador -------------------------------------------------------------
export const leerId = (): string | null => {
  try {
    return localStorage.getItem(CLAVE_ID);
  } catch {
    return null;
  }
};
const guardarId = (id: string) => {
  try {
    localStorage.setItem(CLAVE_ID, id);
  } catch {
    /* sin almacenamiento: el identificador vive solo en esta pestaña */
  }
};
const olvidarId = () => {
  try {
    localStorage.removeItem(CLAVE_ID);
  } catch {
    /* nada que borrar */
  }
};
let idEnMemoria: string | null = null;
const idActual = () => idEnMemoria ?? (idEnMemoria = leerId());

// --- Formato de horas ---------------------------------------------------------------------------
// La API devuelve "HH:MM:SS"; la interfaz trabaja con "HH:MM".
const hm = (t: string) => t.slice(0, 5);
const normSesion = <T extends Sesion>(s: T): T => ({ ...s, inicio: hm(s.inicio), fin: hm(s.fin) });

export function normalizarEstado(e: Estado): Estado {
  return {
    ...e,
    plan: e.plan && {
      ...e.plan,
      disponibilidad: e.plan.disponibilidad.map((d) => ({ ...d, franjas: d.franjas.map((f) => ({ inicio: hm(f.inicio), fin: hm(f.fin) })) })),
    },
    horario: e.horario && { ...e.horario, sesiones: e.horario.sesiones.map(normSesion) },
    bloques: e.bloques.map(normSesion),
    fijadas: e.fijadas.map(normSesion),
  };
}
const normRespuesta = (r: Respuesta): Respuesta => ({ ...r, estado: normalizarEstado(r.estado) });

// --- Peticiones ---------------------------------------------------------------------------------
function mensajeDeError(status: number, cuerpo: unknown): string {
  const detalle = (cuerpo as { detail?: unknown } | null)?.detail;
  if (status === 422) return 'Alguno de los datos enviados no es válido. Revísalos e intenta de nuevo.';
  if (typeof detalle === 'string') return detalle;
  return `El servidor respondió con un error (${status}).`;
}

async function pedir<T>(metodo: 'GET' | 'POST', ruta: string, cuerpo?: unknown, id?: string | null): Promise<T> {
  const cabeceras: Record<string, string> = {};
  if (cuerpo !== undefined) cabeceras['Content-Type'] = 'application/json';
  if (id) cabeceras['X-Estudiante-Id'] = id;
  let res: Response;
  try {
    res = await fetch(`${URL_BASE}${ruta}`, { method: metodo, headers: cabeceras, body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo) });
  } catch {
    throw new ErrorApi(0, 'No pudimos conectar con el servidor. Revisa que la API esté encendida e intenta de nuevo.');
  }
  const json: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 404) {
      // El identificador guardado ya no existe en el servidor: se olvida para pedir uno nuevo en la próxima acción.
      idEnMemoria = null;
      olvidarId();
    }
    throw new ErrorApi(res.status, mensajeDeError(res.status, json));
  }
  return json as T;
}

async function crearEstudiante(): Promise<string> {
  const { estudiante_id } = await pedir<{ estudiante_id: string }>('POST', '/estudiantes');
  idEnMemoria = estudiante_id;
  guardarId(estudiante_id);
  return estudiante_id;
}

/** El identificador guardado o, si todavía no hay, uno nuevo. */
const asegurarId = async () => idActual() ?? crearEstudiante();

async function accion(ruta: string, cuerpo: unknown): Promise<Respuesta> {
  const id = await asegurarId();
  return normRespuesta(await pedir<Respuesta>('POST', ruta, cuerpo, id));
}

const VACIO: Estado = { plan: null, horario: null, bloques: [], fijadas: [], decision: null };

export const apiReal: ClienteApi = {
  async estado() {
    const id = idActual();
    if (!id) return VACIO; // nunca ha creado un plan: no hace falta pedirle un identificador al servidor
    return normalizarEstado(await pedir<Estado>('GET', '/estado', undefined, id));
  },
  crearPlan: (plan: PlanEntrada, reemplazar: boolean) => accion('/plan', { plan, reemplazar }),
  mensaje: (texto: string) => accion('/mensaje', { texto }),
  confirmar: (aceptar: boolean) => accion('/confirmacion', { aceptar }),
  mover: (c: MoverCuerpo) => accion('/sesiones/mover', c),
  intercambiar: (a: Lugar, b: Lugar) => accion('/sesiones/intercambiar', { a, b }),
  noHecha: (s: Sesion) => accion('/sesiones/no-hecha', { actividad: s.actividad, dia: s.dia, inicio: s.inicio, fin: s.fin }),
};
