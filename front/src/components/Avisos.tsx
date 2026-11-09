import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';

// --- Avisos flotantes (toasts) ---------------------------------------------------------------------
export type TipoToast = '' | 'ok' | 'warn' | 'err';
interface Toast {
  id: number;
  texto: string;
  tipo: TipoToast;
}

let siguiente = 0;

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((texto: string, tipo: TipoToast = '') => {
    const id = ++siguiente;
    setToasts((t) => [...t, { id, texto, tipo }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3800);
  }, []);
  return { toasts, toast };
}

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div id="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tipo}`}>
          {t.texto}
        </div>
      ))}
    </div>
  );
}

// --- Ventana de confirmación -----------------------------------------------------------------------
export interface DatosModal {
  titulo: string;
  contenido: ReactNode;
  ok?: string;
  cancelar?: string;
  soloOk?: boolean;
  onOk?: () => void;
  onCancelar?: () => void;
}

export function Modal({ datos, cerrar }: { datos: DatosModal | null; cerrar: () => void }) {
  if (!datos) return null;
  const { titulo, contenido, ok = 'Confirmar', cancelar = 'Cancelar', soloOk, onOk, onCancelar } = datos;
  return (
    <div className="velo">
      <div className="modal" role="dialog" aria-modal="true" aria-label={titulo}>
        <h3>{titulo}</h3>
        {contenido}
        <div className="botones">
          {!soloOk && (
            <button className="btn btn-suave" id="m-no" onClick={() => { cerrar(); onCancelar?.(); }}>
              {cancelar}
            </button>
          )}
          <button className="btn btn-principal" id="m-si" autoFocus onClick={() => { cerrar(); onOk?.(); }}>
            {ok}
          </button>
        </div>
      </div>
    </div>
  );
}
