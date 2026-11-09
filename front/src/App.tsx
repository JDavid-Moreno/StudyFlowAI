import { useCallback, useEffect, useRef, useState } from 'react';
import logo from './assets/logo.png';
import { api, ErrorApi } from './api';
import type { Bloque, DecisionVista, Estado, Opcion, Respuesta } from './api';
import { ahoraMin, etiquetaL, fmtH, hm, hoyISO, toMin } from './utils/fechas';
import { type Formulario, aPlanEntrada, formularioEjemplo, formularioVacio, planAFormulario, validarFormulario } from './utils/formulario';
import { yaEmpezo } from './utils/horario';
import { borrarPendiente, guardarPendiente, leerPendiente } from './utils/pendiente';
import { type DatosModal, Modal, Toasts, useToasts } from './components/Avisos';
import { PasoDisponibilidad } from './components/PasoDisponibilidad';
import { PasoActividades } from './components/PasoActividades';
import { PasoHorario } from './components/PasoHorario';
import { BotonAsistente, Chat, type MensajeChat } from './components/Asistente';

/** Si es true, el horario contado por el redactor (texto_horario) se muestra en el chat al consultar, crear el plan o aplicar un cambio del chat. */
const MOSTRAR_TEXTO_HORARIO = true;

const ir = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
const TEXTO_OPCION: Record<Opcion, string> = {
  abrir_tiempo: 'Quiero abrir más tiempo',
  recortar: 'Prefiero recortar actividades',
  cancelar: 'Cancela el cambio',
};

let idMensaje = 0;
const bot = (texto: string, extra: Partial<MensajeChat> = {}): MensajeChat => ({ id: ++idMensaje, rol: 'bot', texto, ...extra });

/** Mensaje de una decisión que se recupera al recargar (la API solo guarda las opciones, no el texto). */
const textoDecision = (d: DecisionVista) =>
  `No me alcanza el tiempo: faltan ${fmtH(d.faltan_horas * 60)} antes del ${etiquetaL(d.fecha_critica)}. ¿Cómo lo resolvemos?`;

