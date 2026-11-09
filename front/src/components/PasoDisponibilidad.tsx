import { useState } from 'react';
import { DIAS, MESES, diasEntre, etiquetaL, fecha, fmtH, sumaDias, toMin, toStr } from '../utils/fechas';
import {
  DURACIONES, type FranjaForm, type Formulario, HORAS_FIN, HORAS_INICIO, MAX_DIAS,
  diasElegidos, errorFranjas, errorRango, minutosFranjas, semanasRango,
} from '../utils/formulario';
import type { DuracionSesion } from '../api/tipos';

interface Props {
  form: Formulario;
  setForm: (f: (prev: Formulario) => Formulario) => void;
  hoy: string;
  onEjemplo: () => void;
  toast: (t: string, tipo?: 'ok' | 'warn') => void;
}

const copiar = (fr: FranjaForm[]) => fr.map((x) => [...x] as FranjaForm);

export function PasoDisponibilidad({ form, setForm, hoy, onEjemplo, toast }: Props) {
  const [semana, setSemana] = useState(0);
  const [editando, setEditando] = useState<string | null>(null);

  const er = errorRango(form, hoy);
  const semanas = semanasRango(form, hoy);
  const sem = Math.max(0, Math.min(semana, semanas.length - 1));
  const elegidos = diasElegidos(form).length;

  const cambiarDisp = (fn: (d: Record<string, FranjaForm[]>) => void) =>
    setForm((f) => {
      const disp: Record<string, FranjaForm[]> = {};
      Object.entries(f.disp).forEach(([k, v]) => (disp[k] = copiar(v)));
      fn(disp);
      return { ...f, disp };
    });

  const setRango = (k: 'ini' | 'fin', v: string) => {
    setSemana(0);
    setEditando(null);
    setForm((f) => {
      const n = { ...f, [k]: v };
      if (!errorRango(n, hoy)) {
        const disp = { ...n.disp };
        Object.keys(disp).forEach((d) => { if (d < n.ini || d > n.fin) delete disp[d]; });
        n.disp = disp;
      }
      return n;
    });
  };

  const tocarDia = (d: string) => {
    if (!form.disp[d]?.length) cambiarDisp((x) => { x[d] = [['18:00', '21:00']]; });
    setEditando(d);
  };

  const quitarDia = (d: string) => {
    cambiarDisp((x) => { delete x[d]; });
    setEditando(null);
  };

  const quitarFranja = (d: string, j: number) => {
    cambiarDisp((x) => {
      x[d].splice(j, 1);
      if (!x[d].length) delete x[d];
    });
    if (form.disp[d]?.length === 1) setEditando(null);
  };

  const agregarFranja = (d: string) =>
    cambiarDisp((x) => {
      const fr = x[d];
      const ini = Math.min(fr.length ? toMin(fr[fr.length - 1][1]) : 17 * 60, 22 * 60);
      fr.push([toStr(ini), toStr(Math.min(ini + 120, 23 * 60 + 30))]);
    });

  const copiarAOtros = (d: string) => {
    const otros = diasElegidos(form).filter(([o]) => o !== d).map(([o]) => o);
    cambiarDisp((x) => otros.forEach((o) => { x[o] = copiar(x[d]); }));
    toast(otros.length ? `Copié estas horas a tus otros ${otros.length} días.` : 'Aún no has elegido otros días.', otros.length ? 'ok' : 'warn');
  };

  const repetirSemana = () => {
    const w = semanas[sem];
    cambiarDisp((x) =>
      semanas.slice(sem + 1).forEach((w2) =>
        w2.forEach((d2, k) => {
          if (!d2) return;
          const o = w[k] ? x[w[k]!] ?? [] : [];
          if (o.length) x[d2] = copiar(o);
          else delete x[d2];
        }),
      ),
    );
    toast('Copié esta semana en las siguientes.', 'ok');
  };

  const rotulo = (w: (string | null)[]) => {
    const a = w.find(Boolean)!, z = [...w].reverse().find(Boolean)!;
    return a === z
      ? `${fecha(a).getDate()} ${MESES[fecha(a).getMonth()]}`
      : `${fecha(a).getDate()} al ${fecha(z).getDate()} ${MESES[fecha(z).getMonth()]}`;
  };

  const info = er ?? `${diasEntre(form.ini, form.fin) + 1} días en ${semanas.length} ${semanas.length === 1 ? 'semana' : 'semanas'} · ${elegidos} con tiempo libre. Máximo ${MAX_DIAS} días.`;
  const enEdicion = editando && form.disp[editando]?.length && editando >= form.ini && editando <= form.fin ? editando : null;

  return (
    <section className="bloque" id="s-disp">
      <div className="titulo-bloque">
        <div className="num">1</div>
        <div>
          <h2>¿Cuándo puedes estudiar?</h2>
          <p>Elige el rango de fechas y toca los días en que tengas tiempo para estudiar. Luego nos dices de qué hora a qué hora puedes. Puedes poner más de una franja por día.</p>
        </div>
        <div className="der">
          <button className="btn btn-suave btn-chico" onClick={() => { setSemana(0); setEditando(null); onEjemplo(); }}>
            Rellenar con un ejemplo
          </button>
        </div>
      </div>

      <div className="tarjeta">
        <div className="rango">
          <div>
            <label htmlFor="f-ini">Día inicial</label>
            <input type="date" id="f-ini" min={hoy} value={form.ini} onChange={(e) => setRango('ini', e.target.value)} />
          </div>
          <div>
            <label htmlFor="f-fin">Día final</label>
            <input type="date" id="f-fin" min={form.ini || hoy} max={form.ini ? sumaDias(form.ini, MAX_DIAS - 1) : undefined} value={form.fin} onChange={(e) => setRango('fin', e.target.value)} />
          </div>
          <small id="rango-info" className={er && form.ini && form.fin ? 'error' : ''}>{info}</small>
        </div>

        {semanas.length > 0 && (
          <div className="semanas">
            {semanas.map((w, i) => (
              <button key={i} className={sem === i ? 'on' : ''} onClick={() => { setSemana(i); setEditando(null); }}>
                Semana {i + 1} ({rotulo(w)})
              </button>
            ))}
            {sem < semanas.length - 1 && <button className="der" onClick={repetirSemana}>Repetir esta semana en las siguientes</button>}
          </div>
        )}

        <div className="dias">
          {!semanas.length ? (
            <div className="sin-rango">{er}</div>
          ) : (
            semanas[sem].map((dia, k) => {
              const nombre = DIAS[(k + 1) % 7];
              if (!dia)
                return (
                  <div key={k} className="dia fuera">
                    <b>{nombre}</b>
                    <span className="mes">Fuera del rango</span>
                  </div>
                );
              const fr = [...(form.disp[dia] ?? [])].sort((a, b) => toMin(a[0]) - toMin(b[0]));
              const dt = fecha(dia);
              return (
                <button
                  key={dia}
                  className={`dia ${fr.length ? 'on' : ''} ${enEdicion === dia ? 'editando' : ''}`}
                  onClick={() => tocarDia(dia)}
                  aria-pressed={fr.length > 0}
                  aria-label={`${etiquetaL(dia)}${fr.length ? ', con tiempo libre' : ', sin tiempo libre'}`}
                >
                  <b>{nombre}</b>
                  <span className="dnum">{dt.getDate()}</span>
                  <span className="mes">{MESES[dt.getMonth()]}</span>
                  {fr.length ? (
                    <>
                      {fr.map(([i, f]) => <span key={i} className="fr">{i}–{f}</span>)}
                      <span className="tot">{fmtH(minutosFranjas(fr))}</span>
                    </>
                  ) : (
                    <span className="vacio">Toca si tienes tiempo</span>
                  )}
                </button>
              );
            })
          )}
        </div>

        {enEdicion && (
          <EditorDia
            dia={enEdicion}
            franjas={form.disp[enEdicion]}
            onHora={(j, k, v) => cambiarDisp((x) => { x[enEdicion][j][k] = v; })}
            onAgregar={() => agregarFranja(enEdicion)}
            onQuitar={(j) => quitarFranja(enEdicion, j)}
            onListo={() => setEditando(null)}
            onCopiar={() => copiarAOtros(enEdicion)}
            onSinTiempo={() => quitarDia(enEdicion)}
          />
        )}

        <div className="fila-extra">
          <div>
            <label htmlFor="maxdia">Máximo de horas de estudio por día (opcional)</label>
            <input type="number" id="maxdia" min={0.5} max={8} step={0.5} placeholder="Sin límite" value={form.maxDia} onChange={(e) => setForm((f) => ({ ...f, maxDia: e.target.value }))} />
            <small>Entre 0,5 y 8 horas.</small>
          </div>
          <div>
            <label htmlFor="dursel">Duración máxima de cada sesión</label>
            <select id="dursel" value={form.duracion} onChange={(e) => setForm((f) => ({ ...f, duracion: +e.target.value as DuracionSesion }))}>
              {DURACIONES.map((m) => (
                <option key={m} value={m}>{m === 90 ? '1 h 30 min (recomendado)' : fmtH(m)}</option>
              ))}
            </select>
            <small>Una sesión nunca pasa de esta duración. El mínimo es 30 min.</small>
          </div>
        </div>
      </div>
    </section>
  );
}

