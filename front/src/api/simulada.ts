// Cliente SIMULADO: cumple el contrato de ClienteApi sin llamar al backend, para trabajar el diseño.
// Arma un horario sencillo con lo que llega del formulario y responde al chat con unos casos fijos.
// En la fase de conexión se reemplaza por el cliente real (api/real.ts) en api/index.ts.

import type { Bloque, ClienteApi, DecisionVista, Estado, Lugar, MoverCuerpo, PlanEntrada, Respuesta, ResumenActividad, Sesion, TipoRespuesta } from './tipos';
import { ErrorApi } from './tipos';
import { DIASL, ahoraMin, etiqueta, etiquetaL, fecha, hm, hoyISO, sumaDias, toMin, toStr } from '../utils/fechas';
import { type Contexto, agrupar, claveBloque, duracion, razonDia, razonIntercambio, razonNoCabe } from '../utils/horario';

const AVISO_REEMPLAZO = 'Ya tienes un plan. Si creas uno nuevo, el anterior y sus cambios se eliminarán. ¿Quieres continuar?';
const SIN_PENDIENTE = 'No tengo ningún cambio esperando tu confirmación (o pasaron más de 30 minutos). Pídemelo de nuevo.';
const PESO = { alta: 0, media: 1, baja: 2 } as const;

interface Propuesta {
  plan: PlanEntrada;
  bloques: Bloque[];
  faltan: { actividad: string; min: number }[];
}

// --- Estado guardado "en el servidor" -------------------------------------------------------------
let plan: PlanEntrada | null = null;
let bloques: Bloque[] = [];
let decision: (DecisionVista & { propuesta: Propuesta; esperaApertura: boolean }) | null = null;
let pendiente: { propuesta: Propuesta; texto: string } | null = null;
let marcas: number[] = [];

const espera = (ms = 700) => new Promise((r) => setTimeout(r, ms + Math.random() * 300));
const ctx = (p = plan!, b = bloques): Contexto => ({ plan: p, bloques: b, hoy: hoyISO(), ahora: ahoraMin() });

function limitar() {
  const t = Date.now();
  marcas = marcas.filter((x) => t - x < 60_000);
  if (marcas.length >= 10) throw new ErrorApi(429, 'Vas muy rápido: máximo 10 cambios por minuto. Intenta de nuevo en unos segundos.');
  marcas.push(t);
}

function resumen(p: PlanEntrada, bs: Bloque[]): ResumenActividad[] {
  return p.actividades.map((a, i) => {
    const sol = Math.round(a.horas_estimadas * 2) / 2;
    const asig = bs.filter((b) => b.actividad === a.nombre).reduce((s, b) => s + duracion(b), 0) / 60;
    const falt = Math.max(0, sol - asig);
    return {
      nombre: a.nombre,
      prioridad: +(1 - i / Math.max(1, p.actividades.length)).toFixed(2),
      horas_solicitadas: sol,
      horas_asignadas: asig,
      horas_faltantes: falt,
      estado: falt === 0 ? 'asignada' : asig > 0 ? 'parcial' : 'no_asignada',
    };
  });
}

function estado(): Estado {
  return {
    plan,
    horario: plan ? { sesiones: bloques.map(({ actividad, dia, inicio, fin }) => ({ actividad, dia, inicio, fin })), actividades: resumen(plan, bloques) } : null,
    bloques: bloques.map((b) => ({ ...b })),
    fijadas: bloques.filter((b) => b.fijado).map(({ actividad, dia, inicio, fin }) => ({ actividad, dia, inicio, fin })),
    decision: decision ? { opciones: decision.opciones, faltan_horas: decision.faltan_horas, fecha_critica: decision.fecha_critica } : null,
  };
}

const resp = (tipo: TipoRespuesta, mensaje: string, avisos: string[] = []): Respuesta => ({ tipo, mensaje, texto_horario: null, avisos, estado: estado() });

