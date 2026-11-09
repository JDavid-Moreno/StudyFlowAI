import { useCallback, useEffect, useRef, useState } from 'react';
import logo from './assets/logo.png';
import { api, ErrorApi } from './api';
import type { Bloque, Estado, Opcion, Respuesta } from './api';
import { hoyISO, hm } from './utils/fechas';
import { type Formulario, aPlanEntrada, formularioEjemplo, formularioVacio, validarFormulario } from './utils/formulario';
import { type DatosModal, Modal, Toasts, useToasts } from './components/Avisos';
import { PasoDisponibilidad } from './components/PasoDisponibilidad';
import { PasoActividades } from './components/PasoActividades';
import { PasoHorario } from './components/PasoHorario';
import { BotonAsistente, Chat, type MensajeChat } from './components/Asistente';

const ir = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
const TEXTO_OPCION: Record<Opcion, string> = {
  abrir_tiempo: 'Quiero abrir más tiempo',
  recortar: 'Prefiero recortar actividades',
  cancelar: 'Cancela el cambio',
};

let idMensaje = 0;

export default function App() {
  const hoy = hoyISO();
  const [form, setForm] = useState<Formulario>(() => formularioVacio(hoy));
  const [errores, setErrores] = useState<string[]>([]);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [generando, setGenerando] = useState(false);
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

  // Al iniciar: identificador y estado guardado (en el cliente simulado arranca vacío).
  useEffect(() => {
    api.estado().then(setEstado).catch(() => setEstado(null));
  }, []);

  useEffect(() => {
    document.body.classList.toggle('chat-abierto', chatAbierto);
  }, [chatAbierto]);

  const pendiente: 'confirmacion' | 'decision' | null = estado?.decision
    ? 'decision'
    : mensajes.some((m) => m.tipo === 'confirmacion' && m.activa)
      ? 'confirmacion'
      : null;

  const agregarBot = useCallback((r: Respuesta) => {
    setMensajes((ms) => {
      const sinBotones = ms.map((m) => (m.activa ? { ...m, activa: false } : m));
      return [...sinBotones, { id: ++idMensaje, rol: 'bot', texto: r.mensaje, tipo: r.tipo, activa: r.tipo === 'confirmacion' || r.tipo === 'decision' }];
    });
    if (r.tipo === 'confirmacion' || r.tipo === 'decision') setBurbuja(true);
  }, []);

  const manejarError = useCallback(
    (e: unknown) => {
      if (e instanceof ErrorApi && e.status === 429) return toast(e.message, 'err');
      if (e instanceof ErrorApi && e.status === 404)
        return setModal({
          titulo: 'No encontramos tu identificador',
          contenido: <p>Este navegador guardaba un identificador que ya no existe. Podemos crearte uno nuevo, pero el horario anterior no se puede recuperar.</p>,
          ok: 'Crear uno nuevo',
          soloOk: true,
          onOk: () => {
            setEstado(null);
            setMensajes([]);
            setAvisos([]);
            setChatAbierto(false);
          },
        });
      if (e instanceof ErrorApi && e.status === 422) return toast(e.message, 'err');
      toast('No pudimos conectar con el servidor. Intenta de nuevo en unos segundos.', 'err');
    },
    [toast],
  );

  /** Aplica una respuesta de la API: estado, avisos y, si corresponde, el mensaje en el chat. */
  const procesar = useCallback(
    (r: Respuesta, origen: 'chat' | 'calendario') => {
      setEstado(r.estado);
      if (r.avisos.length) setAvisos((a) => [...a, ...r.avisos]);
      if (origen === 'chat') return agregarBot(r);
      if (r.tipo === 'aplicada') toast(r.mensaje, 'ok');
      else if (r.tipo === 'decision') {
        agregarBot(r);
        setChatAbierto(true);
      } else toast(r.mensaje, r.tipo === 'rechazada' ? 'err' : '');
    },
    [agregarBot, toast],
  );

  /** Evita mandar dos cambios a la vez (la API también tiene un candado por estudiante). */
  const unaVez = async (fn: () => Promise<void>) => {
    if (ocupado.current) return;
    ocupado.current = true;
    try {
      await fn();
    } catch (e) {
      manejarError(e);
    } finally {
      ocupado.current = false;
    }
  };

  // --- Crear el plan ---
  const crear = (reemplazar: boolean) =>
    unaVez(async () => {
      setGenerando(true);
      try {
        const r = await api.crearPlan(aPlanEntrada(form), reemplazar);
        if (r.tipo === 'confirmar_reemplazo') {
          setModal({ titulo: 'Ya tienes un plan', contenido: <p>{r.mensaje}</p>, ok: 'Sí, reemplazar', cancelar: 'Volver', onOk: () => crear(true) });
          return;
        }
        setEstado(r.estado);
        setAvisos(r.avisos);
        setVersionPlan((v) => v + 1);
        setMensajes([{ id: ++idMensaje, rol: 'bot', texto: r.mensaje, tipo: r.tipo }]);
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

  // --- Chat ---
  const enviar = (texto: string) =>
    unaVez(async () => {
      setMensajes((ms) => [...ms, { id: ++idMensaje, rol: 'usuario', texto }]);
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
    unaVez(async () => procesar(await api.mover({ actividad: b.actividad, dia_origen: b.dia, dia_destino: dia, hora_inicio: horaInicio, hora_origen: hm(b.inicio) }), 'calendario'));

  const intercambiar = (a: Bloque, b: Bloque) =>
    unaVez(async () => procesar(await api.intercambiar({ dia: a.dia, hora: hm(a.inicio) }, { dia: b.dia, hora: hm(b.inicio) }), 'calendario'));

  const noHecha = (b: Bloque) =>
    unaVez(async () => procesar(await api.noHecha({ actividad: b.actividad, dia: b.dia, inicio: b.inicio, fin: b.fin }), 'calendario'));

  return (
    <>
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
        abierto={chatAbierto && hayPlan}
        mensajes={mensajes}
        escribiendo={escribiendo}
        decision={estado?.decision ?? null}
        onCerrar={() => setChatAbierto(false)}
        onEnviar={enviar}
        onConfirmar={confirmar}
        onOpcion={(o) => enviar(TEXTO_OPCION[o])}
      />
      <BotonAsistente
        visible={hayPlan}
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
