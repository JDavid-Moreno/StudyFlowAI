import { useEffect, useRef, useState } from 'react';
import type { DecisionVista, Opcion, TipoRespuesta } from '../api/tipos';

export const MAX_CHARS = 500;

export interface MensajeChat {
  id: number;
  rol: 'bot' | 'usuario';
  texto: string;
  tipo?: TipoRespuesta;
  /** Los botones de confirmar o de decidir solo se muestran en el mensaje vigente. */
  activa?: boolean;
}

const OPCIONES: Record<Opcion, [string, string]> = {
  abrir_tiempo: ['Abrir más tiempo', 'Me dices qué horas libres puedes sumar.'],
  recortar: ['Recortar actividades', 'Te muestro el recorte exacto antes de aplicarlo.'],
  cancelar: ['Cancelar el cambio', 'Todo sigue como estaba.'],
};

/** Texto del bot: las líneas que empiezan con "- " se agrupan en la caja de cambios. */
function TextoBot({ texto }: { texto: string }) {
  const partes: { lista: boolean; lineas: string[] }[] = [];
  texto.split('\n').forEach((l) => {
    const lista = l.startsWith('- ');
    const u = partes[partes.length - 1];
    if (u && u.lista === lista) u.lineas.push(lista ? l.slice(2) : l);
    else partes.push({ lista, lineas: [lista ? l.slice(2) : l] });
  });
  return (
    <>
      {partes.map((p, i) =>
        p.lista ? (
          <div className="cambio" key={i}>{p.lineas.map((l, j) => <div key={j}>↪ {l}</div>)}</div>
        ) : (
          <span key={i}>{p.lineas.join('\n')}</span>
        ),
      )}
    </>
  );
}

interface PropsChat {
  abierto: boolean;
  mensajes: MensajeChat[];
  escribiendo: boolean;
  decision: DecisionVista | null;
  onCerrar: () => void;
  onEnviar: (t: string) => void;
  onConfirmar: (aceptar: boolean) => void;
  onOpcion: (o: Opcion) => void;
}

export function Chat({ abierto, mensajes, escribiendo, decision, onCerrar, onEnviar, onConfirmar, onOpcion }: PropsChat) {
  const [texto, setTexto] = useState('');
  const lista = useRef<HTMLDivElement>(null);
  const entrada = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = lista.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [mensajes, escribiendo, abierto]);

  useEffect(() => {
    if (abierto) setTimeout(() => entrada.current?.focus(), 260);
  }, [abierto]);

  const enviar = () => {
    const t = texto.trim();
    if (!t || escribiendo) return;
    setTexto('');
    onEnviar(t);
  };

  return (
    <aside className={`chat ${abierto ? 'abierto' : ''}`} id="chat" aria-label="Asistente StudyFlow" aria-hidden={!abierto}>
      <div className="chat-cab">
        <div className="avatar">AI</div>
        <div>
          <b>Asistente StudyFlow</b>
          <small>Pide cambios en lenguaje natural</small>
        </div>
        <button className="x" onClick={onCerrar} aria-label="Cerrar">✕</button>
      </div>

      <div className="mensajes" id="msgs" ref={lista}>
        {mensajes.map((m) =>
          m.rol === 'usuario' ? (
            <div key={m.id} className="msg user">{m.texto}</div>
          ) : (
            <div key={m.id} className="msg bot">
              <TextoBot texto={m.texto} />
              {m.tipo === 'confirmacion' && m.activa && (
                <>
                  <div className="botones">
                    <button className="btn btn-principal btn-chico" onClick={() => onConfirmar(true)}>Aplicar cambios</button>
                    <button className="btn btn-sec btn-chico" onClick={() => onConfirmar(false)}>Descartar</button>
                  </div>
                  <div className="nota">Puedes confirmar durante 30 minutos.</div>
                </>
              )}
              {m.tipo === 'decision' && m.activa && decision && (
                <div className="opciones">
                  {decision.opciones.map((o) => (
                    <button key={o} onClick={() => onOpcion(o)}>
                      {OPCIONES[o][0]}
                      <small>{OPCIONES[o][1]}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {escribiendo && <div className="msg bot escribiendo"><i /><i /><i /></div>}
      </div>

      <div className="entrada-chat">
        <span className={`contador ${texto.length > 450 ? 'alto' : ''}`} id="cnt">{texto.length}/{MAX_CHARS}</span>
        <input
          id="chat-txt"
          ref={entrada}
          maxLength={MAX_CHARS}
          placeholder="Escribe un cambio, ej. mueve la sesión del martes"
          autoComplete="off"
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') enviar(); }}
        />
        <button onClick={enviar} aria-label="Enviar">➤</button>
      </div>
    </aside>
  );
}

interface PropsBoton {
  visible: boolean;
  pendiente: 'confirmacion' | 'decision' | null;
  mostrarBurbuja: boolean;
  onAbrir: () => void;
  onCerrarBurbuja: () => void;
}

/** Botón flotante del asistente con su comentario. */
export function BotonAsistente({ visible, pendiente, mostrarBurbuja, onAbrir, onCerrarBurbuja }: PropsBoton) {
  if (!visible) return null;
  const texto =
    pendiente === 'decision'
      ? 'Tienes una decisión pendiente. ¿La resolvemos?'
      : pendiente === 'confirmacion'
        ? 'Tienes un cambio esperando tu confirmación.'
        : '¡Ey! ¿Quieres modificar algo?';
  return (
    <>
      {mostrarBurbuja && (
        <div className="burbuja" id="burbuja" onClick={onAbrir}>
          <span>{texto}</span>
          <button onClick={(e) => { e.stopPropagation(); onCerrarBurbuja(); }} aria-label="Cerrar comentario">✕</button>
        </div>
      )}
      <button className="fab" id="fab" onClick={onAbrir} aria-label="Abrir el asistente">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
          <path d="M9 11h.01M12 11h.01M15 11h.01" strokeWidth="2.6" />
        </svg>
        {pendiente && <span className="pt" />}
      </button>
    </>
  );
}
