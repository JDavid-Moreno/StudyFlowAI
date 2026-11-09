import { useEffect, useMemo, useState } from 'react';
import type { DragEvent, MouseEvent as ReactMouseEvent } from 'react';
import type { Bloque, Estado } from '../api/tipos';
import { DIAS, MESESL, ahoraMin, diaCorto, etiqueta, etiquetaL, fecha, fmtH, hm, sumaDias, toMin, toStr } from '../utils/fechas';
import {
  COLORES, type Contexto, claveBloque, duracion, indiceColor, opcionesDia, opcionesHora, razonDia, razonIntercambio, semanasPlan, yaEmpezo,
} from '../utils/horario';
import type { DatosModal } from './Avisos';

interface Props {
  estado: Estado;
  hoy: string;
  avisos: string[];
  onQuitarAviso: (i: number) => void;
  onAbrirChat: () => void;
  confirmar: (d: DatosModal) => void;
  toast: (t: string, tipo?: '' | 'ok' | 'warn' | 'err') => void;
  onMover: (b: Bloque, dia: string, horaInicio: string | null) => void;
  onIntercambiar: (a: Bloque, b: Bloque) => void;
  onNoHecha: (b: Bloque) => void;
}

type Zona = { dia: string; ok: boolean; titulo: string; sesion?: string } | null;

