import type { DuracionSesion, Nivel, PlanEntrada } from '../api/tipos';
import { diasEntre, etiqueta, fecha, lunesDe, sumaDias, toMin, toStr } from './fechas';

/** El MLP se entrenó con plazos de 3 a 30 días. */
export const MAX_DIAS = 30;
export const DURACIONES: DuracionSesion[] = [30, 60, 90, 120, 150, 180, 210, 240];

export type FranjaForm = [string, string];

export interface ActividadForm {
  nombre: string;
  prioridad: Nivel;
  dificultad: Nivel;
  fecha: string;
  horas: string;
}

export interface Formulario {
  ini: string;
  fin: string;
  /** Solo los días elegidos tienen entrada; los demás quedan libres. */
  disp: Record<string, FranjaForm[]>;
  actividades: ActividadForm[];
  maxDia: string;
  duracion: DuracionSesion;
}

export const actividadVacia = (): ActividadForm => ({ nombre: '', prioridad: 'media', dificultad: 'media', fecha: '', horas: '' });

export const formularioVacio = (hoy: string): Formulario => ({
  ini: hoy,
  fin: '',
  disp: {},
  actividades: [actividadVacia()],
  maxDia: '',
  duracion: 90,
});

// Horas que ofrecen las listas del editor de franjas (bloques de 30 min).
export const HORAS_INICIO: string[] = [];
export const HORAS_FIN: string[] = [];
for (let m = 5 * 60; m <= 23 * 60; m += 30) HORAS_INICIO.push(toStr(m));
for (let m = 5 * 60 + 30; m <= 23 * 60 + 30; m += 30) HORAS_FIN.push(toStr(m));

export function errorRango(f: Formulario, hoy: string): string | null {
  if (!f.ini || !f.fin) return 'Elige el día inicial y el final para ver los días.';
  if (f.ini < hoy) return 'El día inicial no puede ser anterior a hoy.';
  if (f.fin < f.ini) return 'El día final debe ser igual o posterior al inicial.';
  if (diasEntre(f.ini, f.fin) + 1 > MAX_DIAS) return `El rango puede abarcar como máximo ${MAX_DIAS} días.`;
  return null;
}

/** Semanas de lunes a domingo que cubren el rango; null en los días que quedan fuera. */
export function semanasRango(f: Formulario, hoy: string): (string | null)[][] {
  if (errorRango(f, hoy)) return [];
  const semanas: (string | null)[][] = [];
  for (let lunes = lunesDe(f.ini); lunes <= f.fin; lunes = sumaDias(lunes, 7)) {
    semanas.push(Array.from({ length: 7 }, (_, k) => {
      const d = sumaDias(lunes, k);
      return d >= f.ini && d <= f.fin ? d : null;
    }));
  }
  return semanas;
}

export const diasElegidos = (f: Formulario) =>
  Object.entries(f.disp).filter(([d, fr]) => fr.length && d >= f.ini && d <= f.fin);

export function errorFranjas(fr: FranjaForm[]): string | null {
  if (fr.some(([i, f]) => toMin(f) <= toMin(i))) return 'En cada franja, la hora final debe ser posterior a la inicial.';
  const o = [...fr].sort((a, b) => toMin(a[0]) - toMin(b[0]));
  if (o.some(([i], j) => j > 0 && toMin(i) < toMin(o[j - 1][1]))) return 'Hay franjas que se traslapan. Ajusta las horas.';
  return null;
}

export const minutosFranjas = (fr: FranjaForm[]) => fr.reduce((a, [i, f]) => a + Math.max(0, toMin(f) - toMin(i)), 0);

/** Mismas reglas que el validador del backend, para avisar antes de enviar. */
export function validarFormulario(f: Formulario, hoy: string): string[] {
  const e: string[] = [];
  const er = errorRango(f, hoy);
  if (er) e.push(er);
  const elegidos = er ? [] : diasElegidos(f);
  elegidos.forEach(([d, fr]) => {
    const x = errorFranjas(fr);
    if (x) e.push(`${etiqueta(d)}: ${x.charAt(0).toLowerCase()}${x.slice(1)}`);
  });
  if (!er && !elegidos.length) e.push('Elige al menos un día en que tengas tiempo libre.');
  if (f.maxDia !== '' && (+f.maxDia <= 0 || +f.maxDia > 8)) e.push('El máximo por día debe estar entre 0,5 y 8 horas.');
  const nombres = new Set<string>();
  f.actividades.forEach((a, i) => {
    const p = `Actividad ${i + 1}`;
    const n = a.nombre.trim().toLowerCase();
    if (!n) e.push(`${p}: falta el nombre.`);
    else if (nombres.has(n)) e.push(`${p}: el nombre está repetido.`);
    nombres.add(n);
    if (!a.fecha) e.push(`${p}: falta la fecha límite.`);
    else if (a.fecha <= hoy) e.push(`${p}: la fecha límite ya pasó.`);
    else if (elegidos.filter(([d]) => d < a.fecha).length < 3)
      e.push(`${p}: necesita al menos 3 días con disponibilidad antes de la entrega (el día de entrega no cuenta).`);
    if (!(+a.horas > 0 && +a.horas <= 100)) e.push(`${p}: las horas estimadas deben estar entre 0 y 100.`);
  });
  return e;
}

