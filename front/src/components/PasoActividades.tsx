import type { Nivel } from '../api/tipos';
import { type ActividadForm, type Formulario, actividadVacia } from '../utils/formulario';

interface Props {
  form: Formulario;
  setForm: (f: (prev: Formulario) => Formulario) => void;
  hoy: string;
  errores: string[];
  hayPlan: boolean;
  generando: boolean;
  onGenerar: () => void;
}

const NIVELES: Nivel[] = ['alta', 'media', 'baja'];
const BORDE: Record<Nivel, string> = { alta: 'var(--coral)', media: 'var(--azul)', baja: '#9ED6EE' };

function Segmento({ valor, onCambio }: { valor: Nivel; onCambio: (n: Nivel) => void }) {
  return (
    <div className="segmento">
      {NIVELES.map((n) => (
        <button key={n} type="button" className={n === valor ? `sel-${n}` : ''} onClick={() => onCambio(n)}>
          {n[0].toUpperCase() + n.slice(1)}
        </button>
      ))}
    </div>
  );
}

export function PasoActividades({ form, setForm, hoy, errores, hayPlan, generando, onGenerar }: Props) {
  const cambiar = (i: number, cambio: Partial<ActividadForm>) =>
    setForm((f) => ({ ...f, actividades: f.actividades.map((a, j) => (j === i ? { ...a, ...cambio } : a)) }));

  return (
    <section className="bloque" id="s-act">
      <div className="titulo-bloque">
        <div className="num">2</div>
        <div>
          <h2>Tus actividades</h2>
          <p>Agrega cada entrega, parcial o proyecto pendiente.</p>
        </div>
      </div>

      <div id="lista-actividades">
        {form.actividades.map((a, i) => (
          <div className="actividad" key={i} style={{ borderLeftColor: BORDE[a.prioridad] }}>
            <div className="act-cabecera">
              <h3>Actividad {i + 1}</h3>
              {form.actividades.length > 1 && (
                <button className="quitar" onClick={() => setForm((f) => ({ ...f, actividades: f.actividades.filter((_, j) => j !== i) }))}>
                  ✕ Quitar
                </button>
              )}
            </div>
            <div className="grid-act">
              <div>
                <label>Nombre</label>
                <input maxLength={100} placeholder="Ej. Parcial de Cálculo" value={a.nombre} onChange={(e) => cambiar(i, { nombre: e.target.value })} />
              </div>
              <div>
                <label>Prioridad</label>
                <Segmento valor={a.prioridad} onCambio={(n) => cambiar(i, { prioridad: n })} />
              </div>
              <div>
                <label>Dificultad</label>
                <Segmento valor={a.dificultad} onCambio={(n) => cambiar(i, { dificultad: n })} />
              </div>
              <div>
                <label>Fecha límite</label>
                <input type="date" min={form.ini || hoy} value={a.fecha} onChange={(e) => cambiar(i, { fecha: e.target.value })} />
              </div>
              <div>
                <label>Horas estimadas</label>
                <input type="number" step={0.5} min={0.5} max={100} placeholder="0" value={a.horas} onChange={(e) => cambiar(i, { horas: e.target.value })} />
              </div>
            </div>
          </div>
        ))}
      </div>

      <button className="btn btn-agregar" onClick={() => setForm((f) => ({ ...f, actividades: [...f.actividades, actividadVacia()] }))}>
        ＋ Agregar otra actividad
      </button>

      <div id="errores">
        {errores.length > 0 && (
          <div className="errores">
            <b>Revisa esto antes de generar el horario:</b>
            <ul>{errores.map((x) => <li key={x}>{x}</li>)}</ul>
          </div>
        )}
      </div>

      <div className="acciones">
        <span className="nota">{hayPlan ? 'Ya tienes un plan: te pediremos confirmar el reemplazo.' : ''}</span>
        <button className="btn btn-principal btn-gen-cargando" id="btn-gen" onClick={onGenerar} disabled={generando}>
          {generando ? 'Armando tu horario...' : 'Generar mi horario'}
        </button>
      </div>
    </section>
  );
}