/** Primer hueco para `dur` minutos en los días dados. */
function hueco(c: Contexto, actividad: string, dias: string[], dur: number): { dia: string; ini: number } | null {
  for (const dia of dias) {
    const d = c.plan.disponibilidad.find((x) => x.dia === dia);
    if (!d) continue;
    for (const f of d.franjas) {
      for (let t = toMin(f.inicio); t + dur <= toMin(f.fin); t += 30) {
        if (!razonNoCabe(c, actividad, dia, t, dur)) return { dia, ini: t };
      }
    }
  }
  return null;
}

/** Reparte `min` minutos de una actividad en sesiones, día por día; devuelve lo que no cupo. */
function repartir(c: Contexto, actividad: string, min: number, dias: string[], durMax: number): number {
  let resto = min;
  let avanza = true;
  while (resto > 0 && avanza) {
    avanza = false;
    for (const dia of dias) {
      if (resto <= 0) break;
      for (let d = Math.min(durMax, resto); d >= 30; d -= 30) {
        const h = hueco(c, actividad, [dia], d);
        if (h) {
          c.bloques.push({ actividad, dia, inicio: toStr(h.ini), fin: toStr(h.ini + d), fijado: false });
          resto -= d;
          avanza = true;
          break;
        }
      }
    }
  }
  return resto;
}

function planificar(p: PlanEntrada): Propuesta {
  const c = ctx(p, []);
  const faltan: Propuesta['faltan'] = [];
  const orden = [...p.actividades].sort((a, b) => a.fecha_limite.localeCompare(b.fecha_limite) || PESO[a.prioridad] - PESO[b.prioridad]);
  for (const a of orden) {
    const dias = p.disponibilidad.map((d) => d.dia).filter((d) => d >= c.hoy && d < a.fecha_limite);
    const resto = repartir(c, a.nombre, Math.round(a.horas_estimadas * 2) * 30, dias, p.duracion_sesion);
    if (resto > 0) faltan.push({ actividad: a.nombre, min: resto });
  }
  return { plan: p, bloques: agrupar(c.bloques), faltan };
}

/** Quita un día del plan y reubica sus sesiones en otros días. */
function bloquearDia(dia: string): { propuesta: Propuesta; cambios: string[] } {
  const p: PlanEntrada = { ...plan!, disponibilidad: plan!.disponibilidad.filter((d) => d.dia !== dia) };
  const quedan = bloques.filter((b) => b.dia !== dia).map((b) => ({ ...b }));
  const c = ctx(p, quedan);
  const cambios: string[] = [];
  const faltan: Propuesta['faltan'] = [];
  for (const b of bloques.filter((x) => x.dia === dia)) {
    const limite = p.actividades.find((a) => a.nombre === b.actividad)!.fecha_limite;
    const dias = p.disponibilidad.map((d) => d.dia).filter((d) => d >= c.hoy && d < limite);
    const antes = c.bloques.length;
    const resto = repartir(c, b.actividad, duracion(b), dias, p.duracion_sesion);
    c.bloques.slice(antes).forEach((n) => cambios.push(`- ${b.actividad}: ${etiqueta(b.dia)} ${b.inicio} pasa a ${etiqueta(n.dia)} ${n.inicio}`));
    if (resto > 0) faltan.push({ actividad: b.actividad, min: resto });
  }
  return { propuesta: { plan: p, bloques: agrupar(c.bloques), faltan }, cambios };
}

function crearDecision(propuesta: Propuesta, cancelable: boolean) {
  const limites = propuesta.faltan.map((f) => propuesta.plan.actividades.find((a) => a.nombre === f.actividad)!.fecha_limite).sort();
  decision = {
    opciones: cancelable ? ['abrir_tiempo', 'recortar', 'cancelar'] : ['abrir_tiempo', 'recortar'],
    faltan_horas: propuesta.faltan.reduce((s, f) => s + f.min, 0) / 60,
    fecha_critica: limites[0],
    propuesta,
    esperaApertura: false,
  };
}

const textoDecision = () =>
  `No me alcanza el tiempo: faltan ${decision!.faltan_horas} h antes del ${etiquetaL(decision!.fecha_critica)}. ¿Cómo lo resolvemos? Puedes abrir más tiempo${decision!.opciones.includes('cancelar') ? ', recortar actividades o cancelar el cambio' : ' o recortar actividades'}.`;