/** Convierte el formulario en el cuerpo de POST /plan. */
export function aPlanEntrada(f: Formulario): PlanEntrada {
  return {
    actividades: f.actividades.map((a) => ({
      nombre: a.nombre.trim(),
      prioridad: a.prioridad,
      dificultad: a.dificultad,
      fecha_limite: a.fecha,
      horas_estimadas: +a.horas,
    })),
    disponibilidad: diasElegidos(f)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([dia, fr]) => ({
        dia,
        franjas: [...fr].sort((a, b) => toMin(a[0]) - toMin(b[0])).map(([inicio, fin]) => ({ inicio, fin })),
      })),
    max_horas_dia: f.maxDia === '' ? null : +f.maxDia,
    duracion_sesion: f.duracion,
  };
}

/** Rellena el formulario con el plan guardado en la API (al recargar la página). Los días que ya pasaron se descartan. */
export function planAFormulario(plan: PlanEntrada, hoy: string): Formulario {
  const dias = plan.disponibilidad.filter((d) => d.dia >= hoy && d.franjas.length);
  const disp: Record<string, FranjaForm[]> = {};
  dias.forEach((d) => (disp[d.dia] = d.franjas.map((f) => [f.inicio.slice(0, 5), f.fin.slice(0, 5)] as FranjaForm)));
  const ordenados = Object.keys(disp).sort();
  return {
    ini: ordenados[0] ?? hoy,
    fin: ordenados[ordenados.length - 1] ?? '',
    disp,
    actividades: plan.actividades.map((a) => ({
      nombre: a.nombre,
      prioridad: a.prioridad,
      dificultad: a.dificultad,
      fecha: a.fecha_limite,
      horas: String(a.horas_estimadas),
    })),
    maxDia: plan.max_horas_dia == null ? '' : String(plan.max_horas_dia),
    duracion: plan.duracion_sesion,
  };
}

// --- Ejemplo para rellenar el formulario ---------------------------------------------------------
/** Franjas por día de la semana (0 = lunes). */
const PLANTILLA: FranjaForm[][] = [
  [['18:00', '21:00']],
  [['14:00', '17:00']],
  [['18:00', '21:00']],
  [['14:00', '18:00']],
  [['14:00', '19:00']],
  [['09:00', '13:00'], ['15:00', '17:00']],
  [],
];

export function formularioEjemplo(hoy: string): Formulario {
  const disp: Record<string, FranjaForm[]> = {};
  for (let i = 0; i < 14; i++) {
    const d = sumaDias(hoy, i);
    const fr = PLANTILLA[(fecha(d).getDay() + 6) % 7];
    if (fr.length) disp[d] = fr.map((x) => [...x] as FranjaForm);
  }
  // Entregas contadas en días con tiempo libre, para que el ejemplo funcione cualquier día de la semana.
  const conTiempo = Object.keys(disp).sort();
  const trasDia = (n: number) => sumaDias(conTiempo[n - 1], 1);
  return {
    ini: hoy,
    fin: sumaDias(hoy, 13),
    disp,
    actividades: [
      { nombre: 'Parcial de IA', prioridad: 'alta', dificultad: 'alta', fecha: trasDia(4), horas: '6' },
      { nombre: 'Taller Bases de Datos', prioridad: 'media', dificultad: 'media', fecha: trasDia(5), horas: '4' },
      { nombre: 'Ensayo Marketing', prioridad: 'baja', dificultad: 'media', fecha: trasDia(6), horas: '5.5' },
      { nombre: 'Lectura inglés', prioridad: 'media', dificultad: 'baja', fecha: trasDia(9), horas: '3' },
    ],
    maxDia: '',
    duracion: 90,
  };
}
