// La API no expone en /estado la orden que espera confirmación (dura 30 minutos), así que el texto
// de la confirmación se guarda en el navegador para poder mostrarla de nuevo si se recarga la página.
const CLAVE = 'studyflow.confirmacion_pendiente';
const VIGENCIA_MS = 30 * 60 * 1000;

export const guardarPendiente = (texto: string) => {
  try {
    localStorage.setItem(CLAVE, JSON.stringify({ texto, ts: Date.now() }));
  } catch {
    /* sin almacenamiento: no se podrá recuperar al recargar */
  }
};

export const borrarPendiente = () => {
  try {
    localStorage.removeItem(CLAVE);
  } catch {
    /* nada que borrar */
  }
};

/** El texto de la confirmación pendiente, si no han pasado más de 30 minutos. */
export const leerPendiente = (): string | null => {
  try {
    const crudo = localStorage.getItem(CLAVE);
    if (!crudo) return null;
    const { texto, ts } = JSON.parse(crudo) as { texto: string; ts: number };
    if (Date.now() - ts > VIGENCIA_MS) {
      borrarPendiente();
      return null;
    }
    return texto;
  } catch {
    return null;
  }
};