export function PasoHorario(p: Props) {
  const { estado, hoy } = p;
  const plan = estado.plan!;
  const lunes = useMemo(() => semanasPlan(estado), [estado]);
  const [sem, setSem] = useState(() => Math.max(0, lunes.findIndex((l) => hoy >= l && hoy <= sumaDias(l, 6))));
  const [pop, setPop] = useState<{ b: Bloque; x: number; y: number } | null>(null);
  const [swap, setSwap] = useState<Bloque | null>(null);
  const [arrastrando, setArrastrando] = useState<Bloque | null>(null);
  const [zona, setZona] = useState<Zona>(null);

  const ctx: Contexto = { plan, bloques: estado.bloques, hoy, ahora: ahoraMin() };
  const semana = Math.min(sem, lunes.length - 1);
  const dias = Array.from({ length: 7 }, (_, i) => sumaDias(lunes[semana], i));
  const todos = [...plan.disponibilidad.map((d) => d.dia), ...plan.actividades.map((a) => a.fecha_limite)].sort();
  const [primero, ultimo] = [todos[0], todos[todos.length - 1]];

  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setPop(null);
      setSwap(null);
    };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, []);

  // --- Indicadores de la semana visible ---
  const bsSemana = estado.bloques.filter((b) => dias.includes(b.dia));
  const total = bsSemana.reduce((s, b) => s + duracion(b), 0);
  const porDia: Record<string, number> = {};
  bsSemana.forEach((b) => (porDia[b.dia] = (porDia[b.dia] ?? 0) + duracion(b)));
  const top = Object.entries(porDia).sort((a, b) => b[1] - a[1])[0];
  const faltantes = (estado.horario?.actividades ?? []).filter((a) => a.horas_faltantes > 0);

  // --- Acciones con confirmación (la API aplica de una vez) ---
  const pedirMover = (b: Bloque, dia: string, ini: number | null) =>
    p.confirmar({
      titulo: '¿Mover esta sesión?',
      contenido: (
        <>
          <div className="cambio">
            <div><b>{b.actividad}</b></div>
            <div>De: {etiquetaL(b.dia)} · {b.inicio} - {b.fin}</div>
            <div>A: {etiquetaL(dia)}{ini != null ? ` · ${toStr(ini)} - ${toStr(ini + duracion(b))}` : ' (el primer hueco libre)'}</div>
          </div>
          <p style={{ marginTop: 10 }}>Quedará fijada: el sistema no la moverá al replanificar.</p>
        </>
      ),
      ok: 'Mover',
      onOk: () => p.onMover(b, dia, ini != null ? toStr(ini) : null),
    });

  const pedirIntercambio = (a: Bloque, b: Bloque) => {
    const r = razonIntercambio(ctx, a, b);
    if (r) return p.toast(r, 'err');
    p.confirmar({
      titulo: '¿Intercambiar estas sesiones?',
      contenido: (
        <>
          <div className="cambio">
            <div><b>{a.actividad}</b>: {etiqueta(a.dia)} {a.inicio} pasa a {etiqueta(b.dia)} {b.inicio}</div>
            <div><b>{b.actividad}</b>: {etiqueta(b.dia)} {b.inicio} pasa a {etiqueta(a.dia)} {a.inicio}</div>
          </div>
          <p style={{ marginTop: 10 }}>Las dos quedarán fijadas.</p>
        </>
      ),
      ok: 'Intercambiar',
      onOk: () => p.onIntercambiar(a, b),
    });
  };

  const pedirNoHecha = (b: Bloque) =>
    p.confirmar({
      titulo: '¿No hiciste esta sesión?',
      contenido: <p>{b.actividad} · {etiquetaL(b.dia)} de {b.inicio} a {b.fin}. Intentaré reponer esas horas en otro hueco antes de la entrega.</p>,
      ok: 'Sí, reponerla',
      onOk: () => p.onNoHecha(b),
    });

  const moverADia = (b: Bloque, dia: string) => {
    if (dia === b.dia) return p.toast('Ya está en ese día. Con un clic en la sesión eliges otra hora.', 'warn');
    const r = razonDia(ctx, b, dia);
    if (r) return p.toast(r, 'err');
    pedirMover(b, dia, null);
  };

  // --- Arrastrar y soltar ---
  const alPasar = (e: DragEvent<HTMLDivElement>, dia: string) => {
    if (!arrastrando) return;
    e.preventDefault();
    const el = (e.target as HTMLElement).closest<HTMLElement>('.sesion');
    const clave = el?.dataset.clave;
    if (clave && clave !== claveBloque(arrastrando)) {
      const b = estado.bloques.find((x) => claveBloque(x) === clave)!;
      const r = razonIntercambio(ctx, arrastrando, b);
      setZona({ dia, sesion: clave, ok: !r, titulo: r ?? 'Soltar para intercambiarlas' });
    } else {
      const r = dia === arrastrando.dia ? 'Ya está en ese día.' : razonDia(ctx, arrastrando, dia);
      setZona({ dia, ok: !r, titulo: r ?? 'Soltar para moverla al primer hueco libre' });
    }
  };
  const alSoltar = (e: DragEvent<HTMLDivElement>, dia: string) => {
    e.preventDefault();
    const a = arrastrando;
    setArrastrando(null);
    setZona(null);
    if (!a) return;
    const clave = (e.target as HTMLElement).closest<HTMLElement>('.sesion')?.dataset.clave;
    const b = clave && clave !== claveBloque(a) ? estado.bloques.find((x) => claveBloque(x) === clave) : undefined;
    if (b) pedirIntercambio(a, b);
    else moverADia(a, dia);
  };

  const clicSesion = (e: ReactMouseEvent<HTMLDivElement>, b: Bloque) => {
    const op = (e.target as HTMLElement).dataset.op;
    if (op === 'no') return pedirNoHecha(b);
    if (swap) {
      const a = swap;
      setSwap(null);
      if (claveBloque(a) !== claveBloque(b)) pedirIntercambio(a, b);
      return;
    }
    const r = e.currentTarget.getBoundingClientRect();
    let x = r.right + 8;
    if (x + 290 > window.innerWidth - 10) x = Math.max(10, r.left - 298);
    setPop({ b, x: Math.max(10, x), y: Math.max(10, Math.min(r.top, window.innerHeight - 330)) });
  };

  const d0 = fecha(dias[0]), d6 = fecha(dias[6]);
  const subtitulo = `Semana del ${d0.getDate()} al ${d6.getDate()} de ${MESESL[d6.getMonth()]}`;

  return (
    <section className="bloque" id="s-hor">
      <div className="titulo-bloque">
        <div className="num">3</div>
        <div>
          <h2>Tu horario</h2>
          <p id="sub-horario">{subtitulo}</p>
        </div>
      </div>

      <div id="avisos">
        {estado.decision && (
          <div className="aviso aviso-alerta">
            <div className="ico">⚠️</div>
            <div className="cuerpo">
              <h4>Tu plan no cabe antes del {etiquetaL(estado.decision.fecha_critica)}</h4>
              <p>Faltan {fmtH(estado.decision.faltan_horas * 60)}. El horario de abajo sigue siendo el vigente hasta que elijas cómo resolverlo.</p>
            </div>
            <button className="btn btn-coral btn-chico" onClick={p.onAbrirChat}>Resolver con el asistente</button>
          </div>
        )}
        {p.avisos.map((a, i) => (
          <div className="aviso aviso-ok" key={`${i}-${a}`}>
            <div className="ico">ℹ️</div>
            <div className="cuerpo"><p style={{ color: 'var(--texto)' }}>{a}</p></div>
            <button className="btn btn-suave btn-chico" onClick={() => p.onQuitarAviso(i)}>Entendido</button>
          </div>
        ))}
      </div>

      <div className="resultado">
        <div className="resumen" id="kpis">
          <div className="kpi azul"><small>Horas planificadas</small><b>{fmtH(total)}</b></div>
          <div className="kpi"><small>Sesiones</small><b>{bsSemana.length}</b></div>
          <div className="kpi"><small>Día más cargado</small><b>{top ? `${diaCorto(top[0])} · ${fmtH(top[1])}` : '-'}</b></div>
          <div className="kpi"><small>No asignadas</small><b style={{ color: faltantes.length ? 'var(--coral)' : 'var(--texto)' }}>{faltantes.length}</b></div>
        </div>

        <div className="tarjeta">
          <div className="semana-cab">
            <div className="nav-sem">
              <button onClick={() => { setSem(Math.max(0, semana - 1)); setPop(null); }} disabled={semana === 0} aria-label="Semana anterior">‹</button>
              <h3 id="cal-titulo">Semana</h3>
              <button onClick={() => { setSem(Math.min(lunes.length - 1, semana + 1)); setPop(null); }} disabled={semana === lunes.length - 1} aria-label="Semana siguiente">›</button>
            </div>
            <div className="leyenda" id="leyenda">
              {plan.actividades.map((a, i) => (
                <span key={a.nombre}>
                  <i className="punto" style={{ background: COLORES[i % 5], border: '1px solid rgba(30,34,64,.2)' }} />
                  {a.nombre}
                </span>
              ))}
            </div>
          </div>
          <div className="consejo" id="consejo">
            {swap
              ? 'Elige la sesión con la que quieres intercambiar. Esc para cancelar.'
              : 'Arrastra una sesión a otro día para moverla, o encima de otra para intercambiarlas. Con un clic eliges la hora. Las sesiones con borde punteado están fijadas (las moviste tú).'}
          </div>

          <div className="calendario" id="cal">
            {dias.map((d) => {
              const bs = estado.bloques.filter((b) => b.dia === d).sort((a, b) => toMin(a.inicio) - toMin(b.inicio));
              const entregas = plan.actividades.filter((a) => a.fecha_limite === d);
              const enPlan = d >= primero && d <= ultimo;
              const sinTiempo = enPlan && !plan.disponibilidad.some((x) => x.dia === d) && !bs.length;
              const z = zona && zona.dia === d && !zona.sesion ? (zona.ok ? 'dz-ok' : 'dz-mal') : '';
              return (
                <div
                  key={d}
                  className={`col-dia ${d === hoy ? 'hoy' : ''} ${enPlan ? '' : 'fuera'} ${z}`}
                  data-dia={d}
                  title={zona && zona.dia === d ? zona.titulo : undefined}
                  onDragOver={(e) => alPasar(e, d)}
                  onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setZona(null); }}
                  onDrop={(e) => alSoltar(e, d)}
                >
                  <div className="cab">{DIAS[fecha(d).getDay()].toUpperCase()}<b>{fecha(d).getDate()}</b></div>
                  {sinTiempo && <div className="sin-tiempo">Sin tiempo libre</div>}
                  {entregas.map((a) => <div className="entrega" key={a.nombre}>📌 Entrega {a.nombre}</div>)}
                  {bs.map((b) => {
                    const k = claveBloque(b);
                    const zs = zona?.sesion === k ? (zona.ok ? 'sw-ok' : 'sw-mal') : '';
                    return (
                      <div
                        key={k}
                        data-clave={k}
                        className={`sesion s-${indiceColor(plan, b.actividad)} ${b.fijado ? 'fijada' : ''} ${swap && claveBloque(swap) === k ? 'swapsel' : ''} ${arrastrando && claveBloque(arrastrando) === k ? 'arrastrando' : ''} ${zs}`}
                        draggable
                        tabIndex={0}
                        role="button"
                        title={b.actividad}
                        aria-label={`${b.actividad} ${hm(b.inicio)} a ${hm(b.fin)}`}
                        onDragStart={(e) => { setPop(null); setArrastrando(b); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', k); }}
                        onDragEnd={() => { setArrastrando(null); setZona(null); }}
                        onClick={(e) => clicSesion(e, b)}
                        onKeyDown={(e) => { if (e.key === 'Enter') clicSesion(e as unknown as ReactMouseEvent<HTMLDivElement>, b); }}
                      >
                        <b>{b.actividad}</b>
                        <span>{hm(b.inicio)}–{hm(b.fin)}</span>
                        <div className="marcar">
                          <i data-op="mas" title="Más opciones: mover a otra hora, intercambiar">⋯</i>
                          {yaEmpezo(b, hoy, ctx.ahora) && <i data-op="no" title="No la hice">✕ No</i>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          <div className="no-asignadas">
            <h4>Sin asignar</h4>
            <div id="sin-asignar">
              {faltantes.length ? (
                faltantes.map((a) => <span className="chip" key={a.nombre}>{a.nombre}: faltan {fmtH(a.horas_faltantes * 60)}</span>)
              ) : (
                <p>Todo está asignado.</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {pop && (
        <MenuSesion
          ctx={ctx}
          b={pop.b}
          x={pop.x}
          y={pop.y}
          cerrar={() => setPop(null)}
          onMover={(dia, ini) => { setPop(null); pedirMover(pop.b, dia, ini); }}
          onIntercambiar={() => { setSwap(pop.b); setPop(null); p.toast('Ahora elige la otra sesión.'); }}
          onNoHecha={() => { setPop(null); pedirNoHecha(pop.b); }}
        />
      )}
    </section>
  );
}

interface PropsMenu {
  ctx: Contexto;
  b: Bloque;
  x: number;
  y: number;
  cerrar: () => void;
  onMover: (dia: string, ini: number) => void;
  onIntercambiar: () => void;
  onNoHecha: () => void;
}

function MenuSesion({ ctx, b, x, y, cerrar, onMover, onIntercambiar, onNoHecha }: PropsMenu) {
  const [moviendo, setMoviendo] = useState(false);
  const dias = useMemo(() => opcionesDia(ctx, b), [ctx, b]);
  const [dia, setDia] = useState(dias[0] ?? '');
  const horas = useMemo(() => (dia ? opcionesHora(ctx, b, dia) : []), [ctx, b, dia]);
  const [hora, setHora] = useState<number | null>(null);
  const h = hora != null && horas.includes(hora) ? hora : horas[0];

  return (
    <>
      <div className="pop-velo" onClick={cerrar} />
      <div className="pop" id="pop" style={{ left: x, top: y }} role="dialog" aria-label={`Opciones de ${b.actividad}`}>
        <h4>
          <i className="punto" style={{ background: COLORES[indiceColor(ctx.plan, b.actividad)], border: '1px solid rgba(30,34,64,.25)' }} />
          {b.actividad}
        </h4>
        <p>{etiquetaL(b.dia)} · {hm(b.inicio)} - {hm(b.fin)} ({fmtH(duracion(b))})</p>
        {b.fijado && <div className="fijnota">📌 Fijada: la moviste tú, así que el sistema no la cambia al replanificar.</div>}
        <div className="acc">
          <button onClick={() => setMoviendo(true)}>Mover a otro día u hora</button>
          {moviendo &&
            (dias.length ? (
              <div className="mv">
                <select aria-label="Día" value={dia} onChange={(e) => { setDia(e.target.value); setHora(null); }}>
                  {dias.map((d) => <option key={d} value={d}>{etiqueta(d)}</option>)}
                </select>
                <select aria-label="Hora" value={h} onChange={(e) => setHora(+e.target.value)}>
                  {horas.map((t) => <option key={t} value={t}>{toStr(t)}</option>)}
                </select>
                <button className="btn btn-principal btn-chico" onClick={() => onMover(dia, h)}>Mover</button>
              </div>
            ) : (
              <p style={{ padding: '4px 2px' }}>No hay ningún hueco libre antes de la entrega.</p>
            ))}
          <button onClick={onIntercambiar}>Intercambiar con otra sesión</button>
          {yaEmpezo(b, ctx.hoy, ctx.ahora) && <button onClick={onNoHecha}>No la hice</button>}
        </div>
      </div>
    </>
  );
}