interface PropsEditor {
  dia: string;
  franjas: FranjaForm[];
  onHora: (j: number, k: 0 | 1, v: string) => void;
  onAgregar: () => void;
  onQuitar: (j: number) => void;
  onListo: () => void;
  onCopiar: () => void;
  onSinTiempo: () => void;
}

function EditorDia({ dia, franjas, onHora, onAgregar, onQuitar, onListo, onCopiar, onSinTiempo }: PropsEditor) {
  const er = errorFranjas(franjas);
  return (
    <div className="editor" role="group" aria-label={`Horas libres del ${etiquetaL(dia)}`}>
      <h4>{etiquetaL(dia)}: ¿de qué hora a qué hora puedes estudiar?</h4>
      <p>Si tienes tiempo en dos momentos del día, agrega otra franja.</p>
      {franjas.map(([i, f], j) => (
        <div className="f" key={j}>
          <span>Franja {j + 1}</span>
          <span>de</span>
          <select aria-label={`Hora inicial de la franja ${j + 1}`} value={i} onChange={(e) => onHora(j, 0, e.target.value)}>
            {HORAS_INICIO.map((h) => <option key={h}>{h}</option>)}
          </select>
          <span>a</span>
          <select aria-label={`Hora final de la franja ${j + 1}`} value={f} onChange={(e) => onHora(j, 1, e.target.value)}>
            {HORAS_FIN.map((h) => <option key={h}>{h}</option>)}
          </select>
          <button onClick={() => onQuitar(j)} aria-label={`Quitar la franja ${j + 1}`} title="Quitar franja">✕</button>
        </div>
      ))}
      {er && <p className="err">{er}</p>}
      <div className="acc">
        <button className="btn btn-suave" onClick={onAgregar}>＋ Agregar otra franja</button>
        <button className="btn btn-principal" onClick={onListo} disabled={!!er}>Listo</button>
        <button className="link" onClick={onCopiar}>Usar estas horas en mis otros días</button>
        <button className="link peligro" onClick={onSinTiempo}>Este día no tengo tiempo</button>
      </div>
    </div>
  );
}
