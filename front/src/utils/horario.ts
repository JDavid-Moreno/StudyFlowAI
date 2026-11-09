import type { Bloque, Estado, PlanEntrada, Sesion } from '../api/tipos';
import { etiqueta, hm, lunesDe, sumaDias, toMin } from './fechas';

/** Contexto con lo necesario para saber si una sesión cabe en un lugar. La API valida de nuevo al aplicar. */
export interface Contexto {
  plan: PlanEntrada;
  bloques: Bloque[];
  hoy: string;
  ahora: number;
}

export const claveBloque = (b: Sesion) => `${b.actividad}|${b.dia}|${hm(b.inicio)}`;
export const duracion = (b: Sesion) => toMin(b.fin) - toMin(b.inicio);
export const fechaLimite = (plan: PlanEntrada, actividad: string) =>
  plan.actividades.find((a) => a.nombre === actividad)?.fecha_limite ?? '';

/** Franjas del día en minutos; hoy empiezan en el siguiente bloque de 30 min. */
export function franjasDia(ctx: Contexto, dia: string): [number, number][] {
  const d = ctx.plan.disponibilidad.find((x) => x.dia === dia);
  if (!d || dia < ctx.hoy) return [];
  const corte = dia === ctx.hoy ? Math.ceil(ctx.ahora / 30) * 30 : 0;
  return d.franjas
    .map((f) => [Math.max(toMin(f.inicio), corte), toMin(f.fin)] as [number, number])
    .filter(([a, b]) => b > a);
}

/** Razón por la que no cabe, o null si cabe. */
export function razonNoCabe(ctx: Contexto, actividad: string, dia: string, ini: number, dur: number, ignorar: Sesion[] = []): string | null {
  const limite = fechaLimite(ctx.plan, actividad);
  if (dia < ctx.hoy) return 'Ese día ya pasó.';
  if (limite && dia >= limite) return `Tiene que quedar antes de la entrega (${etiqueta(limite)}); el día de entrega no cuenta.`;
  const fr = franjasDia(ctx, dia);
  if (!fr.length) return 'Ese día no tienes tiempo de estudio.';
  if (!fr.some(([a, b]) => ini >= a && ini + dur <= b)) return 'No cabe dentro de tu tiempo de estudio de ese día.';
  const ign = new Set(ignorar.map(claveBloque));
  const otros = ctx.bloques.filter((o) => o.dia === dia && !ign.has(claveBloque(o)));
  if (otros.some((o) => toMin(o.inicio) < ini + dur && ini < toMin(o.fin))) return 'Choca con otra sesión.';
  const max = ctx.plan.max_horas_dia;
  if (max != null && otros.reduce((s, o) => s + duracion(o), 0) + dur > max * 60) return 'Pasaría tu máximo de horas de ese día.';
  return null;
}

/** Horas de inicio posibles para el bloque en ese día (en minutos). */
export function opcionesHora(ctx: Contexto, b: Bloque, dia: string): number[] {
  const d = duracion(b);
  const out: number[] = [];
  for (const [a, c] of franjasDia(ctx, dia)) {
    for (let t = Math.ceil(a / 30) * 30; t + d <= c; t += 30) {
      if (dia === b.dia && t === toMin(b.inicio)) continue;
      if (!razonNoCabe(ctx, b.actividad, dia, t, d, [b])) out.push(t);
    }
  }
  return out;
}

/** Días del plan (desde hoy) en los que el bloque tiene al menos una hora posible. */
export function opcionesDia(ctx: Contexto, b: Bloque): string[] {
  return ctx.plan.disponibilidad.map((d) => d.dia).filter((d) => opcionesHora(ctx, b, d).length > 0);
}

/** Por qué no se puede soltar el bloque en ese día (null si se puede). */
export function razonDia(ctx: Contexto, b: Bloque, dia: string): string | null {
  if (opcionesHora(ctx, b, dia).length) return null;
  const limite = fechaLimite(ctx.plan, b.actividad);
  if (dia < ctx.hoy) return 'Ese día ya pasó.';
  if (limite && dia >= limite) return `Tiene que quedar antes de la entrega (${etiqueta(limite)}); el día de entrega no cuenta.`;
  if (!franjasDia(ctx, dia).length) return 'Ese día no tienes tiempo de estudio.';
  return 'No queda un hueco libre de esa duración ese día.';
}

/** Por qué no se pueden intercambiar dos bloques (null si se puede). */
export function razonIntercambio(ctx: Contexto, a: Bloque, b: Bloque): string | null {
  if (a.actividad === b.actividad) return 'Son de la misma actividad: intercambiarlas no cambia nada.';
  const ra = razonNoCabe(ctx, a.actividad, b.dia, toMin(b.inicio), duracion(a), [a, b]);
  if (ra) return `${a.actividad}: ${ra}`;
  const rb = razonNoCabe(ctx, b.actividad, a.dia, toMin(a.inicio), duracion(b), [a, b]);
  if (rb) return `${b.actividad}: ${rb}`;
  if (a.dia === b.dia) {
    const ia = toMin(b.inicio), ib = toMin(a.inicio);
    if (ia < ib + duracion(b) && ib < ia + duracion(a)) return 'Al intercambiarlas quedarían una encima de la otra.';
  }
  return null;
}

/** Une las sesiones seguidas de la misma actividad en un mismo día. */
export function agrupar(sesiones: (Sesion & { fijado?: boolean })[]): Bloque[] {
  const o = [...sesiones].sort((a, b) => a.dia.localeCompare(b.dia) || toMin(a.inicio) - toMin(b.inicio));
  const out: Bloque[] = [];
  for (const s of o) {
    const u = out[out.length - 1];
    if (u && u.actividad === s.actividad && u.dia === s.dia && hm(u.fin) === hm(s.inicio)) {
      u.fin = s.fin;
      u.fijado = u.fijado || !!s.fijado;
    } else out.push({ actividad: s.actividad, dia: s.dia, inicio: hm(s.inicio), fin: hm(s.fin), fijado: !!s.fijado });
  }
  return out;
}

/** Semanas (lunes) que cubre el plan: desde el primer día con tiempo hasta la última entrega. */
export function semanasPlan(estado: Estado): string[] {
  const plan = estado.plan;
  if (!plan) return [];
  const dias = [
    ...plan.disponibilidad.map((d) => d.dia),
    ...plan.actividades.map((a) => a.fecha_limite),
    ...estado.bloques.map((b) => b.dia),
  ].sort();
  const out: string[] = [];
  for (let l = lunesDe(dias[0]); l <= dias[dias.length - 1]; l = sumaDias(l, 7)) out.push(l);
  return out;
}

/** Color de cada actividad según su orden en el plan (5 colores de la paleta). */
export const indiceColor = (plan: PlanEntrada | null, actividad: string) =>
  Math.max(0, plan?.actividades.findIndex((a) => a.nombre === actividad) ?? 0) % 5;
export const COLORES = ['var(--coral)', 'var(--azul)', 'var(--celeste)', 'var(--crema)', 'var(--durazno)'];
