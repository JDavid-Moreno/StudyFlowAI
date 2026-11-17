export const DIAS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
export const DIASL = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
export const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
export const MESESL = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];

const dos = (n: number) => String(n).padStart(2, '0');

export const iso = (d: Date) => `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`;
export const fecha = (s: string) => new Date(`${s}T00:00`);
export const hoyISO = () => iso(new Date());
export const ahoraMin = () => {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
};

export const sumaDias = (s: string, n: number) => {
  const d = fecha(s);
  d.setDate(d.getDate() + n);
  return iso(d);
};
export const diasEntre = (a: string, b: string) => Math.round((fecha(b).getTime() - fecha(a).getTime()) / 864e5);
/** Lunes de la semana de la fecha dada. */
export const lunesDe = (s: string) => sumaDias(s, -((fecha(s).getDay() + 6) % 7));

export const diaCorto = (s: string) => DIAS[fecha(s).getDay()];
export const etiqueta = (s: string) => {
  const d = fecha(s);
  return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]}`;
};
export const etiquetaL = (s: string) => {
  const d = fecha(s);
  return `${DIASL[d.getDay()]} ${d.getDate()} de ${MESESL[d.getMonth()]}`;
};

/** "18:30" o "18:30:00" -> minutos del día. */
export const toMin = (t: string) => {
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
};
/** Minutos del día -> "HH:MM". */
export const toStr = (m: number) => `${dos(Math.floor(m / 60))}:${dos(m % 60)}`;
/** Recorta los segundos que trae la API. */
export const hm = (t: string) => t.slice(0, 5);

export const fmtH = (m: number) => {
  const h = Math.floor(m / 60);
  const r = Math.round(m % 60);
  return h && r ? `${h} h ${r} min` : h ? `${h} h` : `${r} min`;
};