export default function App() {
  const hoy = hoyISO();
  const [form, setForm] = useState<Formulario>(() => formularioVacio(hoy));
  const [errores, setErrores] = useState<string[]>([]);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [generando, setGenerando] = useState(false);
  const [trabajando, setTrabajando] = useState(false);
  const [versionPlan, setVersionPlan] = useState(0);
  const [avisos, setAvisos] = useState<string[]>([]);
  const [mensajes, setMensajes] = useState<MensajeChat[]>([]);
  const [escribiendo, setEscribiendo] = useState(false);
  const [chatAbierto, setChatAbierto] = useState(false);
  const [burbuja, setBurbuja] = useState(true);
  const [modal, setModal] = useState<DatosModal | null>(null);
  const { toasts, toast } = useToasts();
  const ocupado = useRef(false);

  const hayPlan = !!estado?.plan;
  /** El asistente también existe cuando el primer plan no cupo: no hay horario, pero sí una decisión pendiente. */
  const hayAsistente = hayPlan || !!estado?.decision;

  const reiniciarTodo = useCallback(() => {
    setEstado(null);
    setMensajes([]);
    setAvisos([]);
    setChatAbierto(false);
    borrarPendiente();
  }, []);

  const manejarError = useCallback(
    (e: unknown) => {
      if (e instanceof ErrorApi && e.status === 404)
        return setModal({
          titulo: 'No encontramos tu sesión',
          contenido: <p>El identificador que guardaba este navegador ya no existe en el servidor, así que el horario anterior no se puede recuperar. Empezaremos de nuevo con un identificador nuevo.</p>,
          ok: 'Entendido',
          soloOk: true,
          onOk: reiniciarTodo,
        });
      if (e instanceof ErrorApi) return toast(e.message, 'err');
      toast('Ocurrió un error inesperado. Intenta de nuevo.', 'err');
    },
    [toast, reiniciarTodo],
  );

  // Al abrir la página: estado guardado, decisión pendiente y confirmación pendiente.
  useEffect(() => {
    let vivo = true;
    api
      .estado()
      .then((e) => {
        if (!vivo) return;
        setEstado(e);
        if (e.plan && e.plan.actividades.every((a) => a.fecha_limite > hoy)) setForm(planAFormulario(e.plan, hoy));
        const inicial: MensajeChat[] = [];
        if (e.decision) inicial.push(bot(textoDecision(e.decision), { tipo: 'decision', activa: true }));
        else {
          const p = leerPendiente();
          if (p && e.plan) inicial.push(bot(p, { tipo: 'confirmacion', activa: true }));
        }
        if (inicial.length) setMensajes(inicial);
      })
      .catch((e) => {
        if (vivo && !(e instanceof ErrorApi && e.status === 404)) manejarError(e); // un 404 al abrir solo significa empezar de cero
      });
    return () => {
      vivo = false;
    };
  }, [manejarError, hoy]);

  useEffect(() => {
    document.body.classList.toggle('chat-abierto', chatAbierto && hayAsistente);
  }, [chatAbierto, hayAsistente]);

  const pendiente: 'confirmacion' | 'decision' | null = estado?.decision
    ? 'decision'
    : mensajes.some((m) => m.tipo === 'confirmacion' && m.activa)
      ? 'confirmacion'
      : null;

  /** Agrega al chat la respuesta de la API (y, si se pide, el horario contado por el redactor). */
  const agregarBot = useCallback((r: Respuesta, conTexto = true) => {
    if (r.tipo === 'confirmacion') guardarPendiente(r.mensaje);
    else borrarPendiente();
    setMensajes((ms) => {
      const nuevos: MensajeChat[] = [];
      if (r.mensaje) nuevos.push(bot(r.mensaje, { tipo: r.tipo, activa: r.tipo === 'confirmacion' || r.tipo === 'decision' }));
      if (conTexto && MOSTRAR_TEXTO_HORARIO && r.texto_horario) nuevos.push(bot(r.texto_horario));
      return [...ms.map((m) => (m.activa ? { ...m, activa: false } : m)), ...nuevos];
    });
    if (r.tipo === 'confirmacion' || r.tipo === 'decision') setBurbuja(true);
  }, []);

  /** Aplica una respuesta de la API: estado, avisos y, si corresponde, el mensaje en el chat. */
  const procesar = useCallback(
    (r: Respuesta, origen: 'chat' | 'calendario') => {
      setEstado(r.estado);
      if (r.avisos.length) setAvisos((a) => [...a, ...r.avisos]);
      if (origen === 'chat') return agregarBot(r);
      if (r.tipo === 'aplicada') toast(r.mensaje, 'ok');
      else if (r.tipo === 'decision') {
        agregarBot(r, false);
        setChatAbierto(true);
      } else toast(r.mensaje, r.tipo === 'rechazada' ? 'err' : '');
    },
    [agregarBot, toast],
  );

  /** Evita mandar dos cambios a la vez (la API también tiene un candado por estudiante). */
  const unaVez = async (fn: () => Promise<void>) => {
    if (ocupado.current) return toast('Espera un momento: todavía estoy aplicando el cambio anterior.', 'warn');
    ocupado.current = true;
    setTrabajando(true);
    try {
      await fn();
    } catch (e) {
      manejarError(e);
    } finally {
      ocupado.current = false;
      setTrabajando(false);
    }
  };

  // --- Crear el plan (POST /plan) ---
  const crear = (reemplazar: boolean) =>
    unaVez(async () => {
      setGenerando(true);
      try {
        const r = await api.crearPlan(aPlanEntrada(form), reemplazar);
        if (r.tipo === 'confirmar_reemplazo') {
          setModal({ titulo: 'Ya tienes un plan', contenido: <p>{r.mensaje}</p>, ok: 'Sí, reemplazar', cancelar: 'Volver', onOk: () => crear(true) });
          return;
        }
        if (r.tipo === 'rechazada') {
          setErrores([r.mensaje]);
          setTimeout(() => document.getElementById('errores')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 30);
          return;
        }
        setErrores([]);
        setEstado(r.estado);
        setAvisos(r.avisos);
        setVersionPlan((v) => v + 1);
        borrarPendiente();
        if (r.tipo === 'decision') {
          // El primer plan no cupo: no hay horario todavía, solo la decisión.
          setMensajes([bot(r.mensaje, { tipo: 'decision', activa: true })]);
          setChatAbierto(true);
          return;
        }
        setMensajes([bot(r.mensaje, { tipo: r.tipo }), ...(MOSTRAR_TEXTO_HORARIO && r.texto_horario ? [bot(r.texto_horario)] : [])]);
        setBurbuja(true);
        toast(reemplazar ? 'Plan reemplazado. Este es tu nuevo horario.' : 'Listo, este es tu horario.', 'ok');
        setTimeout(() => ir('s-hor'), 60);
      } finally {
        setGenerando(false);
      }
    });

  const generar = () => {
    const e = validarFormulario(form, hoy);
    setErrores(e);
    if (e.length) {
      setTimeout(() => document.getElementById('errores')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 30);
      return;
    }
    crear(false);
  };

  // --- Chat (POST /mensaje y POST /confirmacion) ---
  const enviar = (texto: string) =>
    unaVez(async () => {
      setMensajes((ms) => [...ms.map((m) => (m.activa ? { ...m, activa: false } : m)), { id: ++idMensaje, rol: 'usuario', texto }]);
      setEscribiendo(true);
      try {
        procesar(await api.mensaje(texto), 'chat');
      } finally {
        setEscribiendo(false);
      }
    });

  const confirmar = (aceptar: boolean) =>
    unaVez(async () => {
      setMensajes((ms) => ms.map((m) => (m.activa ? { ...m, activa: false } : m)));
      borrarPendiente();
      setEscribiendo(true);
      try {
        const r = await api.confirmar(aceptar);
        procesar(r, 'chat');
        if (r.tipo === 'aplicada') toast('Cambio aplicado.', 'ok');
      } finally {
        setEscribiendo(false);
      }
    });

  // --- Calendario (la API aplica de una vez: el calendario ya pidió confirmación) ---
  const mover = (b: Bloque, dia: string, horaInicio: string | null) =>
    unaVez(async () =>
      procesar(await api.mover({ actividad: b.actividad, dia_origen: b.dia, dia_destino: dia, hora_inicio: horaInicio, hora_origen: hm(b.inicio) }), 'calendario'),
    );

  const intercambiar = (a: Bloque, b: Bloque) =>
    unaVez(async () => procesar(await api.intercambiar({ dia: a.dia, hora: hm(a.inicio) }, { dia: b.dia, hora: hm(b.inicio) }), 'calendario'));

  /** La API marca una sesión a la vez y solo si ya empezó: un bloque puede tener varias, se marcan en orden. */
  const noHecha = (b: Bloque) =>
    unaVez(async () => {
      const sesiones = (estado?.horario?.sesiones ?? []).filter(
        (s) => s.actividad === b.actividad && s.dia === b.dia && toMin(s.inicio) >= toMin(b.inicio) && toMin(s.fin) <= toMin(b.fin) && yaEmpezo(s, hoy, ahoraMin()),
      );
      if (!sesiones.length) return toast('Solo puedes marcar como no hecha una sesión que ya empezó.', 'warn');
      for (const s of sesiones) {
        const r = await api.noHecha(s);
        procesar(r, 'calendario');
        if (r.tipo !== 'aplicada') break;
      }
    });

  return (
    <>
      <div className={`progreso ${trabajando ? 'on' : ''}`} aria-hidden="true" />
      <header>
        <div className="contenedor nav">
          <div className="logo">
            <img src={logo} alt="Logo de StudyFlow AI" />
            StudyFlow <span>AI</span>
          </div>
          <div className="pasos">
            <div className="activo" onClick={() => ir('s-disp')}>1. Disponibilidad</div>
            <div className="activo" onClick={() => ir('s-act')}>2. Actividades</div>
            <div className={hayPlan ? 'activo' : ''} id="paso3" onClick={() => hayPlan && ir('s-hor')}>3. Horario</div>
          </div>
        </div>
      </header>

      <div className="hero">
        <div className="contenedor">
          <h1>Organiza tu semana de estudio <em>en minutos</em></h1>
          <p>Cuéntanos cuándo puedes estudiar y qué tienes pendiente. StudyFlow prioriza tus actividades y arma un horario que respeta tus fechas límite.</p>
        </div>
      </div>

      <main className="contenedor">
        <PasoDisponibilidad
          form={form}
          setForm={setForm}
          hoy={hoy}
          toast={toast}
          onEjemplo={() => {
            setForm(formularioEjemplo(hoy));
            setErrores([]);
          }}
        />
        <PasoActividades form={form} setForm={setForm} hoy={hoy} errores={errores} hayPlan={hayPlan} generando={generando} onGenerar={generar} />
        {estado?.plan && (
          <PasoHorario
            key={versionPlan}
            estado={estado}
            hoy={hoy}
            avisos={avisos}
            onQuitarAviso={(i) => setAvisos((a) => a.filter((_, j) => j !== i))}
            onAbrirChat={() => setChatAbierto(true)}
            confirmar={setModal}
            toast={toast}
            onMover={mover}
            onIntercambiar={intercambiar}
            onNoHecha={noHecha}
          />
        )}
      </main>

      <Chat
        abierto={chatAbierto && hayAsistente}
        mensajes={mensajes}
        escribiendo={escribiendo}
        decision={estado?.decision ?? null}
        onCerrar={() => setChatAbierto(false)}
        onEnviar={enviar}
        onConfirmar={confirmar}
        onOpcion={(o) => enviar(TEXTO_OPCION[o])}
      />
      <BotonAsistente
        visible={hayAsistente}
        pendiente={pendiente}
        mostrarBurbuja={burbuja && !chatAbierto}
        onAbrir={() => {
          setChatAbierto(true);
          setBurbuja(false);
        }}
        onCerrarBurbuja={() => setBurbuja(false)}
      />

      <Toasts toasts={toasts} />
      <Modal datos={modal} cerrar={() => setModal(null)} />

      <footer>StudyFlow AI · PTIA 2026-2</footer>
    </>
  );
}