/** Día mencionado en el texto: hoy, mañana o el próximo día de la semana nombrado. */
function diaMencionado(t: string): string | null {
  const hoy = hoyISO();
  if (/\bpasado mañana\b/.test(t)) return sumaDias(hoy, 2);
  if (/\bmañana\b/.test(t)) return sumaDias(hoy, 1);
  if (/\bhoy\b/.test(t)) return hoy;
  const nombres = DIASL.map((d) => d.normalize('NFD').replace(/[\u0300-\u036f]/g, ''));
  const limpio = t.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const i = nombres.findIndex((n) => new RegExp(`\\b${n}\\b`).test(limpio));
  if (i < 0) return null;
  const dif = (i - fecha(hoy).getDay() + 7) % 7 || 7;
  return sumaDias(hoy, dif);
}

const sesionesDe = (dia: string) =>
  bloques.filter((b) => b.dia === dia).sort((a, b) => toMin(a.inicio) - toMin(b.inicio));

// --- Cliente ---------------------------------------------------------------------------------------
export const apiSimulada: ClienteApi = {
  async crearEstudiante() {
    await espera(200);
    return `simulado-${Math.random().toString(16).slice(2, 10)}`;
  },

  async estado() {
    await espera(150);
    return estado();
  },

  async crearPlan(p, reemplazar) {
    limitar();
    await espera(1000);
    if (plan && !reemplazar) return resp('confirmar_reemplazo', AVISO_REEMPLAZO);
    const pr = planificar(p);
    plan = pr.plan;
    bloques = pr.bloques;
    decision = null;
    pendiente = null;
    const top = [...p.actividades].sort((a, b) => PESO[a.prioridad] - PESO[b.prioridad] || a.fecha_limite.localeCompare(b.fecha_limite))[0];
    const avisos = pr.faltan.map((f) => `"${f.actividad}": no cupieron ${f.min / 60} h antes de su entrega.`);
    return resp(
      'aplicada',
      `¡Listo! Armé tu horario con ${bloques.length} sesiones. Le di más peso a "${top.nombre}" porque vence el ${etiquetaL(top.fecha_limite)} y tiene prioridad ${top.prioridad}. ¿Quieres ajustar algo?`,
      avisos,
    );
  },

  async mensaje(texto) {
    if (texto.length > 500) throw new ErrorApi(422, 'El mensaje puede tener como máximo 500 caracteres.');
    limitar();
    await espera();
    if (!plan) return resp('rechazada', 'Primero crea tu plan con el formulario.');
    const t = texto.toLowerCase();

    if (decision) {
      if (decision.esperaApertura) {
        const dia = diaMencionado(t);
        const m = t.match(/(\d{1,2})(?::(\d{2}))?\s*(?:a|-|hasta)\s*(?:las\s*)?(\d{1,2})(?::(\d{2}))?/);
        if (!dia || !m) return resp('pregunta', 'Dime el día y las horas, por ejemplo: el miércoles de 14:00 a 17:00.');
        const ini = toStr(+m[1] * 60 + +(m[2] ?? 0));
        const fin = toStr(+m[3] * 60 + +(m[4] ?? 0));
        const pr = decision.propuesta;
        const disp = pr.plan.disponibilidad.filter((d) => d.dia !== dia);
        const previo = pr.plan.disponibilidad.find((d) => d.dia === dia)?.franjas ?? [];
        disp.push({ dia, franjas: [...previo, { inicio: ini, fin }].sort((a, b) => toMin(a.inicio) - toMin(b.inicio)) });
        const p2: PlanEntrada = { ...pr.plan, disponibilidad: disp.sort((a, b) => a.dia.localeCompare(b.dia)) };
        const c = ctx(p2, pr.bloques.map((b) => ({ ...b })));
        const faltan = pr.faltan
          .map((f) => {
            const limite = p2.actividades.find((a) => a.nombre === f.actividad)!.fecha_limite;
            return { actividad: f.actividad, min: repartir(c, f.actividad, f.min, p2.disponibilidad.map((d) => d.dia).filter((d) => d >= c.hoy && d < limite), p2.duracion_sesion) };
          })
          .filter((f) => f.min > 0);
        if (faltan.length) {
          decision.propuesta = { plan: p2, bloques: agrupar(c.bloques), faltan };
          decision.faltan_horas = faltan.reduce((s, f) => s + f.min, 0) / 60;
          return resp('pregunta', `Con eso aún faltan ${decision.faltan_horas} h. ¿Puedes sumar otro rato?`);
        }
        pendiente = { propuesta: { plan: p2, bloques: agrupar(c.bloques), faltan: [] }, texto: '' };
        return resp('confirmacion', `Sumaría el ${etiquetaL(dia)} de ${ini} a ${fin} y con eso todo cabe antes de las entregas. ¿Aplico el cambio?`);
      }
      if (/abrir|sumar|m[aá]s tiempo/.test(t)) {
        decision.esperaApertura = true;
        return resp('pregunta', '¿Qué día y de qué hora a qué hora puedes sumar? Por ejemplo: el miércoles de 14:00 a 17:00.');
      }
      if (/recort/.test(t)) {
        const pr = decision.propuesta;
        pendiente = { propuesta: { ...pr, faltan: [] }, texto: '' };
        const lineas = pr.faltan.map((f) => `- "${f.actividad}": se recortan ${f.min / 60} h`).join('\n');
        return resp('confirmacion', `Así quedaría el recorte:\n${lineas}\n¿Lo aplico?`);
      }
      if (/cancel/.test(t) && decision.opciones.includes('cancelar')) {
        decision = null;
        return resp('cancelada', 'Listo, cancelé el cambio. Tu horario sigue como estaba.');
      }
      return resp('aclaracion', textoDecision());
    }

    const dia = diaMencionado(t);
    if (dia && /no puedo|enferm|ocupad|bloque|no voy|libre/.test(t)) {
      if (!plan.disponibilidad.some((d) => d.dia === dia) && !sesionesDe(dia).length)
        return resp('rechazada', `El ${etiquetaL(dia)} ya no tienes tiempo de estudio, así que no hay nada que cambiar.`);
      const { propuesta, cambios } = bloquearDia(dia);
      if (propuesta.faltan.length) {
        crearDecision(propuesta, false);
        return resp('decision', textoDecision());
      }
      pendiente = { propuesta, texto: '' };
      return resp(
        'confirmacion',
        cambios.length
          ? `Entendido. Bloquearía el ${etiquetaL(dia)} y reubicaría las sesiones que tenías ese día. Así quedaría:\n${cambios.join('\n')}`
          : `Entendido. Bloquearía el ${etiquetaL(dia)}. Ese día no tenías sesiones, así que nada más cambia.`,
      );
    }

    if (/qu[eé] tengo|mi horario|qu[eé] hago|consulta/.test(t)) {
      const d = dia ?? hoyISO();
      const ss = sesionesDe(d);
      return resp(
        'consulta',
        ss.length
          ? `El ${etiquetaL(d)} tienes:\n${ss.map((b) => `- "${b.actividad}" de ${b.inicio} a ${b.fin}`).join('\n')}`
          : `El ${etiquetaL(d)} no tienes sesiones.`,
      );
    }

    if (/receta|chiste|clima|f[uú]tbol/.test(t))
      return resp('fuera_de_alcance', 'Solo puedo ayudarte con tu horario de estudio. Por ejemplo: mover una sesión, bloquear un día o agregar una actividad.');

    return resp('aclaracion', '¿Me das un poco más de detalle? Por ejemplo: "no puedo el sábado", "mueve el parcial al jueves" o "¿qué tengo mañana?".');
  },

  async confirmar(aceptar) {
    limitar();
    await espera(500);
    if (!pendiente) return resp('sin_pendiente', SIN_PENDIENTE);
    const p = pendiente;
    pendiente = null;
    if (!aceptar) return resp('descartada', 'Listo, no hice ningún cambio.');
    plan = p.propuesta.plan;
    bloques = p.propuesta.bloques;
    decision = null;
    return resp('aplicada', 'Hecho, ya apliqué el cambio a tu horario.');
  },

  async mover(m: MoverCuerpo) {
    limitar();
    await espera(500);
    const b = bloques.find(
      (x) => x.actividad === m.actividad && x.dia === m.dia_origen && (!m.hora_origen || (toMin(m.hora_origen) >= toMin(x.inicio) && toMin(m.hora_origen) < toMin(x.fin))),
    );
    if (!b) return resp('rechazada', 'No encontré esa sesión en tu horario.');
    const c = ctx();
    const d = duracion(b);
    let ini: number | null = null;
    if (m.hora_inicio) {
      const r = razonNoCabe(c, b.actividad, m.dia_destino, toMin(m.hora_inicio), d, [b]);
      if (r) return resp('rechazada', r);
      ini = toMin(m.hora_inicio);
    } else {
      const r = razonDia(c, b, m.dia_destino);
      if (r) return resp('rechazada', r);
      const sin = { ...c, bloques: bloques.filter((x) => x !== b) };
      ini = hueco(sin, b.actividad, [m.dia_destino], d)!.ini;
    }
    const origen = `${etiqueta(b.dia)} ${b.inicio}`;
    Object.assign(b, { dia: m.dia_destino, inicio: toStr(ini), fin: toStr(ini + d), fijado: true });
    bloques = agrupar(bloques);
    return resp('aplicada', `Listo, moví "${b.actividad}" de ${origen} a ${etiqueta(m.dia_destino)} ${toStr(ini)}. Queda fijada.`);
  },

  async intercambiar(la: Lugar, lb: Lugar) {
    limitar();
    await espera(500);
    const buscar = (l: Lugar) => bloques.find((x) => x.dia === l.dia && toMin(l.hora) >= toMin(x.inicio) && toMin(l.hora) < toMin(x.fin));
    const a = buscar(la);
    const b = buscar(lb);
    if (!a || !b) return resp('rechazada', 'No encontré alguna de las dos sesiones.');
    const r = razonIntercambio(ctx(), a, b);
    if (r) return resp('rechazada', r);
    const da = duracion(a), db = duracion(b);
    const na = { dia: b.dia, ini: toMin(b.inicio) }, nb = { dia: a.dia, ini: toMin(a.inicio) };
    Object.assign(a, { dia: na.dia, inicio: toStr(na.ini), fin: toStr(na.ini + da), fijado: true });
    Object.assign(b, { dia: nb.dia, inicio: toStr(nb.ini), fin: toStr(nb.ini + db), fijado: true });
    bloques = agrupar(bloques);
    return resp('aplicada', `Listo, intercambié "${a.actividad}" y "${b.actividad}". Las dos quedan fijadas.`);
  },

  async noHecha(s: Sesion) {
    limitar();
    await espera(500);
    const b = bloques.find((x) => claveBloque(x) === claveBloque(s));
    if (!b) return resp('rechazada', 'No encontré esa sesión en tu horario.');
    const d = duracion(b);
    bloques = bloques.filter((x) => x !== b);
    const c = ctx(plan!, bloques.map((x) => ({ ...x })));
    const limite = plan!.actividades.find((a) => a.nombre === b.actividad)!.fecha_limite;
    const dias = plan!.disponibilidad.map((x) => x.dia).filter((x) => x >= c.hoy && x < limite);
    const resto = repartir(c, b.actividad, d, dias, plan!.duracion_sesion);
    if (resto > 0) {
      crearDecision({ plan: plan!, bloques: agrupar(c.bloques), faltan: [{ actividad: b.actividad, min: resto }] }, false);
      bloques.push(b);
      bloques = agrupar(bloques);
      return resp('decision', textoDecision());
    }
    bloques = agrupar(c.bloques);
    const nueva = c.bloques[c.bloques.length - 1];
    return resp('aplicada', `Listo, repuse la sesión de "${b.actividad}".`, [
      `"${b.actividad}": la sesión no hecha pasó al ${etiquetaL(nueva.dia)} a las ${hm(nueva.inicio)}.`,
    ]);
  },
};
